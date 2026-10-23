"""离线验证原生 Key 的单卡上界，禁止缓存、撤销复活和复杂权限推断。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Final, Literal
from uuid import UUID

import pytest
from pydantic import ValidationError

from litellm.proxy.management_endpoints.account_pool_cockpit_subject import (
    NativeSubjectResult,
    WriterNativeSubjectRepository,
    inspect_native_subject,
    inspect_native_subject_with_repository,
)

KEY: Final = "a" * 64
CARD: Final = UUID("e515c2db-08dc-443c-b85a-7936d2d42f2f")
NOW: Final = datetime(2026, 10, 23, tzinfo=timezone.utc)


def record(**changes: object) -> Mapping[str, object]:
    return {
        "token": KEY, "blocked": None, "expires": None,
        "metadata": {"account_pool_card_id": str(CARD)},
        "models": [], "aliases": {}, "config": {}, "permissions": {},
        "router_settings": None, "access_group_ids": [], "policies": [],
        "allowed_routes": [], "allowed_cache_controls": [], "key_type": None,
        "model_max_budget": {}, "budget_fallbacks": {}, "budget_limits": None,
        "user_id": None, "team_id": None, "project_id": None, "organization_id": None,
        "agent_id": None, "object_permission_id": None, "budget_id": None,
        **changes,
    }


@dataclass(frozen=True)
class Repository:
    live: object | None
    deleted: object | None = None
    failure: Literal["live", "deleted"] | None = None

    async def find_live(self, key_hash: str) -> object | None:
        assert key_hash == KEY
        assert self.deleted is None, "Revocation must be checked before reading live state"
        if self.failure == "live":
            raise RuntimeError("private database error")
        return self.live

    async def find_deleted(self, key_hash: str) -> object | None:
        assert key_hash == KEY
        if self.failure == "deleted":
            raise RuntimeError("private database error")
        return self.deleted


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ("sk-plaintext", "A" * 64, "a" * 63, "a" * 65, KEY + "\n"))
async def test_invalid_hash_is_rejected_before_repository_access(key: str) -> None:
    result: Final = await inspect_native_subject_with_repository(key, Repository(record()), now=NOW)
    assert result.status == "invalid_record"
    assert result.card_id is None


@pytest.mark.asyncio
async def test_only_active_unrestricted_native_single_card_is_verified() -> None:
    result: Final = await inspect_native_subject_with_repository(KEY, Repository(record()), now=NOW)
    assert result.status == "verified"
    assert result.card_id == CARD
    assert result.checked_at == NOW
    assert set(result.model_dump()) == {"status", "card_id", "checked_at"}
    assert KEY not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "repository,expected",
    (
        (Repository(None), "missing"),
        (Repository(record(), {"token": KEY}), "revoked"),
        (Repository(None, {"token": KEY}), "revoked"),
        (Repository(record(), failure="live"), "unavailable"),
        (Repository(record(), failure="deleted"), "unavailable"),
        (Repository(record(blocked=True)), "blocked"),
        (Repository(record(expires=NOW)), "expired"),
        (Repository(record(expires=NOW - timedelta(seconds=1))), "expired"),
        (Repository(record(token="b" * 64)), "invalid_record"),
        (Repository(record(blocked="false")), "invalid_record"),
        (Repository(record(expires="nonsense")), "invalid_record"),
        (Repository(record(expires=123)), "invalid_record"),
        (Repository(record(metadata=None)), "invalid_record"),
        (Repository(record(metadata="not-json")), "invalid_record"),
        (Repository(record(metadata=[])), "invalid_record"),
        (Repository(record(metadata={})), "card_scope_unverified"),
        (Repository(record(metadata={"account_pool_card_id": "bad"})), "invalid_record"),
        (Repository(record(metadata={"account_pool_card_id": [str(CARD)]})), "invalid_record"),
        (Repository(record(metadata={"account_pool_card_id": str(CARD), "account_pool_binding_id": str(CARD)})), "legacy_subject"),
        (Repository(record(metadata={"account_pool_binding_id": str(CARD)})), "legacy_subject"),
    ),
)
async def test_fail_closed_status_never_exposes_card_scope(repository: Repository, expected: str) -> None:
    result: Final = await inspect_native_subject_with_repository(KEY, repository, now=NOW)
    assert result.status == expected
    assert result.card_id is None
    assert result.checked_at == NOW
    assert "private database error" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    (
        ("models", ["restricted-model"]), ("models", ["all-proxy-models"]),
        ("models", None), ("models", [7]),
        ("aliases", {"alias": "restricted-model"}), ("config", {"model_list": []}),
        ("permissions", {"allowed": False}), ("router_settings", {"fallbacks": []}),
        ("access_group_ids", ["group"]), ("allowed_routes", ["/chat/completions"]),
        ("policies", ["policy"]), ("model_max_budget", {"restricted-model": 1}),
        ("budget_fallbacks", {"restricted-model": ["fallback"]}),
        ("budget_limits", [{"budget_amount": 1}]), ("key_type", "management"),
        ("user_id", "owner"), ("team_id", "owner"), ("project_id", "owner"),
        ("organization_id", "owner"), ("agent_id", "owner"),
        ("object_permission_id", "owner"), ("budget_id", "owner"),
        ("user_id", ""),
        ("metadata", {"account_pool_card_id": str(CARD), "allowed_passthrough_routes": ["/private"]}),
    ),
)
async def test_complex_or_incomplete_permissions_never_expand_scope(field: str, value: object) -> None:
    result: Final = await inspect_native_subject_with_repository(KEY, Repository(record(**{field: value})), now=NOW)
    assert result.status == "model_scope_unverified"
    assert result.card_id is None


@pytest.mark.asyncio
async def test_missing_permission_field_is_not_assumed_unrestricted() -> None:
    incomplete: Final = {key: value for key, value in record().items() if key != "models"}
    result: Final = await inspect_native_subject_with_repository(KEY, Repository(incomplete), now=NOW)
    assert result.status == "model_scope_unverified"
    assert result.card_id is None


@pytest.mark.asyncio
async def test_writer_json_fields_and_future_expiry_are_supported() -> None:
    row: Final = record(
        metadata='{"account_pool_card_id":"e515c2db-08dc-443c-b85a-7936d2d42f2f"}',
        aliases="{}", config="{}", permissions="{}", router_settings="{}",
        model_max_budget="{}", budget_fallbacks="{}", expires="2026-10-24T00:00:00Z",
    )
    result: Final = await inspect_native_subject_with_repository(KEY, Repository(row), now=NOW)
    assert result.status == "verified"
    assert result.card_id == CARD


@dataclass(frozen=True)
class WriterDatabase:
    revoked: bool = False

    async def query_raw(self, query: str, *args: object) -> Sequence[Mapping[str, object]]:
        assert args == (KEY,)
        assert "$1" in query and KEY not in query
        assert query.startswith("SELECT ") and "LIMIT 1" in query
        if query.startswith("SELECT token "):
            assert '"LiteLLM_DeletedVerificationToken"' in query
            return ({"token": KEY},) if self.revoked else ()
        assert '"LiteLLM_VerificationToken"' in query
        assert 'NOT EXISTS (SELECT 1 FROM "LiteLLM_DeletedVerificationToken" WHERE token = $1)' in query
        assert not self.revoked
        return (record(),)


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked,expected", ((False, "verified"), (True, "revoked")))
async def test_writer_repository_uses_bounded_parameterized_reads(revoked: bool, expected: str) -> None:
    repository: Final = WriterNativeSubjectRepository(WriterDatabase(revoked=revoked))
    result: Final = await inspect_native_subject_with_repository(KEY, repository, now=NOW)
    assert result.status == expected
    assert result.card_id == (None if revoked else CARD)


def test_result_cannot_expose_unverified_scope_or_verify_without_card() -> None:
    with pytest.raises(ValidationError):
        NativeSubjectResult(status="revoked", card_id=CARD, checked_at=NOW)
    with pytest.raises(ValidationError):
        NativeSubjectResult(status="verified", checked_at=NOW)


@dataclass(frozen=True)
class PrismaClientFixture:
    writer_db: WriterDatabase

    @property
    def db(self) -> object:
        raise AssertionError("Default database may be replica-routed")

    @property
    def read_db(self) -> object:
        raise AssertionError("Replica cannot determine revocation")


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked,expected", ((False, "verified"), (True, "revoked")))
async def test_runtime_entry_reads_writer_not_replica(
    monkeypatch: pytest.MonkeyPatch, revoked: bool, expected: str
) -> None:
    from litellm.proxy import proxy_server

    monkeypatch.setattr(proxy_server, "prisma_client", PrismaClientFixture(WriterDatabase(revoked)))
    result: Final = await inspect_native_subject(KEY)
    assert result.status == expected
    assert result.card_id == (None if revoked else CARD)
    assert result.checked_at.utcoffset() == timedelta(0)


@pytest.mark.asyncio
async def test_runtime_without_database_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from litellm.proxy import proxy_server

    monkeypatch.setattr(proxy_server, "prisma_client", None)
    result: Final = await inspect_native_subject(KEY)
    assert result.status == "unavailable"
    assert result.card_id is None
