"""显式指定站点后检查真实代理或单文件导入，只输出白名单摘要。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Final, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, JsonValue, TypeAdapter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "account-pool"))

from account_pool.onboarding_models import OnboardingEntry, OnboardingImport


class Preview(BaseModel):
    status: Literal["valid", "invalid", "duplicate"]


class Task(BaseModel):
    id: str
    card_id: str
    state: str
    models: tuple[str, ...] = ()


class ImportResult(BaseModel):
    items: tuple[Task, ...]


class Delay(BaseModel):
    port: int
    status: Literal["ok", "error", "timeout"]
    delay_ms: int | None


def proxy_delays(client: httpx.Client) -> dict[str, JsonValue]:
    response: Final = client.post("/account_pool/proxy-gateways/delay")
    if response.status_code != 200:
        return {"status": "failed", "http_status": response.status_code}
    delays: Final = TypeAdapter(tuple[Delay, ...]).validate_json(response.content)
    return {
        "status": "passed" if delays and all(row.status == "ok" and row.delay_ms is not None for row in delays) else "failed",
        "http_status": response.status_code,
        "measurements": [row.model_dump(mode="json") for row in delays],
    }


def import_file(client: httpx.Client, path: Path, *, submit: bool) -> dict[str, JsonValue]:
    request: Final = OnboardingImport(job_id=uuid4(), source="auth_file", supplier="openai_codex",
                                     entries=(OnboardingEntry(label="acceptance-slot-1.json",
                                                              content=path.read_text(encoding="utf-8-sig")),))
    preview_response: Final = client.post("/account_pool/onboarding/preview", content=request.model_dump_json(),
                                         headers={"Content-Type": "application/json"})
    if preview_response.status_code != 200:
        return {"status": "blocked", "stage": "preview", "http_status": preview_response.status_code}
    preview: Final = TypeAdapter(tuple[Preview, ...]).validate_json(preview_response.content)
    if len(preview) != 1 or preview[0].status != "valid":
        return {"status": "blocked", "preview": preview[0].status if preview else "missing"}
    if not submit:
        return {"status": "partial", "preview": "valid", "submitted": False}
    response: Final = client.post("/account_pool/onboarding/imports", content=request.model_dump_json(),
                                 headers={"Content-Type": "application/json"})
    if response.status_code != 202:
        return {"status": "failed", "stage": "submit", "http_status": response.status_code}
    created: Final = ImportResult.model_validate_json(response.content)
    if len(created.items) != 1:
        return {"status": "failed", "stage": "submit", "created_count": len(created.items)}
    task_id: Final = created.items[0].id
    for _ in range(60):
        polled: Final = client.get("/account_pool/onboarding/items")
        if polled.status_code != 200:
            return {"status": "blocked", "stage": "poll", "http_status": polled.status_code, "item_id": task_id}
        tasks: Final = TypeAdapter(tuple[Task, ...]).validate_json(polled.content)
        task: Final = next((item for item in tasks if item.id == task_id), None)
        if task is not None and task.state in ("ready", "failed", "disabled", "cooling_down"):
            return {"status": "passed" if task.state == "ready" and task.models else "failed", "state": task.state,
                    "model_count": len(task.models), "item_id": task.id, "card_id": task.card_id}
        time.sleep(2)
    return {"status": "blocked", "stage": "poll_timeout", "item_id": task_id,
            "reason": "提交已成功，需继续查看原任务，不能盲目重复提交"}


def main() -> int:
    parser: Final = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("proxy", "import"))
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--submit", action="store_true", help="实际导入，默认仅预览")
    args: Final = parser.parse_args()
    origin: Final = urlsplit(args.base_url)
    if origin.scheme != "https" or not origin.hostname or origin.username or origin.password or origin.path not in ("", "/"):
        parser.error("base-url 必须是无凭据的 HTTPS origin")
    if args.mode == "import" and args.file is None:
        parser.error("import 必须显式指定 --file")
    token: Final = os.environ.get("ACCEPTANCE_ACCESS_TOKEN", "")
    if not token:
        print('{"status":"blocked","reason":"ACCEPTANCE_ACCESS_TOKEN is not set"}')
        return 2
    try:
        with httpx.Client(base_url=args.base_url, headers={"Authorization": f"Bearer {token}"},
                          timeout=30, trust_env=False, follow_redirects=False) as client:
            result: Final = proxy_delays(client) if args.mode == "proxy" else import_file(client, args.file, submit=args.submit)
    except (httpx.HTTPError, ValueError, OSError) as error:
        print(json.dumps({"status": "blocked", "error_type": type(error).__name__}))
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return int(result["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
