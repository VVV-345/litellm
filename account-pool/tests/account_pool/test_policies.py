from __future__ import annotations

from typing import Final

from account_pool.domain import SupplierKind
from account_pool.policies import AccountPolicy, TransportPolicy, persisted_policy_payload, policy_capabilities


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


def test_provider_settings_capability_matches_supplier_policy_fields() -> None:
    expected: Final = {
        SupplierKind.OPENAI_CODEX: "gateway",
        SupplierKind.ANTHROPIC_CLAUDE: "gateway",
        SupplierKind.KIMI: "gateway",
        SupplierKind.XAI: "gateway",
        SupplierKind.GOOGLE_ANTIGRAVITY: "gateway",
        SupplierKind.OPENAI_COMPATIBLE: "unsupported",
        SupplierKind.GEMINI: "unsupported",
        SupplierKind.GEMINI_INTERACTIONS: "unsupported",
        SupplierKind.VERTEX: "unsupported",
    }

    for supplier, status in expected.items():
        capability = next(item for item in policy_capabilities(supplier) if item.name == "provider_settings")
        assert capability.status == status

    assert next(item for item in policy_capabilities() if item.name == "provider_settings").status == "unsupported"
