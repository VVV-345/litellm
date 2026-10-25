from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Final, Literal, Protocol, cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from litellm.proxy.management_endpoints.account_pool_cockpit_subject import (
    NativeModelScope,
    NativeSubjectResult,
    NativeSubjectStatus,
    inspect_native_subject_with_repository,
)

TARGET_MODULE: Final = "litellm.proxy.management_endpoints.account_pool_cockpit_subject_reader"
KEY: Final = "a" * 64
CARD: Final = UUID("e515c2db-08dc-443c-b85a-7936d2d42f2f")
NOW: Final = datetime(2026, 10, 23, tzinfo=timezone.utc)
TEAM: Final = "team-primary"
SECRET: Final = "private-native-error-token"
REPO_ROOT: Final = Path(__file__).parents[4]
NON_VERIFIED: Final[tuple[NativeSubjectStatus, ...]] = (
    "missing",
    "revoked",
    "blocked",
    "expired",
    "legacy_subject",
    "card_scope_unverified",
    "model_scope_unverified",
    "invalid_record",
    "unavailable",
)
INVALID_HASHES: Final[tuple[str, ...]] = (
    "",
    "a" * 63,
    "a" * 65,
    "A" * 64,
    "g" * 64,
    KEY + "\n",
    "sk-fixture-plaintext",
)


class _ScopeDTO(Protocol):
    subject_hash: str
    card_id: UUID
    model_scope: Literal["restricted", "unrestricted"]
    models: tuple[str, ...]


class _Reader(Protocol):
    async def read(self, subject_hash: str) -> _ScopeDTO | None: ...


def _reader(module: ModuleType, inspect_subject: Callable[[str], Awaitable[NativeSubjectResult]]) -> _Reader:
    return cast(_Reader, getattr(module, "NativeTrustedSubjectReader")(inspect_subject))


def _scope_type(module: ModuleType) -> Callable[..., _ScopeDTO]:
    return cast(Callable[..., _ScopeDTO], getattr(module, "TrustedCredentialScope"))


def _verified(models: tuple[str, ...]) -> NativeSubjectResult:
    kind: Final[Literal["restricted", "unrestricted"]] = "restricted" if models else "unrestricted"
    return NativeSubjectResult(
        status="verified",
        card_id=CARD,
        model_scope=NativeModelScope(kind=kind, models=models),
        checked_at=NOW,
    )


def _status(status: NativeSubjectStatus) -> NativeSubjectResult:
    return NativeSubjectResult(status=status, checked_at=NOW)


def test_reader_module_exists() -> None:
    assert importlib.util.find_spec(TARGET_MODULE) is not None


@pytest.fixture(scope="module")
def reader_module() -> ModuleType:
    return importlib.import_module(TARGET_MODULE)


@pytest.mark.parametrize(
    ("native_models", "scope", "models"),
    [
        (("model-alpha", "model-beta"), "restricted", ("model-alpha", "model-beta")),
        ((), "unrestricted", ()),
    ],
)
@pytest.mark.asyncio
async def test_verified_scope_maps_exactly(
    reader_module: ModuleType,
    native_models: tuple[str, ...],
    scope: Literal["restricted", "unrestricted"],
    models: tuple[str, ...],
) -> None:
    calls: Final[list[str]] = []

    async def inspect_subject(subject_hash: str) -> NativeSubjectResult:
        calls.append(subject_hash)
        return _verified(native_models)

    actual = await _reader(reader_module, inspect_subject).read(KEY)
    assert actual is not None
    assert type(actual) is reader_module.TrustedCredentialScope
    assert (actual.subject_hash, actual.card_id, actual.model_scope, actual.models) == (KEY, CARD, scope, models)
    assert calls == [KEY]


@pytest.mark.parametrize("status", NON_VERIFIED)
@pytest.mark.asyncio
async def test_nonverified_statuses_are_rejected(reader_module: ModuleType, status: NativeSubjectStatus) -> None:
    calls: Final[list[str]] = []

    async def inspect_subject(subject_hash: str) -> NativeSubjectResult:
        calls.append(subject_hash)
        return _status(status)

    assert await _reader(reader_module, inspect_subject).read(KEY) is None
    assert calls == [KEY]


