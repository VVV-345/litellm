import asyncio
import importlib
import json
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from litellm.proxy.management_endpoints.account_pool_cockpit_admin import CockpitAdminClient, CockpitAdminConfig
from litellm.proxy.management_endpoints.account_pool_cockpit_receipt_contracts import CredentialReceiptRejection
from litellm.proxy.management_endpoints.account_pool_cockpit_subject_reader import TrustedCredentialScope

SUBJECT = "a" * 64
REVISION = '"sha256-' + "b" * 64 + '"'


class Subjects:
    def __init__(self, revoked=False):
        self.revoked = revoked

    async def read(self, subject_hash):
        if self.revoked:
            return None
        return TrustedCredentialScope(
            subject_hash=subject_hash, card_id=UUID(int=1), model_scope="restricted", models=("fixture-model",)
        )


def manifest():
    return {
        "accounts": [{"id": str(UUID(int=1)), "authId": "fixture.json", "authKind": "oauth"}],
        "modelIds": ["fixture-model"],
    }


async def issue(data=None, files=None, revoked=False, status=200):
    from litellm.proxy.management_endpoints.account_pool_cockpit_credential_service import (
        CockpitCredentialReceiptService,
    )
    from litellm.proxy.management_endpoints.account_pool_cockpit_native_observation import NativeCredentialObservation

    def handle(request):
        assert request.url.host == "cockpit.test"
        assert request.method == "GET"
        if request.url.path == "/admin/v1/manifest":
            return httpx.Response(status, json=manifest() if data is None else data, headers={"ETag": REVISION})
        assert request.url.path == "/admin/v1/auth-files"
        return httpx.Response(
            200, json={"files": files if files is not None else [{"id": "fixture.json", "etag": REVISION, "size": 123}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        target = CockpitAdminClient(
            CockpitAdminConfig(url="http://cockpit.test", token=SecretStr("ADMIN-TOKEN-ONLY-32-CHARACTERS-LONG")), http
        )
        observations = NativeCredentialObservation(Subjects(revoked), target)
        return await CockpitCredentialReceiptService(
            observations, SecretStr("RECEIPT-KEY-ONLY-32-CHARACTERS-LONG"), lambda: 1000
        ).issue(SUBJECT)


def test_native_observation_issues_distinct_scope_receipt_without_legacy_source_fields():
    receipt = asyncio.run(issue())
    contracts = importlib.import_module("litellm.proxy.management_endpoints.account_pool_cockpit_receipt_contracts")
    assert isinstance(receipt, contracts.NativeCredentialReceipt)
    data = json.loads(receipt.model_dump_json())
    assert data["status"] == "native_scope_verified"
    assert data["payload"]["purpose"] == "litellm-cockpit-native-scope"
    assert not any(field.startswith("source_") for field in data["payload"]["bindings"])
    assert data["payload"]["bindings"]["target_account_id"] == str(UUID(int=1))
    assert data["payload"]["bindings"]["target_file_revision"] == REVISION
    assert data["ready_to_migrate"] is False


@pytest.mark.parametrize(
    "change,code",
    [
        ("missing", "target_account_missing"),
        ("duplicate", "target_account_collision"),
        ("auth_alias", "target_credential_collision"),
        ("api_key", "target_auth_unsupported"),
        ("case_alias", "target_auth_unsupported"),
        ("model", "subject_unverified"),
    ],
)
def test_native_observation_rejects_ambiguous_or_incompatible_target(change, code):
    data = manifest()
    if change == "missing":
        data["accounts"] = []
    elif change == "duplicate":
        data["accounts"] *= 2
    elif change == "auth_alias":
        data["accounts"].append({"id": str(UUID(int=2)), "authId": "fixture", "authKind": "oauth"})
    elif change == "api_key":
        data["accounts"][0]["upstreamApiKey"] = "SECRET"
    elif change == "case_alias":
        data["accounts"][0]["AUTHKIND"] = "apikey"
    else:
        data["modelIds"] = []
    assert asyncio.run(issue(data)) == CredentialReceiptRejection(code)


def test_missing_revoked_and_unavailable_observations_fail_closed():
    assert asyncio.run(issue(revoked=True)) == CredentialReceiptRejection("subject_unverified")
    assert asyncio.run(issue(files=[])) == CredentialReceiptRejection("target_credential_missing")
    assert asyncio.run(issue(status=503)) == CredentialReceiptRejection("read_unavailable")
    assert asyncio.run(
        issue(files=[{"id": "fixture.json", "etag": REVISION, "size": 0}])
    ) == CredentialReceiptRejection("target_credential_missing")


@pytest.mark.parametrize(
    "section,alias,value",
    [
        ("accountModelRules", "EXCLUDEDMODELS", {"accountId": str(UUID(int=1)), "excludedModels": []}),
        ("modelAliases", "SOURCEMODEL", {"sourceModel": "fixture-model", "alias": "alias"}),
        ("apiKeys", "ACCOUNTIDS", {"id": "unrelated", "enabled": False}),
    ],
)
def test_native_observation_rejects_nested_case_aliases(section, alias, value):
    data = manifest()
    data[section] = [{**value, alias: ["fixture-model"]}]
    assert asyncio.run(issue(data)) == CredentialReceiptRejection("target_auth_unsupported")
