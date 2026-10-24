"""验证 Manager 凭据回执接口的鉴权、输入和固定错误边界。"""

from __future__ import annotations

from typing import Final

import pytest
from account_pool.api import CredentialReceiptOperations, create_router
from account_pool.cockpit_credential_receipts import CredentialReceipt, CredentialReceiptRejection
from account_pool.service import EnvironmentService
from fastapi import FastAPI
from fastapi.testclient import TestClient

TOKEN: Final = "m" * 32
SUBJECT: Final = "a" * 64
_DEFAULT_REJECTION: Final = CredentialReceiptRejection("subject_unverified")


class _ReceiptService:
    def __init__(
        self,
        result: CredentialReceipt | CredentialReceiptRejection | Exception = _DEFAULT_REJECTION,
    ) -> None:
        self.result = result
        self.calls: list[tuple[str, str, str | None]] = []

    async def issue(self, subject_hash: str) -> CredentialReceipt | CredentialReceiptRejection:
        self.calls.append(("issue", subject_hash, None))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    async def revalidate(self, subject_hash: str, encoded: str) -> CredentialReceipt | CredentialReceiptRejection:
        self.calls.append(("revalidate", subject_hash, encoded))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _app(receipts: CredentialReceiptOperations | None = None) -> FastAPI:
    app: Final = FastAPI()
    app.include_router(create_router(EnvironmentService.__new__(EnvironmentService), TOKEN, credential_receipts=receipts))
    return app


def test_manager_receipt_routes_are_disabled_and_redact_auth_input_errors() -> None:
    app: Final = _app()
    with TestClient(app) as client:
        missing_auth: Final = client.post("/api/cockpit/credential-receipts", json={"key_hash": SUBJECT})
        invalid_input: Final = client.post(
            "/api/cockpit/credential-receipts",
            headers={"Authorization": "Bearer " + TOKEN},
            json={"key_hash": SUBJECT, "credential": "PRIVATE"},
        )
        disabled: Final = client.post(
            "/api/cockpit/credential-receipts",
            headers={"Authorization": "Bearer " + TOKEN},
            json={"key_hash": SUBJECT},
        )
    assert missing_auth.status_code == 401
    assert invalid_input.status_code == 422 and "PRIVATE" not in invalid_input.text
    assert disabled.status_code == 503
    assert all(response.headers["cache-control"] == "no-store" for response in (missing_auth, invalid_input, disabled))


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/cockpit/credential-receipts", {"key_hash": "A" * 64}),
        ("/api/cockpit/credential-receipts/revalidate", {"key_hash": SUBJECT, "receipt": "PRIVATE" * 20_000}),
    ],
)
def test_manager_receipt_routes_reject_untrusted_shape_without_calling_service(path: str, body: dict[str, str]) -> None:
    service: Final = _ReceiptService()
    app: Final = _app(service)
    with TestClient(app) as client:
        response: Final = client.post(path, headers={"Authorization": "Bearer " + TOKEN}, json=body)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert service.calls == []
    assert "PRIVATE" not in response.text


def test_manager_receipt_routes_return_typed_rejection_and_sanitize_failures() -> None:
    service: Final = _ReceiptService()
    app: Final = _app(service)
    with TestClient(app) as client:
        rejected: Final = client.post(
            "/api/cockpit/credential-receipts",
            headers={"Authorization": "Bearer " + TOKEN},
            json={"key_hash": SUBJECT},
        )
    assert rejected.status_code == 200
    assert rejected.json() == {
        "key_hash": SUBJECT,
        "status": "rejected",
        "ready_to_migrate": False,
        "receipt": None,
        "code": "subject_unverified",
    }
    assert rejected.headers["cache-control"] == "no-store"
    assert service.calls == [("issue", SUBJECT, None)]

    failing: Final = _ReceiptService(RuntimeError("PRIVATE-CREDENTIAL"))
    with TestClient(_app(failing)) as client:
        response: Final = client.post(
            "/api/cockpit/credential-receipts/revalidate",
            headers={"Authorization": "Bearer " + TOKEN},
            json={"key_hash": SUBJECT, "receipt": "opaque-receipt"},
        )
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "PRIVATE" not in response.text


def test_cockpit_status_does_not_invoke_receipt_service() -> None:
    service: Final = _ReceiptService()
    with TestClient(_app(service)) as client:
        response: Final = client.get("/api/cockpit/status", headers={"Authorization": "Bearer " + TOKEN})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert service.calls == []
