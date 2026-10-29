"""识别 CPA、Sub2API 和 2FA 登录资料，只转换结构，不执行登录或生成令牌。"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from typing import Final

from pydantic import JsonValue, TypeAdapter

from account_pool.credential_ownership import CredentialConflict
from account_pool.onboarding_models import OnboardingEntry

_JSON: Final = TypeAdapter(JsonValue)


def _access_expiration(token: JsonValue) -> JsonValue:
    if not isinstance(token, str) or len(token.split(".")) != 3:
        return None
    try:
        claims: Final = _JSON.validate_json(base64.urlsafe_b64decode(token.split(".")[1] + "==="))
    except ValueError:
        return None
    return claims.get("exp") if isinstance(claims, dict) else None


def is_two_factor_text(content: str) -> bool:
    return not content.lstrip("\ufeff \t\r\n").startswith(("{", "[")) and "----" in content


def validate_two_factor_entry(entry: OnboardingEntry, supplier: str) -> None:
    if supplier != "openai_codex":
        raise CredentialConflict("当前 2FA 格式仅支持 Codex 账号")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", entry.label) or not entry.supplier_password:
        raise CredentialConflict("2FA 账号需要有效邮箱和供应商密码")
    if not re.fullmatch(r"[A-Z2-7]{16,128}", entry.totp_secret):
        raise CredentialConflict("2FA 密钥必须是有效的 Base32 字符串")
    try:
        base64.b32decode(entry.totp_secret + "=" * (-len(entry.totp_secret) % 8))
    except ValueError:
        raise CredentialConflict("2FA 密钥必须是有效的 Base32 字符串") from None


def parse_two_factor_text(content: str, supplier: str) -> tuple[OnboardingEntry, ...]:
    lines: Final = tuple(line.strip() for line in content.lstrip("\ufeff").splitlines() if line.strip())
    if not 1 <= len(lines) <= 100:
        raise CredentialConflict("每批最多导入 100 个账号")

    def parse(line: str) -> OnboardingEntry:
        fields: Final = line.split("----")
        if len(fields) != 3 or not fields[1] or len(fields[1]) > 1024 or not 1 <= len(fields[0].strip()) <= 254:
            raise CredentialConflict("2FA 格式应为：邮箱----供应商密码----2FA密钥，每行一个账号")
        secret: Final = fields[2].strip().upper()
        if len(secret) > 128:
            raise CredentialConflict("2FA 密钥长度无效")
        entry: Final = OnboardingEntry(label=fields[0].strip(), supplier_password=fields[1], totp_secret=secret)
        validate_two_factor_entry(entry, supplier)
        return entry

    return tuple(parse(line) for line in lines)


def normalize_auth_file(content: bytes, supplier: str) -> bytes:
    try:
        value: Final = _JSON.validate_json(content)
    except ValueError:
        raise CredentialConflict("认证文件必须是有效的 JSON，或邮箱----密码----2FA密钥文本") from None
    if isinstance(value, dict) and "accounts" in value:
        accounts: Final = value["accounts"]
        if not isinstance(accounts, list) or len(accounts) != 1:
            raise CredentialConflict("单张卡片只能导入一个账号，多账号文件请使用批量上号")
        return normalize_auth_file(json.dumps(accounts[0]).encode(), supplier)
    if isinstance(value, list):
        if len(value) != 1:
            raise CredentialConflict("单张卡片只能导入一个账号，多账号文件请使用批量上号")
        return normalize_auth_file(json.dumps(value[0]).encode(), supplier)
    if not isinstance(value, dict):
        raise CredentialConflict("认证文件必须是 JSON 对象")
    if "credentials" not in value and "platform" not in value:
        return content
    if supplier != "openai_codex" or value.get("platform") != "openai" or value.get("type") != "oauth":
        raise CredentialConflict("当前仅支持 OpenAI OAuth 的 Sub2API 文件，且须选择 Codex")
    credentials: Final = value.get("credentials")
    if not isinstance(credentials, dict) or credentials.get("auth_mode", "oauth") != "oauth":
        raise CredentialConflict("Sub2API credentials 必须是 OAuth 凭据对象")
    if not isinstance(credentials.get("refresh_token"), str) or not str(credentials["refresh_token"]).strip():
        raise CredentialConflict("Sub2API 文件缺少 refresh_token，请重新导出完整 OAuth 凭据")
    if any(
        key in credentials and not isinstance(credentials[key], str)
        for key in (
            "access_token",
            "refresh_token",
            "id_token",
            "email",
            "plan_type",
            "chatgpt_account_id",
            "account_id",
        )
    ):
        raise CredentialConflict("Sub2API 凭据字段类型无效")
    extra: Final = value.get("extra")
    metadata: Final = extra if isinstance(extra, dict) else {}
    fields: Final = {
        "type": "codex",
        **{
            key: credentials[key]
            for key in ("access_token", "refresh_token", "id_token", "email", "plan_type")
            if isinstance(credentials.get(key), str) and credentials[key]
        },
        "account_id": credentials.get("chatgpt_account_id") or credentials.get("account_id"),
        "last_refresh": metadata.get("last_refresh"),
        **{key: value[key] for key in ("priority", "prefix", "proxy_url", "disabled") if key in value},
    }
    expires: Final = credentials.get("expires_at") or _access_expiration(credentials.get("access_token"))
    try:
        expiration: Final = (
            datetime.fromtimestamp(expires, timezone.utc).isoformat()
            if (isinstance(expires, (int, float)) and not isinstance(expires, bool))
            else None
        )
    except (ValueError, OverflowError, OSError):
        raise CredentialConflict("Sub2API 令牌过期时间无效") from None
    return json.dumps(
        {key: item for key, item in {**fields, "expired": expiration}.items() if item is not None}
    ).encode()


def expand_import_file(entry: OnboardingEntry, supplier: str) -> tuple[OnboardingEntry, ...]:
    if any(character in entry.label for character in ("/", "\\", "\x00")):
        raise CredentialConflict("请选择不含路径的文件名")
    if is_two_factor_text(entry.content):
        return parse_two_factor_text(entry.content, supplier)
    try:
        value: Final = _JSON.validate_json(entry.content)
    except ValueError:
        raise CredentialConflict("认证文件格式无效，请选择 CPA、Sub2API JSON 或 2FA 文本") from None
    accounts: Final = value.get("accounts") if isinstance(value, dict) and "accounts" in value else value
    items: Final = accounts if isinstance(accounts, list) else [accounts]
    if not 1 <= len(items) <= 100:
        raise CredentialConflict("每批最多导入 100 个账号")
    return tuple(
        OnboardingEntry(
            label=entry.label
            if len(items) == 1 and entry.label.lower().endswith(".json")
            else f"import-{index + 1}.json",
            content=normalize_auth_file(json.dumps(item).encode(), supplier).decode(),
        )
        for index, item in enumerate(items)
    )
