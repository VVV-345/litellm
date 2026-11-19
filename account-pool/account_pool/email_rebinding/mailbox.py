"""只读收取本次验证码，轮换的邮箱 RT 先加密保存，不使用任意用户 URL。"""

from __future__ import annotations

import asyncio
import email
import imaplib
import re
import ssl
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Final

import httpx
from pydantic import BaseModel, Field

from account_pool.domain import utc_now
from account_pool.email_rebinding.imports import IMAP_HOSTS
from account_pool.email_rebinding.models import MailboxSecret


class MailboxUnavailable(RuntimeError):
    pass


def verification_code(sender: str, subject: str, body: str, received: datetime, since: datetime) -> str | None:
    domain: Final = parseaddr(sender)[1].rsplit("@", 1)[-1].casefold()
    if (domain != "openai.com" and not domain.endswith(".openai.com")) or received < since:
        return None
    text: Final = re.sub(r"<[^>]+>", " ", subject + " " + body)
    codes: Final = frozenset(re.findall(r"(?<![0-9])[0-9]{6}(?![0-9])", text))
    return next(iter(codes)) if len(codes) == 1 else None


class TokenResponse(BaseModel):
    access_token: str = Field(min_length=1, max_length=16384, repr=False)
    refresh_token: str | None = Field(default=None, min_length=1, max_length=16384, repr=False)
    expires_in: int = Field(default=300, ge=1, le=86400)


class MailAddress(BaseModel):
    address: str = ""


class MailSender(BaseModel):
    emailAddress: MailAddress


class MailBody(BaseModel):
    content: str = Field(default="", max_length=262144, repr=False)


class GraphMessage(BaseModel):
    sender: MailSender = Field(alias="from")
    receivedDateTime: datetime
    subject: str = Field(default="", max_length=2000, repr=False)
    body: MailBody


class GraphMessages(BaseModel):
    value: tuple[GraphMessage, ...] = Field(default=(), max_length=20)


@dataclass(frozen=True, slots=True)
class MailboxReader:
    http: httpx.AsyncClient
    threads: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(max_workers=1, thread_name_prefix="pool-mailbox")
    )

    async def poll(
        self,
        secret: MailboxSecret,
        since: datetime,
        persist: Callable[[MailboxSecret], Awaitable[None]],
    ) -> str | None:
        if secret.method == "manual":
            return None
        try:
            if secret.method == "imap":
                return await asyncio.get_running_loop().run_in_executor(self.threads, self._imap, secret, since)
            return await self._microsoft(secret, since, persist)
        except MailboxUnavailable:
            raise
        except Exception:
            raise MailboxUnavailable("邮箱收信失败，请检查授权、应用密码或改为手工填码") from None

    async def _microsoft(
        self,
        secret: MailboxSecret,
        since: datetime,
        persist: Callable[[MailboxSecret], Awaitable[None]],
    ) -> str | None:
        current: Final = await self._refresh(secret, persist)
        response: Final = await self.http.get(
            "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
            headers={"Authorization": "Bearer " + current.access_token, "Prefer": 'outlook.body-content-type="text"'},
            params={
                "$top": "20",
                "$select": "from,receivedDateTime,subject,body",
                "$orderby": "receivedDateTime desc",
                "$filter": "receivedDateTime ge " + since.astimezone(timezone.utc).isoformat(),
            },
            timeout=15,
            follow_redirects=False,
        )
        if response.status_code != 200 or len(response.content) > 1048576:
            raise MailboxUnavailable("邮箱收件箱读取失败；需 Mail.Read 收信授权，也可手工填码")
        messages: Final = GraphMessages.model_validate_json(response.content)
        return next(
            (
                code
                for message in messages.value
                if (
                    code := verification_code(
                        message.sender.emailAddress.address,
                        message.subject,
                        message.body.content,
                        message.receivedDateTime,
                        since,
                    )
                )
                is not None
            ),
            None,
        )

    async def _refresh(
        self,
        secret: MailboxSecret,
        persist: Callable[[MailboxSecret], Awaitable[None]],
    ) -> MailboxSecret:
        if secret.access_token and secret.access_expires_at and secret.access_expires_at > utc_now():
            return secret
        if secret.refresh_pending:
            raise MailboxUnavailable("邮箱刷新结果未确认，请手工填码，不能重放旧刷新令牌")
        if not secret.client_id or not secret.refresh_token:
            raise MailboxUnavailable("缺少邮箱客户端 ID 或收信刷新令牌，请手工填码")
        await persist(secret.model_copy(update={"refresh_pending": True}))
        response: Final = await self.http.post(
            "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
            data={
                "client_id": secret.client_id,
                "refresh_token": secret.refresh_token,
                "grant_type": "refresh_token",
                "scope": "https://graph.microsoft.com/Mail.Read offline_access",
            },
            timeout=15,
            follow_redirects=False,
        )
        if response.status_code != 200 or len(response.content) > 65536:
            raise MailboxUnavailable("邮箱 OAuth 刷新失败，请核对收信权限或手工填码")
        pair: Final = TokenResponse.model_validate_json(response.content)
        current: Final = secret.model_copy(
            update={
                "refresh_pending": False,
                "access_token": pair.access_token,
                "refresh_token": pair.refresh_token or secret.refresh_token,
                "access_expires_at": utc_now() + timedelta(seconds=max(1, pair.expires_in - 60)),
            }
        )
        await persist(current)
        return current

    @staticmethod
    def _imap(secret: MailboxSecret, since: datetime) -> str | None:
        host: Final = IMAP_HOSTS[secret.email.rsplit("@", 1)[1]]
        with imaplib.IMAP4_SSL(host, ssl_context=ssl.create_default_context(), timeout=15) as client:
            client.login(secret.email, secret.password)
            if client.select("INBOX", readonly=True)[0] != "OK":
                raise MailboxUnavailable("邮箱收件箱不可读")
            status, data = client.uid("search", "SINCE", since.strftime("%d-%b-%Y"))
            if status != "OK" or not data or not isinstance(data[0], bytes):
                return None
            for uid in reversed(data[0].split()[-20:]):
                fetched, parts = client.uid("fetch", uid.decode("ascii"), "(BODY.PEEK[])")
                if fetched != "OK":
                    continue
                code = next(
                    (
                        _imap_code(part[1], since)
                        for part in parts
                        if isinstance(part, tuple) and isinstance(part[1], bytes)
                    ),
                    None,
                )
                if code:
                    return code
        return None

    def close(self) -> None:
        self.threads.shutdown(wait=False, cancel_futures=True)


def _part_text(part: Message[str, str]) -> str:
    payload: Final = part.get_payload(decode=True)
    return payload.decode(part.get_content_charset() or "utf-8", errors="replace") if isinstance(payload, bytes) else ""


def _imap_code(raw: bytes, since: datetime) -> str | None:
    if len(raw) > 262144:
        return None
    message: Final = email.message_from_bytes(raw)
    try:
        received: Final = parsedate_to_datetime(message.get("Date", ""))
    except (TypeError, ValueError):
        return None
    if received.tzinfo is None:
        return None
    body: Final = " ".join(
        _part_text(part) for part in message.walk() if part.get_content_type() in ("text/plain", "text/html")
    )
    return verification_code(message.get("From", ""), message.get("Subject", ""), body, received, since)
