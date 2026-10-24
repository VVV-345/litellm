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
        "max_budget": None, "budget_duration": None, "budget_reset_at": None,
        "user_scope": None, "team_scope": None, "project_scope": None, "membership_scope": None,
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
    assert set(result.model_dump()) == {"status", "card_id", "checked_at", "model_scope"}
    assert result.model_dump(mode="json")["model_scope"] == {"kind": "unrestricted", "models": []}
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
        ("models", ["all-proxy-models"]),
        ("models", None), ("models", [7]),
        ("aliases", {"alias": "restricted-model"}), ("config", {"model_list": []}),
        ("permissions", {"allowed": False}), ("router_settings", {"fallbacks": []}),
        ("access_group_ids", ["group"]), ("allowed_routes", ["/chat/completions"]),
        ("policies", ["policy"]), ("model_max_budget", {"restricted-model": 1}),
        ("budget_fallbacks", {"restricted-model": ["fallback"]}),
        ("budget_limits", [{"budget_amount": 1}]), ("key_type", "management"),
        ("max_budget", 0), ("budget_duration", "1d"), ("budget_reset_at", NOW),
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


def owner_scope(owner_id: str, **changes: object) -> Mapping[str, object]:
    return {
        "subject_id": owner_id, "models": [], "blocked": False,
        "permissions_unrestricted": True, "team_id": None, **changes,
    }


def membership_scope(**changes: object) -> Mapping[str, object]:
    return {"user_id": "user-1", "team_id": "team-1", "budget_id": None, **changes}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes,models",
    (
        ({"models": ["model-b", "model-a", "model-a"]}, ["model-a", "model-b"]),
        ({"user_id": "user-1", "user_scope": owner_scope("user-1", models=["model-a"]),
          "models": ["model-a", "model-b"]}, ["model-a"]),
        ({"team_id": "team-1", "team_scope": owner_scope("team-1", models=["model-a"]),
          "models": ["model-a", "model-b"]}, ["model-a"]),
        ({"project_id": "project-1", "project_scope": owner_scope("project-1", models=["model-a"]),
          "models": ["model-a", "model-b"]}, ["model-a"]),
        ({"user_id": "user-1", "user_scope": owner_scope("user-1", models=["personal-only"]),
          "team_id": "team-1", "team_scope": owner_scope("team-1", models=["model-a", "model-b"]),
          "project_id": "project-1", "project_scope": owner_scope("project-1", team_id="team-1", models=["model-b"]),
          "membership_scope": membership_scope(), "models": ["model-a", "model-b"]}, ["model-b"]),
    ),
)
async def test_explicit_native_model_scopes_remain_visible(changes: dict[str, object], models: list[str]) -> None:
    result: Final = await inspect_native_subject_with_repository(KEY, Repository(record(**changes)), now=NOW)
    assert result.status == "verified"
    assert result.card_id == CARD
    assert result.model_dump(mode="json")["model_scope"] == {"kind": "restricted", "models": models}
    assert "subject_id" not in result.model_dump_json()
    assert "user-1" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ("user", "team", "project"))
async def test_supported_linked_owner_without_model_limit_is_explicitly_unrestricted(kind: str) -> None:
    changes: Final = {f"{kind}_id": "owner-1", f"{kind}_scope": owner_scope("owner-1")}
    result: Final = await inspect_native_subject_with_repository(KEY, Repository(record(**changes)), now=NOW)
    assert result.status == "verified"
    assert result.model_dump(mode="json")["model_scope"] == {"kind": "unrestricted", "models": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ("user", "team", "project"))
