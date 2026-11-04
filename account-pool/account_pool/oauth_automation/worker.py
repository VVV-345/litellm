"""调用固定镜像的无网络自检及会话专属通道，模型验证只请求当前卡片的 CPA。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter

from account_pool.compose_runtime import ComposeRuntime
from account_pool.domain import GatewayEnvironment
from account_pool.oauth_automation.models import Locale, WorkerView
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


def _objects(value: JsonValue) -> tuple[dict[str, JsonValue], ...]:
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

    async def generate(self, locale: Locale) -> dict[str, JsonValue]:
        raw: Final = await self.runtime.oauth_worker_check(("generate-profile", locale))
        value: Final = TypeAdapter(dict[str, JsonValue]).validate_json(raw)
        if (
            value.keys() != {"version", "locale", "fingerprint"}
            or value["version"] != "camoufox-0.5.9"
            or value["locale"] != locale
        ):
            raise ValueError("worker returned an incompatible fingerprint")
        if not isinstance(value["fingerprint"], str):
            raise ValueError("worker returned an incompatible fingerprint encoding")
        return value

    async def self_check(self) -> bool:
        result: Final = ImageCheck.model_validate_json(await self.runtime.oauth_worker_check(("self-check",)))
        return result.healthy and result.sdk == "0.5.9" and bool(result.browser)

    async def command(
        self,
        session_id: UUID,
        token: str,
        action: Literal["start", "status", "takeover", "resume", "cancel"],
        *,
        credentials: str | None = None,
    ) -> WorkerView:
        payload: Final = {"action": action, **({"credentials": credentials} if action == "start" else {})}
        response: Final = await self.http.post(
            f"http://{oauth_browser_control_host(session_id)}:8092/control",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=20,
            follow_redirects=False,
        )
        response.raise_for_status()
        if len(response.content) > 4096:
            raise ValueError("worker response is oversized")
        return WorkerView.model_validate_json(response.content)

    async def verify(self, endpoint: GatewayEnvironment, model: str) -> ModelAcceptance:
        if (
            not endpoint.routable
            or model not in endpoint.enabled_models
            or endpoint.api_base != f"http://cliproxy-{endpoint.id.hex}:8317/v1"
        ):
            return ModelAcceptance(chat=False, responses=False)
        chat: Final = await self._infer(endpoint, model, "chat")
        responses: Final = await self._infer(endpoint, model, "responses")
        return ModelAcceptance(chat=chat, responses=responses)

    async def _infer(self, endpoint: GatewayEnvironment, model: str, mode: Literal["chat", "responses"]) -> bool:
        payload: Final = {
            "model": model,
            "stream": False,
            **(
                {
                    "messages": [{"role": "user", "content": "Hello. Reply briefly with Hello."}],
                    "max_completion_tokens": 256,
                }
                if mode == "chat"
                else {"input": "Hello. Reply briefly with Hello.", "max_output_tokens": 256, "store": False}
            ),
        }
        try:
            response: Final = await self.http.post(
                endpoint.api_base + ("/chat/completions" if mode == "chat" else "/responses"),
                json=payload,
                headers={"Authorization": f"Bearer {endpoint.api_key}"},
                timeout=30,
                follow_redirects=False,
            )
            if not response.is_success or len(response.content) > 1024 * 1024:
                return False
            return complete_text(TypeAdapter(dict[str, JsonValue]).validate_json(response.content), mode)
        except (httpx.HTTPError, ValueError):
            return False
