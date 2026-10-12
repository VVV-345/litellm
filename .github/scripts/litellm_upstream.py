"""在一次性副本合并固定 LiteLLM tag，输出候选与证据；发布和 PR 由受控工作流负责。"""

import argparse
import json
import os
import re
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast
from uuid import UUID

BUSINESS = "CLIProxyAPI分支"
SYNC = "codex/litellm-upstream-sync"
STATUS = ".codex/upstream-sync-status.json"
REVIEW = ".codex/upstream-sync-review.md"


class SyncReport(TypedDict):
    schema_version: int
    state: str
    action: str
    request_id: str
    base_sha: str
    target_sha: str
    target_tag: str
    candidate_sha: str
    conflict_files: list[str]
    failed_steps: list[str]
    message: str
    updated_at: str
    workflow_url: str
    pull_request_url: str | None


def read_report(path: Path) -> SyncReport:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid report")
    for field in ("base_sha", "target_sha", "candidate_sha"):
        if not isinstance(payload.get(field), str) or not re.fullmatch("[0-9a-f]{40}", payload[field]):
            raise ValueError("Invalid report identity")
    UUID(payload["request_id"])
    if payload.get("action") not in ("analyze", "promote"):
        raise ValueError("Invalid report action")
    return cast(SyncReport, payload)


