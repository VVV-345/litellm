"""调用固定镜像的无网络自检及会话专属通道，模型验证只请求当前卡片的 CPA。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Final, Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter

from account_pool.compose_runtime import ComposeRuntime
from account_pool.domain import GatewayEnvironment
from account_pool.email_rebinding.models import RebindingWorkerView
from account_pool.oauth_automation.models import BrowserOS, WorkerView
from account_pool.oauth_browser import oauth_browser_control_host


class ImageCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    healthy: bool
    sdk: str
    browser: str
    elapsed_ms: int


class ModelAcceptance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    chat: bool
    responses: bool
    failure_detail: str | None = None


def _objects(value: JsonValue | tuple[JsonValue, ...]) -> tuple[dict[str, JsonValue], ...]:
    try:
        return TypeAdapter(tuple[dict[str, JsonValue], ...]).validate_python(value)
    except ValueError:
        return ()


def complete_text(payload: dict[str, JsonValue], mode: Literal["chat", "responses"]) -> bool:
    if payload.get("error") is not None:
        return False
    if mode == "chat":
        return any(
            choice.get("finish_reason") == "stop"
            and any(
                isinstance(message.get("content"), str) and bool(str(message["content"]).strip())
                for message in _objects([choice.get("message")])
            )
            for choice in _objects(payload.get("choices"))
        )
    return payload.get("status") == "completed" and any(
        item.get("type") == "message"
        and any(
            part.get("type") == "output_text" and isinstance(part.get("text"), str) and bool(str(part["text"]).strip())
            for part in _objects(item.get("content"))
        )
        for item in _objects(payload.get("output"))
    )


@dataclass(frozen=True, slots=True)
class AutomationWorker:
    runtime: ComposeRuntime
    http: httpx.AsyncClient

    @property
    def image(self) -> str | None:
        return self.runtime.settings.oauth_browser_image

    async def generate(self, locale: str, os: BrowserOS = "linux") -> dict[str, JsonValue]:
        raw: Final = await self.runtime.oauth_worker_check(("generate-profile", locale, os))
        value: Final = TypeAdapter(dict[str, JsonValue]).validate_json(raw)
        if (
            value.keys() != {"version", "locale", "fingerprint", "os"}
            or value["version"] != "camoufox-0.5.9"
            or value["os"] != os
            or not isinstance(value["locale"], str)
            or (value["locale"].split("-")[-1] != locale if len(locale) == 2 else value["locale"] != locale)
        ):
            raise ValueError("worker returned an incompatible fingerprint")
        if not isinstance(value["fingerprint"], str):
            raise ValueError("worker returned an incompatible fingerprint encoding")
        return value

    async def self_check(self) -> bool:
        result: Final = ImageCheck.model_validate_json(await self.runtime.oauth_worker_check(("self-check",)))
        return result.healthy and result.sdk == "0.5.9" and bool(result.browser)

    async def confirm_login_script(self, session_id: UUID, token: str, sha256: str) -> bool:
        response: Final = await self.http.post(
            f"http://{oauth_browser_control_host(session_id)}:8092/control",
            headers={"Authorization": f"Bearer {token}"},
            json={"action": "script_check", "sha256": sha256},
            timeout=20,
            follow_redirects=False,
        )
        return response.status_code == 200 and len(response.content) <= 256 and response.json() == {"sha256": sha256}

    async def command(
        self,
        session_id: UUID,
        token: str,
        action: Literal["start", "status", "takeover", "resume", "cancel"],
        *,
        credentials: str | None = None,
    ) -> WorkerView:
        payload: Final = {"action": action, **({"credentials": credentials} if action == "start" else {})}
        # status 是只读查询；登录、接管等命令的未知结果不得重放。
        attempts: Final = 3 if action == "status" else 1
        for attempt in range(attempts):
            try:
                response = await self.http.post(
                    f"http://{oauth_browser_control_host(session_id)}:8092/control",
                    headers={"Authorization": f"Bearer {token}", "X-OAuth-Diagnostics": "1"},
                    json=payload,
                    timeout=5 if action == "status" else 20,
                    follow_redirects=False,
                )
                response.raise_for_status()
                if len(response.content) > 4096:
                    raise ValueError("worker response is oversized")
                return WorkerView.model_validate_json(response.content)
            except (httpx.TransportError, httpx.HTTPStatusError) as error:
                transient = isinstance(error, httpx.TransportError) or error.response.status_code in (502, 503, 504)
                if not transient or attempt + 1 == attempts:
                    raise
                await asyncio.sleep(0.2 * (attempt + 1))
        raise AssertionError("unreachable worker retry state")

    async def confirm_binding_script(self, session_id: UUID, token: str, sha256: str | None) -> bool:
        response: Final = await self.http.post(
            f"http://{oauth_browser_control_host(session_id)}:8092/control",
            headers={"Authorization": f"Bearer {token}"},
            json={"action": "script_check", "sha256": sha256, "purpose": "email_rebind"},
            timeout=20, follow_redirects=False,
        )
        return response.status_code == 200 and len(response.content) <= 256 and response.json() == {"sha256": sha256}

    async def binding_command(
        self, session_id: UUID, token: str, action: str, payload: dict[str, JsonValue] | None = None,
    ) -> RebindingWorkerView:
        response: Final = await self.http.post(
            f"http://{oauth_browser_control_host(session_id)}:8092/control",
            headers={"Authorization": f"Bearer {token}"}, json={"action": action, **(payload or {})},
            timeout=20, follow_redirects=False,
        )
        response.raise_for_status()
        if len(response.content) > 4096:
            raise ValueError("oversized email worker response")
        return RebindingWorkerView.model_validate_json(response.content)

    async def verify(self, endpoint: GatewayEnvironment, model: str) -> ModelAcceptance:
        if (
            not endpoint.routable
            or model not in endpoint.enabled_models
            or endpoint.api_base != f"http://cliproxy-{endpoint.id.hex}:8317/v1"
        ):
            return ModelAcceptance(chat=False, responses=False)
        chat, chat_detail = await self._infer(endpoint, model, "chat")
        responses, responses_detail = await self._infer(endpoint, model, "responses")
        return ModelAcceptance(
            chat=chat,
            responses=responses,
            failure_detail=None if chat and responses else f"Chat: {chat_detail}; Responses: {responses_detail}",
        )

    async def _infer(
        self, endpoint: GatewayEnvironment, model: str, mode: Literal["chat", "responses"]
    ) -> tuple[bool, str]:
        for budget in (256, 2048):
            payload = {
                "model": model,
                "stream": False,
                **(
                    {"messages": [{"role": "user", "content": "Hello. Reply briefly with Hello."}],
                     "max_completion_tokens": budget}
                    if mode == "chat"
                    else {"input": "Hello. Reply briefly with Hello.", "max_output_tokens": budget, "store": False}
                ),
            }
            try:
                response = await self.http.post(
                    endpoint.api_base + ("/chat/completions" if mode == "chat" else "/responses"),
                    json=payload,
                    headers={"Authorization": f"Bearer {endpoint.api_key}"},
                    timeout=30,
                    follow_redirects=False,
                )
                if not response.is_success:
                    return False, f"HTTP {response.status_code}"
                if len(response.content) > 1024 * 1024:
                    return False, "响应过大"
                body = TypeAdapter(dict[str, JsonValue]).validate_json(response.content)
                if complete_text(body, mode):
                    return True, "通过"
                truncated = (
                    any(choice.get("finish_reason") == "length" for choice in _objects(body.get("choices")))
                    if mode == "chat"
                    else body.get("status") == "incomplete" and any(
                        item.get("reason") == "max_output_tokens" for item in _objects((body.get("incomplete_details"),))
                    )
                )
                if not truncated:
                    return False, "未返回完整文本"
                if budget == 2048:
                    return False, "输出预算不足，响应被截断；可仅重试验收，不代表凭据失效"
            except httpx.HTTPError:
                return False, "网络错误或超时，结果未知"
            except ValueError:
                return False, "响应格式无效"
        raise AssertionError("unreachable model budget state")
