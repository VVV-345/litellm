from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import Callable
from uuid import UUID

import pytest
from pydantic import SecretStr

from litellm.proxy.management_endpoints.account_pool_cockpit_receipt_contracts import (
    CredentialBindings,
    CredentialReceipt,
    CredentialReceiptRejection,
    TrustedCredentialScope,
)

MODULE = "litellm.proxy.management_endpoints.account_pool_cockpit_credential_service"
SUBJECT = "a" * 64
KEY = SecretStr("OFFLINE-RECEIPT-SIGNING-KEY-ONLY-32")
REVISION = '"sha256-' + "b" * 64 + '"'


def bindings() -> CredentialBindings:
    return CredentialBindings(
        scope=TrustedCredentialScope(
            subject_hash=SUBJECT, card_id=UUID(int=1), model_scope="restricted", models=("fixture-model",)
        ),
        source_version=1,
        source_file="fixture.json",
        source_state_digest="c" * 64,
        source_fingerprint_digest="d" * 64,
        source_content_digest="e" * 64,
        source_size=123,
        target_account_id=UUID(int=1),
        target_auth_id="fixture.json",
        target_file_revision=REVISION,
        manifest_revision=REVISION,
    )


class Observations:
    def __init__(self, values: tuple[CredentialBindings | CredentialReceiptRejection | Exception, ...] | None = None):
        self.values = iter(values or (bindings(),))
        self.last: CredentialBindings | CredentialReceiptRejection | Exception = bindings()
        self.calls: list[str] = []

    async def read(self, subject_hash: str) -> CredentialBindings | CredentialReceiptRejection:
        self.calls.append(subject_hash)
        self.last = next(self.values, self.last)
        if isinstance(self.last, Exception):
            raise self.last
        return self.last


def service(observations: Observations | None = None, clock: Callable[[], int] = lambda: 1000, key: SecretStr = KEY):
    module = importlib.import_module(MODULE)
    return module.CockpitCredentialReceiptService(observations, key, clock)


def test_native_receipt_module_exists():
    assert importlib.util.find_spec(MODULE) is not None


def test_signed_receipt_requires_two_equal_observations_and_rechecks_each_use():
    observations = Observations()
    core = service(observations)
    receipt = asyncio.run(core.issue(SUBJECT))
    assert isinstance(receipt, CredentialReceipt)
    assert (receipt.payload.issued_at, receipt.payload.expires_at) == (1000, 1300)
    assert receipt.ready_to_migrate is False
    assert observations.calls == [SUBJECT, SUBJECT]
    assert asyncio.run(core.revalidate(SUBJECT, receipt.model_dump_json())) == receipt
    assert observations.calls == [SUBJECT] * 4
    assert KEY.get_secret_value() not in receipt.model_dump_json()


@pytest.mark.parametrize("subject", ["", "a" * 63, "A" * 64, "a" * 64 + "\n", "not-a-hash"])
def test_invalid_subject_never_observes(subject: str):
    observations = Observations()
    core = service(observations)
    assert asyncio.run(core.issue(subject)) == CredentialReceiptRejection("invalid_subject")
    assert observations.calls == []


def test_missing_trusted_observer_keeps_issuance_closed():
    assert asyncio.run(service().issue(SUBJECT)) == CredentialReceiptRejection("read_unavailable")


@pytest.mark.parametrize("change", ["signature", "subject", "model", "purpose", "version", "lifetime"])
def test_tampered_receipt_is_rejected_before_reading_live_state(change: str):
    signed = asyncio.run(service(Observations()).issue(SUBJECT))
    assert isinstance(signed, CredentialReceipt)
    data = json.loads(signed.model_dump_json())
    if change == "signature":
        data["signature"] = "0" * 64
    elif change == "subject":
        data["payload"]["bindings"]["scope"]["subject_hash"] = "f" * 64
    elif change == "model":
        data["payload"]["bindings"]["scope"]["models"] = ["another-model"]
    elif change == "purpose":
        data["payload"]["purpose"] = "authorization"
    elif change == "version":
        data["payload"]["version"] = 2
    else:
        data["payload"]["expires_at"] = 1400
    observations = Observations()
    result = asyncio.run(service(observations).revalidate(SUBJECT, json.dumps(data)))
    assert result == CredentialReceiptRejection("invalid_receipt")
    assert observations.calls == []


