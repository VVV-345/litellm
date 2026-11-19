"""验证只读收信、验证码来源及 RT 先保存后读信。"""

from datetime import timedelta

import httpx
import pytest
from account_pool.domain import utc_now
from account_pool.email_rebinding.mailbox import MailboxReader, MailboxUnavailable, verification_code
from account_pool.email_rebinding.models import MailboxSecret


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["rotated-mail-rt", None])
async def test_rotated_rt_saved_before_failed_graph_request_and_missing_rt_retained(replacement):
    saved = []
    requests = []

    def transport(request):
        requests.append(request)
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(
                200,
                json={
                    "access_token": "mail-access",
                    "expires_in": 3600,
                    **({"refresh_token": replacement} if replacement else {}),
                },
            )
        assert saved and saved[-1].refresh_token == (replacement or "original-mail-rt")
        return httpx.Response(403, json={"error": "private-provider-detail"})

    async def persist(secret):
        saved.append(secret)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        reader = MailboxReader(client)
        secret = MailboxSecret(
            email="new@outlook.com", client_id="fixture-client", refresh_token="original-mail-rt", method="microsoft"
        )
        with pytest.raises(MailboxUnavailable) as error:
            await reader.poll(secret, utc_now(), persist)
        assert "private-provider-detail" not in str(error.value)
        with pytest.raises(MailboxUnavailable):
            await reader.poll(saved[-1], utc_now(), persist)
        assert sum(r.url.host == "login.microsoftonline.com" for r in requests) == 1
        assert "Mail.Read" in requests[0].content.decode()
        reader.close()


@pytest.mark.parametrize(
    "sender,age,body,expected",
    [
        ("noreply@openai.com", 0, "Your code 123456", "123456"),
        ("noreply@auth.openai.com", 0, "<b>123456</b>", "123456"),
        ("noreply@evilopenai.com", 0, "123456", None),
        ("noreply@openai.com", 60, "123456", None),
        ("noreply@openai.com", 0, "123456 654321", None),
    ],
)
def test_otp_filters_sender_recency_and_ambiguity(sender, age, body, expected):
    now = utc_now()
    assert verification_code(sender, "", body, now - timedelta(seconds=age), now) == expected


@pytest.mark.asyncio
async def test_unknown_provider_has_explicit_manual_code_fallback_without_network():
    def forbidden(request):
        raise AssertionError("manual provider must not send mailbox credentials")

    async def persist(secret):
        raise AssertionError("manual provider must not rotate tokens")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        reader = MailboxReader(client)
        assert await reader.poll(MailboxSecret(email="mail@custom.test", method="manual"), utc_now(), persist) is None
        reader.close()


@pytest.mark.asyncio
async def test_refresh_pending_is_durable_before_post_and_blocks_old_rt_after_lost_response():
    saved = []
    calls = []

    def transport(request):
        assert saved[-1].refresh_pending is True
        calls.append(request)
        raise httpx.ReadTimeout("lost response", request=request)

    async def persist(secret):
        saved.append(secret)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        reader = MailboxReader(client)
        secret = MailboxSecret(
            email="new@outlook.com", method="microsoft", client_id="fixture", refresh_token="original"
        )
        with pytest.raises(MailboxUnavailable):
            await reader.poll(secret, utc_now(), persist)
        with pytest.raises(MailboxUnavailable):
            await reader.poll(saved[-1], utc_now(), persist)
        assert len(calls) == 1 and saved[-1].refresh_pending
        reader.close()
