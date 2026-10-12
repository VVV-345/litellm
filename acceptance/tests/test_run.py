"""验收工具必须保留失败和跳过证据，不能把未执行项算作通过。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run import classify, execute, record_manual, render_report


def test_classify_does_not_hide_failures_or_skips():
    assert classify(1, "3 passed") == "failed"
    assert classify(0, "3 passed, 1 skipped") == "partial"
    assert classify(0, "3 passed") == "passed"
    assert classify(5, "no tests ran") == "failed"


def test_execute_records_real_stdout_stderr_and_exit(tmp_path):
    result = execute(
        "sample",
        [sys.executable, "-c", "import sys; print('output'); print('diagnostic', file=sys.stderr); sys.exit(2)"],
        tmp_path,
        tmp_path,
    )
    assert result["status"] == "failed"
    assert result["exit_code"] == 2
    assert (tmp_path / "sample.stdout.txt").read_text() == "output\n"
    assert (tmp_path / "sample.stderr.txt").read_text() == "diagnostic\n"


def test_missing_command_is_blocked_not_passed(tmp_path):
    result = execute("missing", [str(tmp_path / "no-such-program")], tmp_path, tmp_path)
    assert result["status"] == "blocked"
    assert result["exit_code"] is None


def test_offline_runner_removes_database_opt_in_instead_of_setting_empty(tmp_path):
    result = execute(
        "environment",
        [sys.executable, "-c", "import os; print('absent' if 'ACCOUNT_POOL_TEST_DATABASE_URL' not in os.environ else 'present')"],
        tmp_path,
        tmp_path,
    )
    assert result["exit_code"] == 0
    assert (tmp_path / "environment.stdout.txt").read_text() == "absent\n"


def test_report_preserves_unexecuted_and_unimplemented():
    matrix = [{"id": "new", "title": "待实现功能", "implementation": "not_implemented", "scope": "live"}]
    report = render_report(matrix, {}, "commit", "test-run")
    assert "not_implemented" in report
    assert "not_run" in report
    assert "正式全量上线：未通过" in report


def test_partial_report_cannot_claim_full_release_ready():
    matrix = [
        {"id": "done", "title": "完成", "implementation": "implemented", "scope": "local", "command": ["true"]},
        {"id": "missing", "title": "未执行", "implementation": "implemented", "scope": "live"},
    ]
    result = {"done": {"status": "passed"}}
    assert "正式全量上线：通过" not in render_report(matrix, result, "commit", "test-run")


def test_live_evidence_records_error_state_instead_of_http_success():
    assert classify(0, '{"status":"failed","http_status":200}', "live-proxy") == "failed"


def test_manual_live_result_keeps_auditable_evidence(tmp_path):
    result = record_manual("live-proxy", "failed", "response id 1; 20 endpoints status=error", tmp_path)
    assert result["status"] == "failed"
    assert result["evidence"] != ""
    assert Path(result["evidence"]).read_text().strip() == "response id 1; 20 endpoints status=error"
    assert classify(0, '{"status":"error"}', "live-proxy") == "blocked"