def test_key_rotation_and_cross_subject_use_reject_existing_receipts():
    signed = asyncio.run(service(Observations()).issue(SUBJECT))
    assert isinstance(signed, CredentialReceipt)
    assert asyncio.run(
        service(Observations(), key=SecretStr("ROTATED-KEY-ONLY-32-CHARACTERS-LONG")).revalidate(
            SUBJECT, signed.model_dump_json()
        )
    ) == CredentialReceiptRejection("invalid_receipt")
    assert asyncio.run(
        service(Observations()).revalidate("f" * 64, signed.model_dump_json())
    ) == CredentialReceiptRejection("invalid_receipt")


@pytest.mark.parametrize("now", [999, 1300, True, 1000.5, float("nan")])
def test_time_window_uses_strict_integer_clock(now):
    signed = asyncio.run(service(Observations()).issue(SUBJECT))
    assert isinstance(signed, CredentialReceipt)
    observed = Observations()
    assert asyncio.run(
        service(observed, lambda: now).revalidate(SUBJECT, signed.model_dump_json())
    ) == CredentialReceiptRejection("receipt_time_invalid")
    assert observed.calls == []


@pytest.mark.parametrize("now", [0, -1, True, 1000.5])
def test_invalid_issue_clock_is_rejected_before_observation(now):
    observed = Observations()
    assert asyncio.run(service(observed, lambda: now).issue(SUBJECT)) == CredentialReceiptRejection("invalid_clock")
    assert observed.calls == []


def test_clock_failure_is_sanitized():
    def unavailable():
        raise RuntimeError("SECRET-CLOCK-ERROR")

    result = asyncio.run(service(Observations(), unavailable).issue(SUBJECT))
    assert result == CredentialReceiptRejection("invalid_clock")
    assert "SECRET" not in repr(result)


def test_expiry_during_observation_is_rejected():
    clocks = iter((1000, 1300))
    assert asyncio.run(service(Observations(), lambda: next(clocks)).issue(SUBJECT)) == CredentialReceiptRejection(
        "receipt_time_invalid"
    )
    signed = asyncio.run(service(Observations()).issue(SUBJECT))
    assert isinstance(signed, CredentialReceipt)
    clocks = iter((1299, 1300))
    assert asyncio.run(
        service(Observations(), lambda: next(clocks)).revalidate(SUBJECT, signed.model_dump_json())
    ) == CredentialReceiptRejection("receipt_time_invalid")


@pytest.mark.parametrize(
    "field,value",
    [("source_version", 2), ("source_content_digest", "f" * 64), ("manifest_revision", '"sha256-' + "f" * 64 + '"')],
)
def test_changed_observation_invalidates_receipt(field, value):
    signed = asyncio.run(service(Observations()).issue(SUBJECT))
    assert isinstance(signed, CredentialReceipt)
    changed = bindings().model_copy(update={field: value})
    assert asyncio.run(
        service(Observations((changed,))).revalidate(SUBJECT, signed.model_dump_json())
    ) == CredentialReceiptRejection("stale_receipt")
    assert asyncio.run(service(Observations((bindings(), changed))).issue(SUBJECT)) == CredentialReceiptRejection(
        "state_changed"
    )


@pytest.mark.parametrize(
    "observation,code",
    [
        (CredentialReceiptRejection("subject_unverified"), "subject_unverified"),
        (RuntimeError("SECRET-UPSTREAM-ERROR"), "read_unavailable"),
    ],
)
def test_failed_trusted_observation_never_issues(observation, code):
    assert asyncio.run(service(Observations((observation,))).issue(SUBJECT)) == CredentialReceiptRejection(code)


def test_observation_cannot_bind_another_subject_or_account():
    wrong_subject = bindings().model_copy(
        update={"scope": bindings().scope.model_copy(update={"subject_hash": "f" * 64})}
    )
    wrong_account = bindings().model_copy(update={"target_account_id": UUID(int=2)})
    for value in (wrong_subject, wrong_account):
        assert asyncio.run(service(Observations((value,))).issue(SUBJECT)) == CredentialReceiptRejection(
            "subject_unverified"
        )


def test_empty_or_short_signing_key_is_not_accepted():
    for value in ("", "short"):
        with pytest.raises(ValueError, match="signing key"):
            service(Observations(), key=SecretStr(value))
