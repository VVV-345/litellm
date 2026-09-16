"""本脚本用标准库连接服务器本机部署后台，旧版页面不可用时仍可恢复备份。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from http.client import HTTPResponse
from pathlib import Path
from typing import Final, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def read_token(directory: Path) -> str:
    values: Final = tuple(
        line.strip().partition("=")
        for line in (directory / ".env").read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("ACCOUNT_POOL_RELEASE_TOKEN=")
    )
    token: Final = values[-1][2].strip().strip("\"'") if values else ""
    if len(token) < 32:
        raise SystemExit("请先在部署目录 .env 中设置 ACCOUNT_POOL_RELEASE_TOKEN")
    return token


def record(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SystemExit("部署后台返回无效数据")
    return cast(dict[str, object], value)


def call(token: str, path: str = "", body: dict[str, object] | None = None) -> dict[str, object]:
    request: Final = Request(
        "http://127.0.0.1:8092/api/releases" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "X-Release-Actor": "server-cli",
        },
    )
    try:
        with cast(HTTPResponse, urlopen(request, timeout=35)) as response:
            return record(cast(object, json.loads(response.read())))
    except HTTPError as error:
        detail: Final = record(cast(object, json.loads(error.read()))).get("detail", "请求失败")
        raise SystemExit(str(detail)) from None
    except URLError:
        raise SystemExit("部署后台不可用，请检查 docker compose logs release-worker") from None


def main() -> None:
    parser: Final = argparse.ArgumentParser(description="项目镜像备份与恢复，仅连接服务器 127.0.0.1:8092")
    parser.add_argument("action", choices=("status", "scan", "apply", "delete", "deploy", "recover"))
    parser.add_argument("target", nargs="?", help="apply/delete 使用备份 ID；deploy 使用新版本 commit 前 10 位")
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parent)
    args: Final = cast(dict[str, object], vars(parser.parse_args()))
    action: Final = str(args["action"])
    target: Final = str(args["target"]) if args["target"] else None
    token: Final = read_token(Path(str(args["directory"])))
    view: Final = call(token)
    if action == "status":
        output(json.dumps(view, ensure_ascii=False, indent=2))
        return
    if action in ("apply", "delete", "deploy") and not target:
        parser.error("此操作需要提供目标版本")
    body: Final = {
        "action": action,
        "revision": view["revision"],
        **({"tag": target} if action == "deploy" else {}),
        **({"version_id": target} if action in ("apply", "delete") else {}),
    }
    confirmation: Final = call(token, "/prepare", body)
    output(f"操作：{action}，目标：{target or '当前项目'}，当前 commit：{confirmation['current_commit']}")
    output("删除将移除备份归档；切换会短暂中断服务。数据库和认证文件不随镜像回退。")
    for seconds in range(int(str(confirmation["delay_seconds"])), 0, -1):
        output(f"\r请思考确认，剩余 {seconds} 秒 ", end="")
        time.sleep(1)
    if input("\n输入 CONFIRM 执行，其余输入取消：").strip() != "CONFIRM":
        output("已取消")
        return
    job: Final = call(token, "/execute", {"token": confirmation["token"]})
    output(f"任务 {job['id']} 已保存，可以随时用 status 查看")
    while poll(token, job):
        time.sleep(3)


def poll(token: str, job: dict[str, object]) -> bool:
    current: Final = record(call(token).get("job"))
    if current.get("id") != job["id"]:
        raise SystemExit("已有后续任务，请用 status 核对当前运行版本")
    if current["status"] not in ("queued", "running"):
        output(f"\n{current['status']}：{current['phase']} {current['message']}")
        sys.exit(0 if current["status"] == "succeeded" else 1)
    output(f"\r{current['phase']}                          ", end="")
    return True


def output(message: str, end: str = "\n") -> None:
    sys.stdout.write(message + end)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