@pytest.mark.parametrize("subject_hash", INVALID_HASHES)
@pytest.mark.asyncio
async def test_invalid_hash_is_rejected_before_inspection(reader_module: ModuleType, subject_hash: str) -> None:
    calls: Final[list[str]] = []

    async def inspect_subject(candidate: str) -> NativeSubjectResult:
        calls.append(candidate)
        return _verified(("model-alpha",))

    assert await _reader(reader_module, inspect_subject).read(subject_hash) is None
    assert calls == []


@pytest.mark.asyncio
async def test_each_read_is_fresh_and_revocation_wins(reader_module: ModuleType) -> None:
    calls: Final[list[str]] = []
    replies: Final[list[NativeSubjectResult]] = [_verified(("model-alpha",)), _status("revoked")]

    async def inspect_subject(subject_hash: str) -> NativeSubjectResult:
        calls.append(subject_hash)
        return replies.pop(0)

    reader = _reader(reader_module, inspect_subject)
    actual = await reader.read(KEY)
    assert actual is not None
    assert (actual.subject_hash, actual.card_id, actual.model_scope, actual.models) == (
        KEY,
        CARD,
        "restricted",
        ("model-alpha",),
    )
    assert await reader.read(KEY) is None
    assert calls == [KEY, KEY]


@pytest.mark.asyncio
async def test_inspector_exception_is_closed_without_secret_leak(
    reader_module: ModuleType, caplog: pytest.LogCaptureFixture
) -> None:
    calls: Final[list[str]] = []

    async def inspect_subject(subject_hash: str) -> NativeSubjectResult:
        calls.append(subject_hash)
        raise RuntimeError(SECRET)

    with caplog.at_level(logging.DEBUG):
        assert await _reader(reader_module, inspect_subject).read(KEY) is None
    assert calls == [KEY]
    assert SECRET not in caplog.text


@pytest.mark.asyncio
async def test_verified_duplicate_models_fails_closed(reader_module: ModuleType) -> None:
    native = _verified(("model-alpha", "model-alpha"))
    assert native.model_scope is not None

    async def inspect_subject(subject_hash: str) -> NativeSubjectResult:
        assert subject_hash == KEY
        return native

    assert await _reader(reader_module, inspect_subject).read(KEY) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"subject_hash": "A" * 64},
        {"subject_hash": "a" * 63},
        {"subject_hash": "a" * 65},
        {"subject_hash": "g" * 64},
        {"card_id": "not-a-uuid"},
        {"model_scope": "restricted", "models": ()},
        {"model_scope": "unrestricted", "models": ("model-alpha",)},
        {"models": ("model-alpha", "model-alpha")},
        {"models": ("all-team-models",)},
        {"models": ("all-proxy-models",)},
        {"models": ("no-default-models",)},
        {"models": ("*",)},
        {"unexpected": "value"},
    ],
)
def test_trusted_scope_rejects_invalid_legacy_cases(reader_module: ModuleType, overrides: dict[str, object]) -> None:
    values: Final[dict[str, object]] = {
        "subject_hash": KEY,
        "card_id": CARD,
        "model_scope": "restricted",
        "models": ("model-alpha",),
    }
    values.update(overrides)
    with pytest.raises(ValidationError):
        _scope_type(reader_module)(**values)


def test_trusted_scope_is_frozen(reader_module: ModuleType) -> None:
    scope = _scope_type(reader_module)(
        subject_hash=KEY, card_id=CARD, model_scope="restricted", models=("model-alpha",)
    )
    with pytest.raises(ValidationError):
        setattr(scope, "models", ())


def _record(**changes: object) -> Mapping[str, object]:
    values: Final[dict[str, object]] = {
        "token": KEY,
        "blocked": None,
        "expires": None,
        "metadata": {"account_pool_card_id": str(CARD)},
        "models": [],
        "aliases": {},
        "config": {},
        "permissions": {},
        "router_settings": None,
        "access_group_ids": [],
        "policies": [],
        "allowed_routes": [],
        "allowed_cache_controls": [],
        "key_type": None,
        "model_max_budget": {},
        "budget_fallbacks": {},
        "budget_limits": None,
        "user_id": None,
        "team_id": None,
        "project_id": None,
        "organization_id": None,
        "agent_id": None,
        "object_permission_id": None,
        "budget_id": None,
        "max_budget": None,
        "budget_duration": None,
        "budget_reset_at": None,
        "user_scope": None,
        "team_scope": None,
        "project_scope": None,
        "membership_scope": None,
    }
    values.update(changes)
    return values


