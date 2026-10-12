"""执行显式选定的验收命令并生成报告，未执行和跳过项不计为验收通过。"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Literal, NotRequired, TypedDict

from pydantic import TypeAdapter

Status = Literal["passed", "failed", "partial", "not_run", "blocked", "excluded"]


class Case(TypedDict):
    id: str
    title: str
    implementation: Literal["implemented", "not_implemented", "unknown"]
    scope: str
    command: NotRequired[list[str]]
    cwd: NotRequired[str]
    criterion: NotRequired[str]
    excluded: NotRequired[bool]


class Result(TypedDict):
    status: Status
    exit_code: int | None
    command: list[str]
    cwd: str
    started_at: str
    finished_at: str
    stdout: str
    stderr: str


def classify(exit_code: int, output: str) -> Status:
    if exit_code != 0:
        return "failed"
    return "partial" if re.search(r"\b[1-9]\d* skipped\b", output) else "passed"


def execute(case_id: str, command: list[str], cwd: Path, output: Path) -> Result:
    started: Final = datetime.now(timezone.utc).isoformat()
    stdout: Final = output / f"{case_id}.stdout.txt"
    stderr: Final = output / f"{case_id}.stderr.txt"
    environment: Final = {
        **os.environ,
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "PYTHONDONTWRITEBYTECODE": "1",
        "RUN_RELEASE_DATABASE_INTEGRATION": "0",
        "ACCOUNT_POOL_TEST_DATABASE_URL": "",
        "ACCOUNT_POOL_STATS_TEST_DATABASE_URL": "",
        "CI": "1",
    }
    try:
        process: Final = subprocess.run(command, cwd=cwd, env=environment, capture_output=True, timeout=1200)
    except OSError as error:
        stdout.write_text("", encoding="utf-8")
        stderr.write_text(type(error).__name__, encoding="utf-8")
        return Result(status="blocked", exit_code=None, command=command, cwd=str(cwd), started_at=started,
                      finished_at=datetime.now(timezone.utc).isoformat(), stdout=str(stdout), stderr=str(stderr))
    except subprocess.TimeoutExpired as error:
        stdout.write_bytes(error.stdout or b"")
        stderr.write_bytes((error.stderr or b"") + b"\nAcceptance process exceeded 1200 seconds\n")
        return Result(status="failed", exit_code=None, command=command, cwd=str(cwd), started_at=started,
                      finished_at=datetime.now(timezone.utc).isoformat(), stdout=str(stdout), stderr=str(stderr))
    stdout.write_bytes(process.stdout)
    stderr.write_bytes(process.stderr)
    return Result(
        status=classify(process.returncode, (process.stdout + process.stderr).decode("utf-8", errors="replace")),
        exit_code=process.returncode, command=command, cwd=str(cwd), started_at=started,
        finished_at=datetime.now(timezone.utc).isoformat(), stdout=str(stdout), stderr=str(stderr),
    )


def render_report(matrix: list[Case], results: dict[str, Result], commit: str, run_id: str) -> str:
    rows: Final = tuple(
        f"| {case['id']} | {case['title']} | {case['implementation']} | "
        f"{results[case['id']]['status'] if case['id'] in results else 'excluded' if case.get('excluded') else 'not_run'} | "
        f"{case['scope']} | {case.get('criterion', '')} |"
        for case in matrix
    )
    accepted: Final = all(case.get("excluded") or results.get(case["id"], {}).get("status") == "passed" for case in matrix)
    return "\n".join((
        f"# 验收报告 {run_id}", "", f"源码提交：`{commit}`", "",
        f"正式全量上线：{'通过' if accepted else '未通过'}", "",
        "自动报告只反映本轮已登记结果；真实服务与人工验收证据另见经审阅的报告。未执行不等于未实现。", "",
        "| 编号 | 项目 | 实现状态 | 验收状态 | 范围 | 通过标准 |",
        "| --- | --- | --- | --- | --- | --- |", *rows, "",
        "命令、工作目录、起止时间、退出码与完整输出路径见同目录 results.json", "",
    ))


def main() -> int:
    parser: Final = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", nargs="+", required=True)
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    args: Final = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.run_id):
        parser.error("run-id must contain only letters, digits, underscores or hyphens")
    root: Final = Path(__file__).resolve().parents[1]
    matrix: Final = TypeAdapter(list[Case]).validate_json((root / "acceptance/matrix.json").read_text(encoding="utf-8"))
    selected: Final = tuple(case for case in matrix if case["id"] in args.cases)
    if set(args.cases) != {case["id"] for case in selected} or any(not case.get("command") for case in selected):
        parser.error("select only registered executable cases")
    output: Final = root / "acceptance/runs" / args.run_id
    output.mkdir(parents=True, exist_ok=False)
    commit: Final = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    results: Final[dict[str, Result]] = {}
    for case in selected:
        command: Final = [part.replace("{python}", sys.executable).replace("{root}", str(root)) for part in case["command"]]
        print(f"START {case['id']}", flush=True)
        results[case["id"]] = execute(case["id"], command, root / case.get("cwd", "."), output)
        (output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        (output / "REPORT.md").write_text(render_report(matrix, results, commit, args.run_id), encoding="utf-8")
        print(f"END {case['id']} {results[case['id']]['status']} exit={results[case['id']]['exit_code']}", flush=True)
    print(output / "REPORT.md")
    return int(any(result["status"] != "passed" for result in results.values()))


if __name__ == "__main__":
    raise SystemExit(main())