def write_report(candidate: Path, report: SyncReport) -> None:
    status = candidate / STATUS
    if status.parent.is_symlink() or status.is_symlink() or (candidate / REVIEW).is_symlink():
        raise RuntimeError("Evidence paths must not be symlinks")
    status.parent.mkdir(exist_ok=True)
    status.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (candidate / REVIEW).write_text(
        "# LiteLLM upstream review\n\n```json\n"
        + json.dumps(report, ensure_ascii=False, indent=2)
        + "\n```\n\nReview configured checks and run live acceptance before merging. "
        "Never deploy from this report alone.\n",
        encoding="utf-8",
    )
    git(candidate, "add", STATUS, REVIEW)
    git(candidate, "commit", "-m", "chore: record LiteLLM compatibility evidence")


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def prepare(source: Path, upstream: str, tag: str, action: str, request: str, output: Path) -> SyncReport:
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:[.-][0-9A-Za-z.-]+)?", tag):
        raise ValueError("Invalid release tag")
    UUID(request)
    if action not in ("analyze", "promote"):
        raise ValueError("Invalid action")
    output.mkdir(parents=True, exist_ok=True)
    candidate = output / "candidate"
    git(output, "clone", "--no-hardlinks", "--branch", BUSINESS, str(source), str(candidate))
    git(candidate, "config", "user.name", "github-actions[bot]")
    git(candidate, "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    base = git(candidate, "rev-parse", "HEAD")
    source_sync = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "--verify", f"refs/remotes/origin/{SYNC}"],
        capture_output=True,
        text=True,
    )
    if source_sync.returncode == 0:
        git(candidate, "fetch", str(source), f"refs/remotes/origin/{SYNC}:refs/remotes/origin/{SYNC}")
    sync_base = git(candidate, "rev-parse", f"origin/{SYNC}")
    git(candidate, "fetch", "--no-tags", upstream, f"refs/tags/{tag}")
    target = git(candidate, "rev-parse", "FETCH_HEAD^{commit}")
    previous_text = subprocess.run(
        ["git", "-C", str(candidate), "show", f"origin/{SYNC}:{STATUS}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    previous = json.loads(previous_text.stdout) if previous_text.returncode == 0 else {}
    same = previous.get("base_sha") == base and previous.get("target_sha") == target
    if action == "promote" and (not same or previous.get("state") not in ("passed", "pr_created")):
        raise RuntimeError("A current passed report is required before creating a PR")
    if not previous and sync_base != base:
        raise RuntimeError("Compatibility branch has unrecorded work; reconcile it before retrying")
    if previous and not same:
        raise RuntimeError("Compatibility branch has another baseline or target; archive/reconcile it before retrying")
    git(candidate, "switch", "-C", SYNC, f"origin/{SYNC}" if same else base)
    result = subprocess.run(
        ["git", "-C", str(candidate), "merge", "--no-ff", "--no-edit", target],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    (output / "merge.stdout.txt").write_text(result.stdout, encoding="utf-8")
    (output / "merge.stderr.txt").write_text(result.stderr, encoding="utf-8")
    conflicts = git(candidate, "diff", "--name-only", "--diff-filter=U").splitlines()
    if result.returncode:
        if not conflicts:
            raise RuntimeError(result.stderr)
        git(candidate, "merge", "--abort")
    if action == "promote":
        tested = previous.get("candidate_sha")
        if not tested or git(
            candidate,
            "diff",
            tested,
            "HEAD",
            "--",
            ".",
            ":(exclude).codex/upstream-sync-status.json",
            ":(exclude).codex/upstream-sync-review.md",
        ):
            raise RuntimeError("Candidate changed after validation; analyze again")
    report: SyncReport = {
        "schema_version": 1,
        "state": "conflict" if conflicts else "prepared",
        "action": action,
        "request_id": request,
        "base_sha": base,
        "target_sha": target,
        "target_tag": tag,
        "candidate_sha": git(candidate, "rev-parse", "HEAD"),
        "conflict_files": conflicts,
        "failed_steps": [],
        "message": "Candidate prepared; validation has not passed yet",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "workflow_url": "",
        "pull_request_url": None,
    }
    (output / "sync-base.txt").write_text(sync_base, encoding="utf-8")
    (output / "prepared.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["candidate_sha"] != base:
        git(candidate, "bundle", "create", str(output / "prepared.bundle"), SYNC, "^" + base)
    return report


def restore(source: Path, output: Path) -> None:
    report = read_report(output / "prepared.json")
    candidate = output / "candidate"
    git(output, "clone", "--no-hardlinks", "--branch", BUSINESS, str(source), str(candidate))
    git(candidate, "config", "user.name", "github-actions[bot]")
    git(candidate, "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    if (output / "prepared.bundle").exists():
        git(candidate, "fetch", str(output / "prepared.bundle"), f"{SYNC}:refs/remotes/prepared/upstream")
    git(candidate, "switch", "-C", SYNC, report["candidate_sha"])
    if git(candidate, "rev-parse", "HEAD") != report["candidate_sha"]:
        raise RuntimeError("Candidate identity mismatch")


def finish(output: Path) -> None:
    report = read_report(output / "prepared.json")
    candidate = output / "candidate"
    if git(candidate, "rev-parse", "HEAD") != report["candidate_sha"] or git(
        candidate, "status", "--porcelain", "--untracked-files=no"
    ):
        raise RuntimeError("Candidate changed after preparation")
    checks = json.loads(os.environ["VALIDATION_RESULTS"])
    if not isinstance(checks, dict) or not checks:
        raise ValueError("Validation results are required")
    failed = [name for name, outcome in checks.items() if outcome != "success"]
    result: SyncReport = {
        **report,
        "state": "conflict" if report["conflict_files"] else "failed" if failed else "passed",
        "failed_steps": failed,
        "message": "Configured checks completed; live service and migration acceptance remain separate",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "workflow_url": f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
    }
    write_report(candidate, result)
    git(candidate, "bundle", "create", str(output / "candidate.bundle"), SYNC, "^" + report["base_sha"])
    (output / "result.json").write_text(json.dumps(result), encoding="utf-8")


def github_command(arguments: list[str], source: Path) -> str:
    return subprocess.check_output(["gh", *arguments], cwd=source, text=True, encoding="utf-8").strip()


def publish(source: Path, output: Path, github: Callable[[list[str], Path], str] = github_command) -> None:
    report = read_report(output / "result.json")
    expected = (output / "sync-base.txt").read_text(encoding="utf-8").strip()
    if not re.fullmatch("[0-9a-f]{40}", expected):
        raise ValueError("Invalid preparation lease")
    git(source, "fetch", "origin", BUSINESS, SYNC)
    if git(source, "rev-parse", f"origin/{BUSINESS}") != report["base_sha"]:
        raise RuntimeError("Business baseline changed; analyze again")
    if git(source, "rev-parse", f"origin/{SYNC}") != expected:
        raise RuntimeError("Compatibility branch changed; refusing to overwrite concurrent work")
    candidate = output / "candidate"
    if git(candidate, "rev-parse", "HEAD^") != report["candidate_sha"]:
        raise RuntimeError("Published candidate does not match prepared identity")
    # 仅把不可变准备产物发布到兼容分支，永不更新业务分支
    git(source, "fetch", str(output / "candidate.bundle"), f"{SYNC}:refs/remotes/candidate/upstream")
    head = git(source, "rev-parse", "refs/remotes/candidate/upstream")
    if git(source, "diff", "--name-only", expected, head, "--", ".github/workflows"):
        blocked: SyncReport = {
            **report,
            "state": "failed",
            "failed_steps": ["workflow_file_publication"],
            "message": "Workflow changes require manual publication with separately approved workflows permission",
        }
        (output / "result.json").write_text(json.dumps(blocked), encoding="utf-8")
        raise RuntimeError(blocked["message"])
    git(source, "push", f"--force-with-lease=refs/heads/{SYNC}:{expected}", "origin", f"{head}:refs/heads/{SYNC}")
    if report["action"] != "promote" or report["state"] != "passed":
        return
    body = output / "pr-body.md"
    body.write_text(
        "## TLDR\n\nProblem this solves:\n\n- Official LiteLLM changes need account-pool compatibility review\n\n"
        "How it solves it:\n\n- Retains pool changes and proposes a reviewed upstream merge\n\n"
        "## User Flow\n\nBefore: operators use the existing release\n\n"
        "1. Open /ui/account-pool/ and select LiteLLM upstream updates\n"
        "2. Review the configured upstream baseline\n\n"
        "After: operators can review an update before merging\n\n"
        "1. Open /ui/account-pool/ and select LiteLLM upstream updates\n"
        "2. Open this compatibility PR after validation\n\n"
        "## Relevant issues\n\n## Screenshots / Proof of Fix\n\n"
        f"Live QA not performed by this workflow. Compare {report['base_sha']} with {head}\n\n"
        "Open the account-pool page on both test deployments, verify authorization, models, quota and streaming, "
        "then attach before/after evidence before marking this draft ready\n\n"
        f"Automated workflow evidence: {report['workflow_url']}\n\n"
        "## Type\n\nInfrastructure\n\n## Caveats (if any)\n\n### Severe\n\n"
        "- Merging builds images; enabled server auto-deployment may follow\n\n### Medium\n\n"
        "- Live API, database and rollback acceptance remain required\n"
        "- Revalidate after business baseline or candidate code changes\n",
        encoding="utf-8",
    )
    try:
        existing = github(
            [
                "pr",
                "list",
                "--repo",
                "VVV-345/litellm",
                "--head",
                SYNC,
                "--base",
                BUSINESS,
                "--json",
                "url",
                "--jq",
                ".[0].url // empty",
            ],
            source,
        )
        url = existing or github(
            [
                "pr",
                "create",
                "--draft",
                "--repo",
                "VVV-345/litellm",
                "--head",
                SYNC,
                "--base",
                BUSINESS,
                "--title",
                f"chore: integrate LiteLLM {report['target_tag']}",
                "--body-file",
                str(body),
            ],
            source,
        )
        if not re.fullmatch(r"https://github.com/VVV-345/litellm/pull/[0-9]+", url):
            raise RuntimeError("Unexpected PR URL")
        final: SyncReport = {
            **report,
            "state": "pr_created",
            "pull_request_url": url,
            "message": "Update PR created or reused; review and live acceptance required",
        }
    except (subprocess.CalledProcessError, RuntimeError):
        final = {
            **report,
            "state": "failed",
            "failed_steps": ["create_pull_request"],
            "message": "PR creation failed; inspect Actions and existing PRs before retrying",
        }
    write_report(candidate, final)
    git(source, "fetch", str(candidate), f"{SYNC}:refs/remotes/candidate/upstream")
    git(
        source,
        "push",
        f"--force-with-lease=refs/heads/{SYNC}:{head}",
        "origin",
        f"refs/remotes/candidate/upstream:refs/heads/{SYNC}",
    )
    (output / "result.json").write_text(json.dumps(final), encoding="utf-8")
    if final["state"] == "failed":
        raise RuntimeError(final["message"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "restore", "finish", "publish"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.phase == "finish":
        finish(args.output)
    elif args.phase == "restore":
        restore(Path.cwd(), args.output)
    elif args.phase == "publish":
        publish(Path.cwd(), args.output)
    else:
        prepare(
            Path.cwd(),
            "https://github.com/BerriAI/litellm.git",
            os.environ["SYNC_TARGET_TAG"],
            os.environ["SYNC_ACTION"],
            os.environ["SYNC_REQUEST_ID"],
            args.output,
        )
