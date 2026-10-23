from __future__ import annotations

from account_pool.policies import AccountPolicy, TransportPolicy, persisted_policy_payload


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
