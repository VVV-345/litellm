"""解析 CSV 或四段式 TXT，只校验资料，不登录邮箱或覆盖已导入的密文。"""

import csv
import io
import re
from dataclasses import dataclass
from typing import Final

from account_pool.email_rebinding.models import MailboxSecret

IMAP_HOSTS: Final = {
    "gmail.com": "imap.gmail.com",
    "googlemail.com": "imap.gmail.com",
    "icloud.com": "imap.mail.me.com",
    "me.com": "imap.mail.me.com",
    "mac.com": "imap.mail.me.com",
    "qq.com": "imap.qq.com",
    "foxmail.com": "imap.qq.com",
    "163.com": "imap.163.com",
    "126.com": "imap.126.com",
    "yahoo.com": "imap.mail.yahoo.com",
    "yahoo.co.jp": "imap.mail.yahoo.co.jp",
}
MICROSOFT: Final = frozenset({"outlook.com", "hotmail.com", "live.com", "msn.com"})
_NAMES: Final = {
    "邮箱": "email",
    "账号": "email",
    "account": "email",
    "mail": "email",
    "密码": "password",
    "邮箱密码": "password",
    "mailbox_password": "password",
    "clientid": "client_id",
    "客户端id": "client_id",
    "rt": "refresh_token",
    "oauth": "refresh_token",
    "状态": "binding_status",
    "status": "binding_status",
    "收信方式": "method",
}


@dataclass(frozen=True, slots=True)
class ImportedMailbox:
    secret: MailboxSecret
    bound: bool


def parse_mailboxes(content: str) -> tuple[ImportedMailbox, ...]:
    if len(content.encode("utf-8")) > 1048576:
        raise ValueError("邮箱文件超过 1 MiB")
    text: Final = content.lstrip("\ufeff")
    lines: Final = tuple(line for line in text.splitlines() if line.strip())
    if not lines or len(lines) > 2001:
        raise ValueError("每次导入 1 至 2000 条邮箱资料")
    delimited: Final = "----" in lines[0]
    rows: Final = (
        tuple(tuple(part.strip() for part in line.split("----")) for line in lines)
        if delimited
        else tuple(
            tuple(part.strip() for part in row)
            for row in csv.reader(io.StringIO(text))
            if any(part.strip() for part in row)
        )
    )
    first: Final = tuple(_NAMES.get(name.casefold(), name.casefold()) for name in rows[0])
    header: Final = "email" in first
    names: Final = first if header else ("email", "password", "client_id", "refresh_token")
    selected: Final = rows[1:] if header else rows
    if len(set(names)) != len(names) or not selected or len(selected) > 2000:
        raise ValueError("检查表头和资料条数")
    result: Final = tuple(
        _parse_row(number, row, names, header) for number, row in enumerate(selected, 2 if header else 1)
    )
    if len({row.secret.email for row in result}) != len(result):
        raise ValueError("文件中存在重复邮箱，未导入任何资料")
    return tuple(result)


def _parse_row(number: int, row: tuple[str, ...], names: tuple[str, ...], header: bool) -> ImportedMailbox:
    if len(row) != len(names) and not (not header and len(row) == 2):
        raise ValueError(f"第 {number} 行列数不正确，请使用表头 CSV 或邮箱----密码----客户端ID----刷新令牌")
    values: Final = dict(zip(names, row))
    email: Final = values.get("email", "").casefold()
    if not re.fullmatch(r"[^\s@,]+@[^\s@,]+\.[^\s@,]+", email) or len(email) > 320:
        raise ValueError(f"第 {number} 行邮箱格式不正确")
    domain: Final = email.rsplit("@", 1)[1]
    inferred: Final = (
        "microsoft"
        if domain in MICROSOFT and values.get("client_id") and values.get("refresh_token")
        else ("imap" if domain in IMAP_HOSTS and values.get("password") else "manual")
    )
    method: Final = values.get("method") or inferred
    if method not in ("microsoft", "imap", "manual") or method == "imap" and domain not in IMAP_HOSTS:
        raise ValueError(f"第 {number} 行收信方式不受支持；可选 manual 手工填码")
    secret: Final = MailboxSecret(
        email=email,
        password=values.get("password", ""),
        client_id=values.get("client_id", ""),
        refresh_token=values.get("refresh_token", ""),
        method=method,
    )
    bound: Final = values.get("binding_status", "").casefold() in {
        "bound",
        "reserved",
        "已绑定",
        "已使用",
        "处理中",
        "used",
    }
    return ImportedMailbox(secret, bound)
