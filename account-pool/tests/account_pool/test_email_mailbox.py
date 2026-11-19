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


class CountedMailboxBody(httpx.AsyncByteStream):
    def __init__(self, chunk_bytes=32768, chunks=64):
        self.chunk_bytes, self.chunks = chunk_bytes, chunks
        self.downloaded = 0
        self.closed = False

    async def __aiter__(self):
        for _ in range(self.chunks):
            self.downloaded += self.chunk_bytes
            yield b"x" * self.chunk_bytes

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", ["1", "2097152"])
async def test_graph_response_stops_reading_at_limit_even_with_wrong_content_length(declared):
    body = CountedMailboxBody()

    def respond(request):
        return httpx.Response(200, headers={"content-length": declared}, stream=body)

    async def persist(secret):
        raise AssertionError("cached token must not refresh")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        reader = MailboxReader(client)
        secret = MailboxSecret(
            email="test@outlook.com",
            method="microsoft",
            access_token="fixture",
            access_expires_at=utc_now() + timedelta(hours=1),
        )
        try:
            with pytest.raises(MailboxUnavailable):
                await reader.poll(secret, utc_now(), persist)
            assert body.downloaded <= 1048576 + body.chunk_bytes
            assert body.closed
        finally:
            reader.close()


class MailboxWire:
    def __init__(self, raw, declared):
        self.raw, self.declared = raw, declared
        self.lines = [b"* OK fixture ready\r\n"]
        self.commands, self.read_sizes = [], []
        self.closed = False

    def sendall(self, data):
        self.commands.append(data)
        words = data.split()
        tag, command = words[:2]
        if command == b"CAPABILITY":
            self.lines.append(b"* CAPABILITY IMAP4rev1\r\n")
        elif command in (b"EXAMINE", b"SELECT"):
            self.lines.append(b"* 1 EXISTS\r\n")
        elif command == b"UID" and words[2] == b"SEARCH":
            self.lines.append(b"* SEARCH 1\r\n")
        elif command == b"UID" and words[2] == b"FETCH":
            self.lines.extend([b"* 1 FETCH (UID 1 BODY[]<0> {" + str(self.declared).encode() + b"}\r\n", b")\r\n"])
        elif command == b"LOGOUT":
            self.lines.append(b"* BYE closing\r\n")
        self.lines.append(tag + b" OK completed\r\n")

    def makefile(self, mode):
        return self

    def readline(self, limit):
        return self.lines.pop(0)

    def read(self, size):
        self.read_sizes.append(size)
        return self.raw[:size] if self.declared == len(self.raw) else b"x" * size

    def shutdown(self, how):
        self.closed = True

    def close(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [0, 262145, 1048576])
async def test_imap_partial_fetch_and_literal_guard_bound_downloads(monkeypatch, size):
    import socket
    import ssl
    from email.utils import format_datetime

    now = utc_now()
    raw = (
        "From: noreply@openai.com\r\nDate: "
        + format_datetime(now)
        + "\r\nSubject: Verify\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n123456"
    ).encode()
    wire = MailboxWire(raw, size or len(raw))

    class Context:
        def wrap_socket(self, sock, server_hostname):
            assert server_hostname == "imap.gmail.com"
            return sock

    def connect(address, timeout=None):
        assert address == ("imap.gmail.com", 993)
        return wire

    monkeypatch.setattr(socket, "create_connection", connect)
    monkeypatch.setattr(ssl, "create_default_context", Context)

    async def persist(secret):
        raise AssertionError("IMAP must not rotate credentials")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("no HTTP")), trust_env=False
    ) as client:
        reader = MailboxReader(client)
        secret = MailboxSecret(email="test@gmail.com", password="fixture-app-password", method="imap")
        try:
            if size > 262145:
                with pytest.raises(MailboxUnavailable):
                    await reader.poll(secret, now - timedelta(seconds=1), persist)
                assert wire.read_sizes == [], "oversized literal must be rejected before reading any payload"
            else:
                result = await reader.poll(secret, now - timedelta(seconds=1), persist)
                assert result == (None if size else "123456")
                assert sum(wire.read_sizes) <= 262145
            assert wire.closed
            assert any(b"BODY.PEEK[]<0.262145>" in command for command in wire.commands)
        finally:
            reader.close()


@pytest.mark.asyncio
async def test_token_response_is_bounded_and_uncertain_rotation_stays_pending():
    saved = []
    body = CountedMailboxBody(chunk_bytes=4096, chunks=64)

    def respond(request):
        assert request.url.host == "login.microsoftonline.com"
        assert request.headers["accept-encoding"] == "identity"
        assert saved[-1].refresh_pending
        return httpx.Response(200, stream=body)

    async def persist(secret):
        saved.append(secret)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        reader = MailboxReader(client)
        try:
            with pytest.raises(MailboxUnavailable):
                await reader.poll(
                    MailboxSecret(
                        email="test@outlook.com",
                        method="microsoft",
                        client_id="fixture",
                        refresh_token="original-fixture-rt",
                    ),
                    utc_now(),
                    persist,
                )
            assert body.closed and body.downloaded <= 65536 + 16384
            assert len(saved) == 1 and saved[-1].refresh_pending
            assert saved[-1].refresh_token == "original-fixture-rt"
        finally:
            reader.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
async def test_unexpected_compressed_mail_body_is_rejected_before_decode(encoding):
    body = CountedMailboxBody()

    def respond(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, headers={"content-encoding": encoding}, stream=body)

    async def persist(secret):
        raise AssertionError("cached token must not refresh")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        reader = MailboxReader(client)
        try:
            with pytest.raises(MailboxUnavailable):
                await reader.poll(
                    MailboxSecret(
                        email="test@outlook.com",
                        method="microsoft",
                        access_token="fixture",
                        access_expires_at=utc_now() + timedelta(hours=1),
                    ),
                    utc_now(),
                    persist,
                )
            assert body.downloaded == 0 and body.closed
        finally:
            reader.close()


@pytest.mark.asyncio
async def test_bounded_graph_reply_still_returns_current_verification_code():
    now = utc_now()

    def respond(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "from": {"emailAddress": {"address": "noreply@openai.com"}},
                        "receivedDateTime": now.isoformat(),
                        "subject": "Verification",
                        "body": {"content": "123456"},
                    }
                ]
            },
        )

    async def persist(secret):
        raise AssertionError("cached token must not refresh")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        reader = MailboxReader(client)
        try:
            assert (
                await reader.poll(
                    MailboxSecret(
                        email="test@outlook.com",
                        method="microsoft",
                        access_token="fixture",
                        access_expires_at=now + timedelta(hours=1),
                    ),
                    now,
                    persist,
                )
                == "123456"
            )
        finally:
            reader.close()
