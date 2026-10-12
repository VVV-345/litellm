"""真实站点验收工具在预览失败或重复时不得提交，报告不得携带凭据。"""

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live_check import import_file, proxy_delays


def test_duplicate_is_not_submitted_and_secrets_are_not_reported(tmp_path):
    path = tmp_path / "account.json"
    path.write_text('{"type":"codex","refresh_token":"private-token"}')

    def handler(request):
        assert request.url.path == "/account_pool/onboarding/preview"
        return httpx.Response(200, json=[{"status": "duplicate"}])

    with httpx.Client(base_url="https://test.invalid", transport=httpx.MockTransport(handler)) as client:
        result = import_file(client, path, submit=True)
    assert result["status"] == "blocked"
    assert result["preview"] == "duplicate"
    assert "private-token" not in json.dumps(result)


def test_http_200_does_not_hide_proxy_measurement_failure():
    def handler(request):
        return httpx.Response(200, json=[{"port": 7891, "status": "error", "delay_ms": None}])

    with httpx.Client(base_url="https://test.invalid", transport=httpx.MockTransport(handler)) as client:
        result = proxy_delays(client)
    assert result["status"] == "failed"
    assert result["http_status"] == 200