@pytest.mark.parametrize("owner", (
    None, {}, owner_scope("wrong-owner"), owner_scope("owner-1", blocked=True),
    owner_scope("owner-1", blocked="false"), owner_scope("owner-1", permissions_unrestricted=False),
    owner_scope("owner-1", permissions_unrestricted=None), owner_scope("owner-1", models=None),
    owner_scope("owner-1", models=[7]), owner_scope("owner-1", team_id="different-team"),
))
async def test_owner_state_cannot_be_missing_malformed_blocked_or_inconsistent(kind: str, owner: object) -> None:
    changes: Final = {f"{kind}_id": "owner-1", f"{kind}_scope": owner}
    result: Final = await inspect_native_subject_with_repository(KEY, Repository(record(**changes)), now=NOW)
    assert result.status == "model_scope_unverified"
    assert result.card_id is None
    assert result.model_dump(mode="json").get("model_scope") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("membership", (
    None, {}, membership_scope(user_id="other"), membership_scope(team_id="other"),
    membership_scope(budget_id="budget-1"), membership_scope(budget_id=""),
))
async def test_team_member_policy_is_not_silently_discarded(membership: object) -> None:
    result: Final = await inspect_native_subject_with_repository(KEY, Repository(record(
        user_id="user-1", user_scope=owner_scope("user-1"),
        team_id="team-1", team_scope=owner_scope("team-1"), membership_scope=membership,
    )), now=NOW)
    assert result.status == "model_scope_unverified"
    assert result.card_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ("models", "user_scope", "team_scope", "project_scope"))
@pytest.mark.parametrize("models", (
    ["all-team-models"], ["all-proxy-models"], ["no-default-models"],
    ["model-a", "all-team-models"], ["all-proxy-models", "model-a"],
    ["*"], ["model-*"], ["model-?"], ["[model]"], [""], [" model-a"], ["model-a\n"],
))
async def test_dynamic_model_resolution_never_becomes_unrestricted(field: str, models: list[str]) -> None:
    changes: Final = {"models": models} if field == "models" else {
        field: owner_scope("owner-1", models=models), field.replace("_scope", "_id"): "owner-1",
    }
    result: Final = await inspect_native_subject_with_repository(KEY, Repository(record(**changes)), now=NOW)
    assert result.status == "model_scope_unverified"
    assert result.card_id is None
    assert result.model_dump(mode="json").get("model_scope") is None


@pytest.mark.asyncio
async def test_disjoint_models_cannot_be_encoded_as_unrestricted() -> None:
    result: Final = await inspect_native_subject_with_repository(KEY, Repository(record(
        models=["model-a"], team_id="team-1", team_scope=owner_scope("team-1", models=["model-b"]),
    )), now=NOW)
    assert result.status == "model_scope_unverified"
    assert result.card_id is None


@pytest.mark.asyncio
async def test_joined_writer_checks_current_references_without_owner_secrets() -> None:
    @dataclass(frozen=True)
    class JoinedDatabase:
        async def query_raw(self, query: str, *args: object) -> Sequence[Mapping[str, object]]:
            assert args == (KEY,)
            if query.startswith("SELECT token "):
                return ()
            assert "SELECT *" not in query and "to_jsonb(" not in query
            for table in ("LiteLLM_UserTable", "LiteLLM_TeamTable", "LiteLLM_ProjectTable", "LiteLLM_TeamMembership"):
                assert f'LEFT JOIN "{table}"' in query
            assert "password" not in query and "user_email" not in query and "secret" not in query
            assert "budget_id" in query and "policies" in query and "model_id" in query
            assert 'NOT EXISTS (SELECT 1 FROM "LiteLLM_DeletedVerificationToken" WHERE token = $1)' in query
            return (record(team_id="team-1", team_scope=owner_scope("team-1", models=["model-a"])),)

    result: Final = await inspect_native_subject_with_repository(
        KEY, WriterNativeSubjectRepository(JoinedDatabase()), now=NOW,
    )
    assert result.status == "verified"
    assert result.model_dump(mode="json")["model_scope"] == {"kind": "restricted", "models": ["model-a"]}


def test_verified_subject_requires_explicit_model_scope() -> None:
    with pytest.raises(ValidationError):
        NativeSubjectResult(status="verified", card_id=CARD, checked_at=NOW)


@pytest.mark.parametrize("scope", (
    {"kind": "restricted", "models": []}, {"kind": "unrestricted", "models": ["model-a"]},
    {"kind": "restricted", "models": ["*"]},
))
def test_result_rejects_ambiguous_model_scope(scope: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        NativeSubjectResult.model_validate({"status": "verified", "card_id": CARD, "checked_at": NOW, "model_scope": scope})
