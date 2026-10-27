"""验证单卡维护锁、在途拒绝及能力检查，不连接真实数据库或改动账号卷。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import httpx
import psycopg
import pytest
from account_pool.compose_renderer import render_compose
from account_pool.compose_runtime import ComposeRuntime
from account_pool.domain import EnvironmentRecord
from account_pool.shared.secrets import EnvironmentSecretDeriver
from test_cliproxy_runtime_image import _NEW_IMAGE, _OLD_IMAGE, _record, _Runner, _settings


class _Cursor:
    def __init__(self, row: Mapping[str, object] | None) -> None:
        self.row = row

    async def fetchone(self) -> Mapping[str, object] | None:
        return self.row


class _Connection:
    def __init__(self, record: EnvironmentRecord | None, *, active_lease: bool = False) -> None:
        self.record = record
        self.active_lease = active_lease
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    async def execute(self, query: str, params: tuple[object, ...]) -> _Cursor:
        self.statements.append((query, params))
        if "FROM account_pool_environments" in query:
            return _Cursor(None if self.record is None else {"payload": self.record.model_dump(mode="json")})
        if "FROM account_pool_leases" in query:
            return _Cursor({"active": 1} if self.active_lease else None)
        return _Cursor(None)


@pytest.mark.parametrize("blocked", ["enabled", "lease", "pending", "auth_enabled", "missing"])
async def test_maintenance_refuses_unsafe_card_before_docker(tmp_path: Path, blocked: str) -> None:
    from account_pool.runtime_maintenance import update_runtime

    record = _record().model_copy(update={
        "enabled": blocked == "enabled",
        "configuration_pending": blocked == "pending",
        "auth_file_disabled": blocked != "auth_enabled",
    })
    connection = _Connection(None if blocked == "missing" else record, active_lease=blocked == "lease")
    runner = _Runner()
    runtime = ComposeRuntime(_settings(tmp_path), EnvironmentSecretDeriver("s" * 32), runner=runner)
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError):
            await update_runtime(cast(psycopg.AsyncConnection[Mapping[str, object]], connection), runtime, client, record.id)
    assert runner.calls == []
    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize("capability", [{"supported": True, "version": 1}, {"supported": True, "version": True}])
async def test_maintenance_uses_shared_locks_and_strict_http_capability(tmp_path: Path, capability: dict[str, object]) -> None:
    from account_pool.runtime_maintenance import update_runtime

    record = _record()
    connection = _Connection(record)
    runner = _Runner(running=False)
    runtime = ComposeRuntime(_settings(tmp_path), EnvironmentSecretDeriver("s" * 32), runner=runner)
    previous = render_compose(record, runtime.settings).replace(_NEW_IMAGE, _OLD_IMAGE)
    compose_file = runtime.environment_dir(record.id) / "compose.yaml"
    compose_file.parent.mkdir()
    compose_file.write_text(previous, encoding="utf-8")
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert len(connection.statements) == 3
        assert connection.statements[0][0] == "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"
        assert connection.statements[0][1] == (str(record.id),)
        assert connection.statements[1][0].endswith("FOR UPDATE")
        assert "expires_at > now()" in connection.statements[2][0]
        return httpx.Response(200, json=capability) if request.headers.get("X-Management-Key") else httpx.Response(401)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        if type(capability["version"]) is bool:
            with pytest.raises(ValueError):
                await update_runtime(cast(psycopg.AsyncConnection[Mapping[str, object]], connection), runtime, client, record.id)
            assert compose_file.read_text(encoding="utf-8") == previous
        else:
            assert await update_runtime(
                cast(psycopg.AsyncConnection[Mapping[str, object]], connection), runtime, client, record.id
            ) is True
    assert [request.headers.get("X-Management-Key") is not None for request in requests] == [False, True]
    assert all(request.method == "GET" for request in requests)
    assert runner.running is False
    assert connection.record is record


def test_maintenance_failure_does_not_claim_an_enabled_card_was_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import account_pool.runtime_maintenance as maintenance

    async def rejected(*_arguments: object) -> None:
        raise ValueError("runtime update requires a disabled card; secret=do-not-print")

    monkeypatch.setattr(maintenance, "_run", rejected)
    monkeypatch.setattr("sys.argv", ["runtime_maintenance", str(_record().id), "--image", _NEW_IMAGE])
    with pytest.raises(SystemExit) as error:
        maintenance.main()
    assert "card remains disabled" not in str(error.value)
    assert "no enable action was performed" in str(error.value)
    assert "do-not-print" not in str(error.value)
