"""在本地 Git fixture 验证候选合并、冲突与过期阻断，不调用 GitHub 或生产。"""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".github/scripts/litellm_upstream.py"


def runner():
    spec = importlib.util.spec_from_file_location("litellm_upstream_runner", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True, encoding="utf-8").strip()


@pytest.fixture
def repository(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-b", "CLIProxyAPI分支")
    git(source, "config", "user.email", "fixture@example.invalid")
    git(source, "config", "user.name", "Fixture")
    (source / "shared.txt").write_text("base\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "base")
    base = git(source, "rev-parse", "HEAD")
    git(source, "switch", "-c", "official")
    (source / "official.txt").write_text("official\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "official")
    git(source, "tag", "v1.105.0")
    git(source, "switch", "CLIProxyAPI分支")
    (source / "pool.txt").write_text("custom pool\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "pool")
    git(source, "branch", "codex/litellm-upstream-sync")
    return source


def test_candidate_keeps_pool_and_official_code_without_changing_business(repository, tmp_path):
    module = runner()
    before = git(repository, "rev-parse", "CLIProxyAPI分支")
    report = module.prepare(repository, str(repository), "v1.105.0", "analyze", "af094d6b-f0da-4ad6-aa59-7704393a81a4", tmp_path / "result")
    assert report["state"] == "prepared"
    candidate = tmp_path / "result/candidate"
    assert (candidate / "pool.txt").read_text() == "custom pool\n"
    assert (candidate / "official.txt").read_text() == "official\n"
    assert git(repository, "rev-parse", "CLIProxyAPI分支") == before
    assert report["base_sha"] == before
    assert report["target_sha"] == git(repository, "rev-parse", "v1.105.0")


def test_conflict_does_not_modify_business(repository, tmp_path):
    git(repository, "switch", "official")
    (repository / "shared.txt").write_text("official conflict\n")
    git(repository, "commit", "-am", "conflict")
    git(repository, "tag", "v1.106.0")
    git(repository, "switch", "CLIProxyAPI分支")
    (repository / "shared.txt").write_text("pool conflict\n")
    git(repository, "commit", "-am", "local conflict")
    git(repository, "branch", "-f", "codex/litellm-upstream-sync", "HEAD")
    before = git(repository, "rev-parse", "HEAD")
    result = runner().prepare(repository, str(repository), "v1.106.0", "analyze", "af094d6b-f0da-4ad6-aa59-7704393a81a4", tmp_path / "result")
    assert result["state"] == "conflict"
    assert result["conflict_files"] == ["shared.txt"]
    assert git(repository, "rev-parse", "HEAD") == before


def test_promotion_requires_current_passed_report(repository, tmp_path):
    with pytest.raises(RuntimeError, match="passed report"):
        runner().prepare(repository, str(repository), "v1.105.0", "promote", "af094d6b-f0da-4ad6-aa59-7704393a81a4", tmp_path / "result")


def finish_env(monkeypatch, result="success"):
    monkeypatch.setenv("VALIDATION_RESULTS", json.dumps({"validation": result}))
    monkeypatch.setenv("GITHUB_REPOSITORY", "VVV-345/litellm")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")


@pytest.mark.parametrize("outcome,state", [("success", "passed"), ("failure", "failed"), ("cancelled", "failed")])
def test_finish_records_result_and_bundles_tested_candidate(repository, tmp_path, monkeypatch, outcome, state):
    module = runner()
    output = tmp_path / "result"
    prepared = module.prepare(repository, str(repository), "v1.105.0", "analyze", "af094d6b-f0da-4ad6-aa59-7704393a81a4", output)
    finish_env(monkeypatch, outcome)
    module.finish(output)
    report = json.loads((output / "result.json").read_text())
    assert report["state"] == state
    assert report["candidate_sha"] == prepared["candidate_sha"]
    assert report["updated_at"]
    assert (output / "candidate.bundle").is_file()
    assert git(output / "candidate", "rev-parse", "HEAD^") == prepared["candidate_sha"]


def test_finish_rejects_candidate_changes(repository, tmp_path, monkeypatch):
    module = runner()
    output = tmp_path / "result"
    module.prepare(repository, str(repository), "v1.105.0", "analyze", "af094d6b-f0da-4ad6-aa59-7704393a81a4", output)
    (output / "candidate/pool.txt").write_text("tampered")
    finish_env(monkeypatch)
    with pytest.raises(RuntimeError, match="Candidate changed"):
        module.finish(output)


def test_remote_only_compatibility_ref_is_preserved(repository, tmp_path):
    expected = git(repository, "rev-parse", "codex/litellm-upstream-sync")
    git(repository, "update-ref", "refs/remotes/origin/codex/litellm-upstream-sync", expected)
    git(repository, "branch", "-D", "codex/litellm-upstream-sync")
    output = tmp_path / "result"
    runner().prepare(repository, str(repository), "v1.105.0", "analyze", "af094d6b-f0da-4ad6-aa59-7704393a81a4", output)
    assert (output / "sync-base.txt").read_text() == expected


def test_unrecorded_compatibility_work_is_not_discarded(repository, tmp_path):
    git(repository, "switch", "codex/litellm-upstream-sync")
    (repository / "repair.txt").write_text("repair")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "unrecorded repair")
    git(repository, "switch", "CLIProxyAPI分支")
    with pytest.raises(RuntimeError, match="unrecorded work"):
        runner().prepare(repository, str(repository), "v1.105.0", "analyze", "af094d6b-f0da-4ad6-aa59-7704393a81a4", tmp_path / "result")


@pytest.mark.parametrize("concurrent", [False, True])
def test_publish_changes_only_compatibility_branch_and_checks_lease(repository, tmp_path, monkeypatch, concurrent):
    module = runner()
    remote = tmp_path / "remote.git"
    git(tmp_path, "clone", "--bare", str(repository), str(remote))
    git(repository, "remote", "add", "origin", str(remote))
    output = tmp_path / "result"
    prepared = module.prepare(repository, str(repository), "v1.105.0", "analyze", "af094d6b-f0da-4ad6-aa59-7704393a81a4", output)
    finish_env(monkeypatch)
    module.finish(output)
    if concurrent:
        git(remote, "update-ref", "refs/heads/codex/litellm-upstream-sync", prepared["target_sha"])
        with pytest.raises(RuntimeError, match="concurrent work"):
            module.publish(repository, output)
        assert git(remote, "rev-parse", "codex/litellm-upstream-sync") == prepared["target_sha"]
    else:
        module.publish(repository, output)
        report = json.loads(git(remote, "show", "codex/litellm-upstream-sync:.codex/upstream-sync-status.json"))
        assert report["state"] == "passed"
    assert git(remote, "rev-parse", "CLIProxyAPI分支") == prepared["base_sha"]


@pytest.mark.parametrize("pr_fails", [False, True])
def test_promote_retests_preserved_candidate_and_records_pr_result(repository, tmp_path, monkeypatch, pr_fails):
    module = runner()
    remote = tmp_path / "remote.git"
    git(tmp_path, "clone", "--bare", str(repository), str(remote))
    git(repository, "remote", "add", "origin", str(remote))
    output = tmp_path / "analyze"
    prepared = module.prepare(repository, str(repository), "v1.105.0", "analyze", "af094d6b-f0da-4ad6-aa59-7704393a81a4", output)
    finish_env(monkeypatch)
    module.finish(output)
    module.publish(repository, output)
    git(repository, "fetch", "origin")
    promote = tmp_path / "promote"
    module.prepare(repository, str(repository), "v1.105.0", "promote", "11111111-1111-4111-8111-111111111111", promote)
    module.finish(promote)
    def github(arguments, source):
        if arguments[1] == "list":
            return ""
        assert "--draft" in arguments
        assert arguments[arguments.index("--base") + 1] == "CLIProxyAPI分支"
        if pr_fails:
            raise RuntimeError("GitHub refused")
        return "https://github.com/VVV-345/litellm/pull/42"
    if pr_fails:
        with pytest.raises(RuntimeError, match="PR creation failed"):
            module.publish(repository, promote, github)
    else:
        module.publish(repository, promote, github)
    report = json.loads(git(remote, "show", "codex/litellm-upstream-sync:.codex/upstream-sync-status.json"))
    assert report["state"] == ("failed" if pr_fails else "pr_created")
    assert report["pull_request_url"] == (None if pr_fails else "https://github.com/VVV-345/litellm/pull/42")
    assert git(remote, "rev-parse", "CLIProxyAPI分支") == prepared["base_sha"]



def test_workflow_changes_require_explicit_manual_publication(repository, tmp_path, monkeypatch):
    module = runner()
    git(repository, "switch", "official")
    workflow = repository / ".github/workflows/new.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: new")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "official workflow")
    git(repository, "tag", "v1.106.0")
    git(repository, "switch", "CLIProxyAPI分支")
    remote = tmp_path / "remote.git"
    git(tmp_path, "clone", "--bare", str(repository), str(remote))
    git(repository, "remote", "add", "origin", str(remote))
    output = tmp_path / "result"
    prepared = module.prepare(repository, str(repository), "v1.106.0", "analyze", "af094d6b-f0da-4ad6-aa59-7704393a81a4", output)
    finish_env(monkeypatch)
    module.finish(output)
    with pytest.raises(RuntimeError, match="Workflow changes require manual publication"):
        module.publish(repository, output)
    assert git(remote, "rev-parse", "codex/litellm-upstream-sync") == prepared["base_sha"]
    assert json.loads((output / "result.json").read_text())["state"] == "failed"
