"""可信主体回调与凭据回执端点的边界测试。"""

from typing import Final
from uuid import UUID

import httpx
import pytest
from account_pool.cockpit_receipt_adapter import CockpitSubjectClient, CockpitSubjectConfig
from account_pool.config import Settings
from pydantic import SecretStr

SUBJECT: Final = "a" * 64
TOKEN: Final = "m" * 32
CARD: Final = str(UUID(int=1))


def test_subject_config_rejects_dynamic_or_credential_bearing_origins() -> None:
    for url in (
        "http://example.test/path", "http://user@example.test", "http://example.test?q=1",
        "http://example.test?", "http://example.test#", "http://example.test:",
        "http://example.test:0", "http://example..test", "http://example.test\\@evil.test",
    ):
        with pytest.raises(ValueError):
            CockpitSubjectConfig(url=url, token=SecretStr(TOKEN))


def test_subject_url_is_disabled_by_default_and_requires_cockpit_admin() -> None:
    assert Settings(
        database_url="postgresql://unused",
        manager_token=TOKEN,
        secret_seed="s" * 32,
        ssh_host="example.test",
        ssh_user="operator",
    ).cockpit_subject_url is None
    with pytest.raises(ValueError):
        Settings(
            database_url="postgresql://unused",
            manager_token=TOKEN,
            secret_seed="s" * 32,
            ssh_host="example.test",
            ssh_user="operator",
            cockpit_subject_url="http://manager.test",
        )
    configured: Final = Settings(
        database_url="postgresql://unused",
        manager_token=TOKEN,
        secret_seed="s" * 32,
        ssh_host="example.test",
        ssh_user="operator",
        cockpit_admin_url="http://cockpit.test",
        cockpit_admin_token=SecretStr("c" * 32),
        cockpit_subject_url="http://manager.test/",
    )
    assert configured.cockpit_subject_url == "http://manager.test"


@pytest.mark.asyncio
async def test_subject_client_requires_exact_subject_and_redacts_failures() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/account_pool/internal/cockpit/native-subject"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.content == b'{"subject_hash":"' + SUBJECT.encode() + b'"}'
        return httpx.Response(200, json={"subject_hash": SUBJECT, "scope": {
            "subject_hash": SUBJECT, "card_id": CARD, "model_scope": "restricted", "models": ["fixture-model"]
        }})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
        reader = CockpitSubjectClient(CockpitSubjectConfig(url="http://example.test", token=SecretStr(TOKEN)), client)
        scope = await reader.read(SUBJECT)
    assert scope is not None and scope.subject_hash == SUBJECT and scope.card_id == UUID(CARD)


@pytest.mark.asyncio
async def test_subject_client_rejects_wrong_echo_and_non_success() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"subject_hash": "b" * 64, "scope": None, "detail": "SECRET"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
        reader = CockpitSubjectClient(CockpitSubjectConfig(url="http://example.test", token=SecretStr(TOKEN)), client)
        assert await reader.read(SUBJECT) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"subject_hash": SUBJECT, "scope": None, "verified": True},
        {"subject_hash": SUBJECT, "scope": {
            "subject_hash": SUBJECT, "card_id": CARD, "model_scope": "unknown", "models": [],
        }},
        {"subject_hash": SUBJECT, "scope": {
            "subject_hash": SUBJECT, "card_id": CARD, "model_scope": "restricted", "models": [17],
        }},
    ],
)
async def test_subject_client_rejects_unexpected_or_coerced_scope_data(body: dict[str, object]) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as client:
        reader = CockpitSubjectClient(CockpitSubjectConfig(url="http://example.test", token=SecretStr(TOKEN)), client)
        assert await reader.read(SUBJECT) is None


@pytest.mark.asyncio
async def test_subject_client_never_follows_redirects_and_uses_a_bounded_timeout() -> None:
    requests: Final[list[httpx.Request]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(307, headers={"Location": "http://attacker.test/collect"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
        reader = CockpitSubjectClient(CockpitSubjectConfig(url="http://example.test", token=SecretStr(TOKEN)), client)
        assert await reader.read(SUBJECT) is None
    assert len(requests) == 1
    assert requests[0].url.host == "example.test"
    assert requests[0].extensions["timeout"]["read"] == 10.0

