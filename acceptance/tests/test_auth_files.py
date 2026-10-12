"""真实文件检查必须只输出匿名槽位，并验证供应商和原始字节不变。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from check_auth_files import inspect_directory


def test_inspection_uses_production_validation_without_disclosing_secrets(tmp_path):
    path = tmp_path / "private@example.com.json"
    content = b'{"type":"codex","email":"private@example.com","refresh_token":"secret-refresh"}'
    path.write_bytes(content)
    result = inspect_directory(tmp_path)
    encoded = json.dumps(result)
    assert result["status"] == "passed"
    assert result["files"][0]["unchanged"] is True
    assert path.read_bytes() == content
    assert "private@example.com" not in encoded
    assert "secret-refresh" not in encoded


def test_invalid_provider_and_empty_input_are_not_passed(tmp_path):
    assert inspect_directory(tmp_path)["status"] == "failed"
    (tmp_path / "wrong.json").write_text('{"type":"claude","refresh_token":"token"}')
    assert inspect_directory(tmp_path)["status"] == "failed"