@dataclass
class _FakeRepository:
    live: object | None
    deleted: object | None = None

    async def find_deleted(self, key_hash: str) -> object | None:
        assert key_hash == KEY
        return self.deleted

    async def find_live(self, key_hash: str) -> object | None:
        assert key_hash == KEY
        assert self.deleted is None
        return self.live


@pytest.mark.asyncio
async def test_real_inspector_narrows_then_revokes(reader_module: ModuleType) -> None:
    team_scope: Final[Mapping[str, object]] = {
        "subject_id": TEAM,
        "models": ["model-alpha", "model-delta"],
        "blocked": False,
        "permissions_unrestricted": True,
        "team_id": None,
    }
    repository: Final[_FakeRepository] = _FakeRepository(
        live=_record(
            team_id=TEAM,
            models=["model-alpha", "model-beta", "model-gamma"],
            team_scope=team_scope,
        )
    )

    async def inspect_subject(subject_hash: str) -> NativeSubjectResult:
        return await inspect_native_subject_with_repository(subject_hash, repository, now=NOW)

    reader = _reader(reader_module, inspect_subject)
    actual = await reader.read(KEY)
    assert actual is not None
    assert (actual.subject_hash, actual.card_id, actual.model_scope, actual.models) == (
        KEY,
        CARD,
        "restricted",
        ("model-alpha",),
    )
    repository.deleted = KEY
    assert await reader.read(KEY) is None


_SUBPROCESS_SCRIPT: Final = r"""
import asyncio
import importlib.abc
import sys
from datetime import datetime, timezone
from uuid import UUID

sys.path.insert(0, sys.argv[1])
BLOCKED = ('account_pool',)
ATTEMPTED = []

class BlockAccountPoolManager(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in BLOCKED):
            ATTEMPTED.append(fullname)
            raise ImportError(fullname)
        return None

sys.meta_path.insert(0, BlockAccountPoolManager())

from litellm.proxy.management_endpoints.account_pool_cockpit_subject import (
    NativeModelScope,
    NativeSubjectResult,
)
from litellm.proxy.management_endpoints.account_pool_cockpit_subject_reader import (
    NativeTrustedSubjectReader,
)

SUBJECT = 'a' * 64
CARD = UUID('e515c2db-08dc-443c-b85a-7936d2d42f2f')
NATIVE = NativeSubjectResult(
    status='verified',
    card_id=CARD,
    model_scope=NativeModelScope(kind='restricted', models=('model-alpha',)),
    checked_at=datetime(2026, 10, 23, tzinfo=timezone.utc),
)

async def inspect_subject(subject_hash: str) -> NativeSubjectResult:
    assert subject_hash == SUBJECT
    return NATIVE

async def main() -> None:
    scope = await NativeTrustedSubjectReader(inspect_subject).read(SUBJECT)
    assert scope is not None
    assert scope.subject_hash == SUBJECT
    assert scope.card_id == CARD
    assert scope.model_scope == 'restricted'
    assert scope.models == ('model-alpha',)
    print('NATIVE-SUBJECT-OK')

asyncio.run(main())
assert not ATTEMPTED
assert 'account_pool' not in sys.modules
"""


def test_isolated_subprocess_has_no_manager_dependency() -> None:
    completed = subprocess.run(
        [sys.executable, "-I", "-c", _SUBPROCESS_SCRIPT, str(REPO_ROOT)],
        cwd=REPO_ROOT,
        env={**os.environ, "LITELLM_LOCAL_MODEL_COST_MAP": "True"},
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "NATIVE-SUBJECT-OK"


@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed(reader_module: ModuleType) -> None:
    async def inspect_subject(subject_hash: str) -> NativeSubjectResult:
        assert subject_hash == KEY
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _reader(reader_module, inspect_subject).read(KEY)
