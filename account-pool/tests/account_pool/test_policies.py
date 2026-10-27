from __future__ import annotations

import pytest
from account_pool.domain import SupplierKind
from account_pool.policies import (
    AccountPolicy,
    CodexPolicy,
    TransportPolicy,
    persisted_policy_payload,
    policy_capabilities,
)


def test_managed_identity_mode_round_trips_without_changing_defaults() -> None:
    from account_pool.policies import CodexPolicy

    policy = AccountPolicy(codex=CodexPolicy(identity_fingerprint_mode="managed", identity_confuse=True))
    assert AccountPolicy.model_validate(persisted_policy_payload(policy)) == policy
    assert CodexPolicy().identity_fingerprint_mode == "off"
    assert CodexPolicy().identity_confuse is False


def test_persisted_policy_omits_default_read_timeout_and_keeps_explicit_value() -> None:
    default: dict[str, object] = persisted_policy_payload(AccountPolicy())
    explicit: dict[str, object] = persisted_policy_payload(
        AccountPolicy(transport=TransportPolicy(read_timeout_seconds=60))
    )

    assert "read_timeout_seconds" not in default["transport"]
    assert explicit["transport"]["read_timeout_seconds"] == 60


def test_app_server_originators_are_trimmed_and_rejected_when_unsafe() -> None:
    policy = CodexPolicy(allow_app_server_clients=(" Codex_App ", "codex_app", "desktop"))

    assert policy.allow_app_server_clients == ("codex_app", "desktop")
    with pytest.raises(ValueError, match="originators"):
        CodexPolicy(allow_app_server_clients=("has space",))
    assert CodexPolicy().identity_fingerprint_mode == "off"


def test_identity_capability_matches_suppliers_that_write_auth_metadata() -> None:
    written = (
        SupplierKind.OPENAI_CODEX,
        SupplierKind.ANTHROPIC_CLAUDE,
        SupplierKind.KIMI,
    )
    assert all(
        next(item.status for item in policy_capabilities(supplier) if item.name == "identity") == "metadata"
        for supplier in written
    )
    xai_identity = next(item.status for item in policy_capabilities(SupplierKind.XAI) if item.name == "identity")
    assert xai_identity == "unsupported"
