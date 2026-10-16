"""本模块静态核对回退证据并生成可读结论，不读取业务数据或执行历史代码。"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final

from pydantic import JsonValue, TypeAdapter

from account_pool.release_models import RollbackCheck


@dataclass(frozen=True, slots=True)
class RollbackEvidence:
    database: str
    pool: str
    credentials: str
    logs: str
    startup: str
    features: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OAuthBrowserSchemaEvidence:
    prisma: bytes
    pool: bytes
    sources: str
    startup: str


STARTUP_SOURCES: Final = ("proxy_cli.py", "utils.py", "prod_entrypoint.sh", "migration-history")

# 仅绑定已审阅的 5b76f5170d -> 0813fb86f0 非 release_* 源码树，不接受目标镜像自报允许范围。
OAUTH_BROWSER_SOURCE_DIGESTS: Final = (
    "27e3e2ca5cdee2bbecba5c0f5accf77dafb9daf100e811244fd834610732fba5",
    "aeb2c6886210594cb6652dc6541127d3468092989786aaf89518b4588a3bc953",
)
OAUTH_BROWSER_SCHEMA_ADDITIONS: Final = (
    b"CREATE TABLE IF NOT EXISTS account_pool_oauth_browser_sessions ( id uuid PRIMARY KEY, "
    b"environment_id uuid NOT NULL REFERENCES account_pool_environments(id) ON DELETE CASCADE, "
    b"status text NOT NULL, expires_at timestamptz NOT NULL, payload jsonb NOT NULL )",
    b"CREATE INDEX IF NOT EXISTS account_pool_oauth_browser_sessions_environment_idx "
    b"ON account_pool_oauth_browser_sessions (environment_id)",
    b"CREATE UNIQUE INDEX IF NOT EXISTS account_pool_oauth_browser_sessions_active_environment_idx "
    b"ON account_pool_oauth_browser_sessions (environment_id) WHERE status IN ('starting', 'active')",
    b"CREATE UNIQUE INDEX IF NOT EXISTS account_pool_oauth_browser_sessions_active_callback_environment_idx "
    b"ON account_pool_oauth_browser_sessions (environment_id) WHERE status IN ('starting', 'active', 'callback_pending')",
)

# 只允许此处审阅过的初始目录 schema 通过自动兼容门禁。
PROXY_CATALOG_SCHEMA_ADDITIONS: Final = tuple(
    sorted(
        (
            b"CREATE INDEX IF NOT EXISTS account_pool_isp_binding_profile_idx ON account_pool_proxy_gateway_isp_bindings (isp_profile_id)",
            b"CREATE INDEX IF NOT EXISTS account_pool_isp_expiry_idx ON account_pool_isp_profiles (expires_on)",
            (
                b"CREATE TABLE IF NOT EXISTS account_pool_isp_profiles ( id uuid PRIMARY KEY, "
                b"name text NOT NULL, protocol text NOT NULL CHECK (protocol IN ('http', 'https', 'socks5')), "
                b"host text NOT NULL, port integer NOT NULL CHECK (port BETWEEN 1 AND 65535), "
                b"credentials_ciphertext text NOT NULL, expires_on date, enabled boolean NOT NULL DEFAULT true, "
                b"created_at timestamptz NOT NULL DEFAULT now() )"
            ),
            (
                b"CREATE TABLE IF NOT EXISTS account_pool_proxy_configuration_profiles ( id uuid PRIMARY KEY, "
                b"name text NOT NULL, content_ciphertext text NOT NULL, source_url_ciphertext text, "
                b"created_at timestamptz NOT NULL )"
            ),
            (
                b"CREATE TABLE IF NOT EXISTS account_pool_proxy_configuration_state ( singleton boolean PRIMARY KEY "
                b"DEFAULT true CHECK (singleton), active_profile_id uuid NOT NULL "
                b"REFERENCES account_pool_proxy_configuration_profiles(id) ON DELETE RESTRICT )"
            ),
            (
                b"CREATE TABLE IF NOT EXISTS account_pool_proxy_gateway_isp_bindings ( port integer PRIMARY KEY "
                b"REFERENCES account_pool_proxy_gateways(port) ON DELETE CASCADE, isp_profile_id uuid NOT NULL "
                b"REFERENCES account_pool_isp_profiles(id) ON DELETE RESTRICT )"
            ),
        )
    )
)


ISP_PROBE_SCHEMA_ADDITIONS: Final = (
    b"CREATE TABLE IF NOT EXISTS account_pool_isp_probe_results ( profile_id uuid PRIMARY KEY "
    b"REFERENCES account_pool_isp_profiles(id) ON DELETE CASCADE, "
    b"status text NOT NULL CHECK (status IN ('ok', 'timeout', 'error')), checked_at timestamptz NOT NULL, "
    b"exit_ip inet, country text, region text, city text, isp text, "
    b"delay_ms integer CHECK (delay_ms >= 0), error text )",
)


def manager_source_digest(sources: tuple[tuple[str, bytes], ...]) -> str:
    return hashlib.sha256(
        b"\n".join(
            name.encode() + b"\0" + hashlib.sha256(source.replace(b"\r\n", b"\n")).hexdigest().encode()
            for name, source in sorted(sources)
            for path in (PurePosixPath(name),)
            if not (
                path.parent == PurePosixPath("account_pool")
                and path.name.startswith("release_")
                and path.suffix == ".py"
            )
            and not ("__pycache__" in path.parts and path.suffix == ".pyc")
        )
    ).hexdigest()


def _source_named(sources: tuple[tuple[str, bytes], ...], filename: str) -> bytes:
    return next((source for name, source in sources if PurePosixPath(name).name == filename), b"")


def _transport_fields(source: bytes) -> tuple[tuple[str, str], ...]:
    try:
        tree: Final = ast.parse(source)
    except SyntaxError:
        return ()
    transport: Final = next(
        (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "TransportPolicy"),
        None,
    )
    if transport is None:
        return ()
    return tuple(
        (statement.target.id, ast.dump(statement, include_attributes=False))
        for statement in transport.body
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
    )


def schema_member_contract(name: str, source: bytes, *, omit_read_timeout_field: bool = False) -> bytes:
    path: Final = PurePosixPath(name)
    if path.suffix == ".prisma":
        return source.replace(b"\r\n", b"\n")
    if path.suffix != ".py" or path.name.startswith("release_"):
        return b""
    tree: Final = ast.parse(source)
    statements: Final = tuple(
        " ".join(node.value.split())
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.search(r"\b(?:CREATE\s+TABLE|ALTER\s+TABLE|CREATE\s+(?:UNIQUE\s+)?INDEX)\b", node.value, re.I)
    )
    if not statements:
        return b""
    contracts: Final = tuple(
        f"{node.name}:"
        + ast.dump(
            ast.ClassDef(
                name=node.name,
                bases=node.bases,
                keywords=node.keywords,
                body=[
                    item
                    for item in node.body
                    if isinstance(item, (ast.AnnAssign, ast.Assign))
                    and not (
                        omit_read_timeout_field
                        and path.name == "policies.py"
                        and node.name == "TransportPolicy"
                        and isinstance(item, ast.AnnAssign)
                        and isinstance(item.target, ast.Name)
                        and item.target.id == "read_timeout_seconds"
                    )
                ],
                decorator_list=[],
                type_params=[],
            ),
            include_attributes=False,
        )
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(isinstance(base, ast.Name) and base.id in ("BaseModel", "StrEnum", "Enum") for base in node.bases)
    )
    aliases: Final = tuple(
        ast.dump(node, include_attributes=False)
        for node in tree.body
        if isinstance(node, (ast.AnnAssign, ast.Assign)) and isinstance(node.value, (ast.Subscript, ast.BinOp))
    )
    return "\n".join(sorted((*statements, *contracts, *aliases))).encode()


def manager_schema_contract(sources: tuple[tuple[str, bytes], ...], *, omit_read_timeout_field: bool = False) -> bytes:
    return b"\n".join(
        sorted(
            contract
            for name, source in sources
            for contract in schema_member_contract(
                name, source, omit_read_timeout_field=omit_read_timeout_field
            ).splitlines()
            if contract
        )
    )

def additive_manager_schema_upgrade(
    current: tuple[tuple[str, bytes], ...], target: tuple[tuple[str, bytes], ...]
) -> bool:
    if not current or not target or _source_named(current, "schema.prisma") != _source_named(target, "schema.prisma"):
        return False
    try:
        before: Final = manager_schema_contract(current)
        after: Final = manager_schema_contract(target)
    except SyntaxError:
        return False
    additions: Final = tuple(line for line in after.splitlines() if line not in before.splitlines())
    if additions not in (
        PROXY_CATALOG_SCHEMA_ADDITIONS,
        ISP_PROBE_SCHEMA_ADDITIONS,
        tuple(sorted((*PROXY_CATALOG_SCHEMA_ADDITIONS, *ISP_PROBE_SCHEMA_ADDITIONS))),
    ):
        return False
    return all(
        line.startswith(b"CREATE TABLE IF NOT EXISTS account_pool_")
        or line.startswith(b"CREATE INDEX IF NOT EXISTS account_pool_")
        or line.startswith(b"CREATE UNIQUE INDEX IF NOT EXISTS account_pool_")
        for line in additions
    ) and all(line in after.splitlines() for line in before.splitlines())


def forward_compatible_manager_upgrade(
    current: tuple[tuple[str, bytes], ...], target: tuple[tuple[str, bytes], ...]
) -> bool:
    current_source: Final = _source_named(current, "policies.py")
    target_source: Final = _source_named(target, "policies.py")
    if (
        not current_source
        or not target_source
        or not _source_named(current, "schema.prisma")
        or not _source_named(target, "schema.prisma")
        or b"exclude_none=True" not in target_source
    ):
        return False
    current_fields: Final = _transport_fields(current_source)
    target_fields: Final = _transport_fields(target_source)
    current_names: Final = tuple(name for name, _ in current_fields)
    target_names: Final = tuple(name for name, _ in target_fields)
    added: Final = tuple(name for name in target_names if name not in current_names)
    removed: Final = tuple(name for name in current_names if name not in target_names)
    current_by_name: Final = dict(current_fields)
    target_by_name: Final = dict(target_fields)
    unchanged: Final = all(current_by_name[name] == target_by_name[name] for name in current_names)
    try:
        schema_unchanged: Final = manager_schema_contract(current) == manager_schema_contract(
            target, omit_read_timeout_field=True
        )
    except SyntaxError:
        return False
    expected: Final = ast.parse("read_timeout_seconds: int | None = Field(default=None, ge=1, le=3600)").body[0]
    added_field: Final = next(
        (
            statement
            for node in ast.parse(target_source).body
            if isinstance(node, ast.ClassDef) and node.name == "TransportPolicy"
            for statement in node.body
            if isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "read_timeout_seconds"
        ),
        None,
    )
    return (
        added == ("read_timeout_seconds",)
        and not removed
        and unchanged
        and schema_unchanged
        and added_field is not None
        and ast.dump(added_field, include_attributes=False) == ast.dump(expected, include_attributes=False)
    )


def oauth_browser_schema_upgrade(current: OAuthBrowserSchemaEvidence, target: OAuthBrowserSchemaEvidence) -> bool:
    if (current.sources, target.sources) != OAUTH_BROWSER_SOURCE_DIGESTS:
        return False
    if not current.prisma or current.prisma.replace(b"\r\n", b"\n") != target.prisma.replace(b"\r\n", b"\n"):
        return False
    if not current.startup or current.startup != target.startup:
        return False
    before: Final = current.pool.splitlines()
    # 保留所有旧契约及重复项；缺一条、重复新增或已部分升级均不属于此次受控升级。
    return (
        bool(before)
        and not any(statement in before for statement in OAUTH_BROWSER_SCHEMA_ADDITIONS)
        and sorted(target.pool.splitlines()) == sorted((*before, *OAUTH_BROWSER_SCHEMA_ADDITIONS))
    )


FEATURES: Final = {
    "virtual_key_secret.py": ("虚拟密钥显示与复制", "def open_virtual_key("),
    "account_pool_full_log_api.py": ("完整日志查询", "def create_full_log_router("),
    "account_pool_timing.py": ("请求分段耗时", ""),
    "account_pool_native_routing.py": ("卡片策略与原生路由整合", ""),
    "account_pool_releases.py": ("版本管理页面接口", "def create_release_router("),
}


def digest_sources(sources: dict[str, bytes], names: tuple[str, ...]) -> str:
    if any(not sources.get(name) for name in names):
        return ""
    return hashlib.sha256(b"\n".join(sources[name].replace(b"\r\n", b"\n") for name in names)).hexdigest()


def credential_contract(pool: dict[str, bytes], proxy: dict[str, bytes]) -> str:
    classes: Final = ("SecretPurpose", "EnvironmentSecretDeriver", "StateCipher")
    definitions: Final = tuple(
        (node.name, ast.dump(node, include_attributes=False))
        for source in pool.values()
        for node in ast.parse(source).body
        if isinstance(node, ast.ClassDef) and node.name in classes
    )
    if any(sum(name == expected for name, _ in definitions) != 1 for expected in classes):
        return ""
    persistence: Final = digest_sources(pool, ("repository.py", "management_repository.py"))
    encryption: Final = digest_sources(proxy, ("virtual_key_secret.py", "encrypt_decrypt_utils.py"))
    if not persistence or not encryption:
        return ""
    return hashlib.sha256((repr(sorted(definitions)) + persistence + encryption).encode()).hexdigest()


def same_contract(key: str, title: str, current: str, target: str, detail: str) -> RollbackCheck:
    same: Final = bool(current and target and current == target)
    return RollbackCheck(
        key=key,
        title=title,
        status="compatible" if same else "unverified",
        detail=detail if same else "定义或关键读写代码存在差异，尚未验证旧版能否处理当前数据；差异不等于一定不兼容。",
    )


def canonical_configuration(raw: bytes) -> dict[str, JsonValue]:
    config: Final = TypeAdapter(dict[str, JsonValue]).validate_json(raw)
    services: Final = TypeAdapter(dict[str, dict[str, JsonValue]]).validate_python(config["services"])
    # Docker 不保证挂载和端口返回顺序；仅这些无序集合排序，启动参数等有序列表保持原样。
    return {
        **config,
        "services": {
            name: {
                key: sorted(value, key=lambda item: json.dumps(item, sort_keys=True))
                if key in ("volumes", "ports") and isinstance(value, list)
                else value
                for key, value in service.items()
            }
            for name, service in services.items()
        },
    }


def configuration_checks(current: bytes, target: bytes) -> tuple[RollbackCheck, ...]:
    current_config: Final = canonical_configuration(current)
    target_config: Final = canonical_configuration(target)
    current_services: Final = TypeAdapter(dict[str, dict[str, JsonValue]]).validate_python(current_config["services"])
    target_services: Final = TypeAdapter(dict[str, dict[str, JsonValue]]).validate_python(target_config["services"])
    same: Final = all(
        {
            key: value
            for key, value in current_services[service].items()
            if key not in ("image", "labels", "pull_policy")
        }
        == {
            key: value
            for key, value in target_services[service].items()
            if key not in ("image", "labels", "pull_policy")
        }
        for service in ("litellm", "account-pool")
    ) and all(
        current_config.get(key) == target_config.get(key) for key in ("volumes", "networks", "secrets", "configs")
    )
    raw_command: Final = current_services["litellm"].get("command")
    command: Final = (
        shlex.split(raw_command)
        if isinstance(raw_command, str)
        else TypeAdapter(tuple[str, ...]).validate_python(raw_command or ())
    )
    safe: Final = (
        "--use_v2_migration_resolver" in command
        and not any(
            item.split("=", 1)[0] in ("--use_prisma_db_push", "--skip_db", "--skip_server_startup") for item in command
        )
        and current_services["litellm"].get("entrypoint")
        in (
            ["docker/prod_entrypoint.sh"],
            ["/app/docker/prod_entrypoint.sh"],
            "docker/prod_entrypoint.sh",
            "/app/docker/prod_entrypoint.sh",
        )
    )
    return (
        RollbackCheck(
            key="configuration",
            title="部署配置与数据位置",
            status="compatible" if same else "unverified",
            detail="环境变量、启动参数与挂载位置一致，继续使用当前数据。"
            if same
            else "历史配置与当前不同；本次保留当前环境变量、加密配置和数据挂载，不应用历史配置。旧程序的配置支持范围仍需核对。",
        ),
        RollbackCheck(
            key="migration",
            title="数据库启动方式",
            status="compatible" if safe else "blocked",
            detail="使用 v2 迁移流程，未启用强制数据库同步；仍需结构与启动代码检查通过。"
            if safe
            else "当前启动参数未明确使用 v2 安全迁移流程，或启用了数据库强制同步等不支持的参数，不能用于程序回退。",
        ),
    )


def compare_evidence(current: RollbackEvidence, target: RollbackEvidence) -> tuple[RollbackCheck, ...]:
    return (
        same_contract(
            "database",
            "数据库结构",
            current.database,
            target.database,
            "两版 Prisma 结构定义一致；未执行数据库恢复或迁移。",
        ),
        same_contract("pool", "号池配置与账号状态", current.pool, target.pool, "号池存储结构及状态定义一致。"),
        same_contract(
            "credentials",
            "认证文件与密钥",
            current.credentials,
            target.credentials,
            "密钥派生、状态加密和数据库凭据存储定义一致；不保证 OAuth 有效或上游认证文件协议兼容。",
        ),
        same_contract(
            "logs",
            "日志与费用记录",
            current.logs,
            target.logs,
            "完整日志读写代码一致；费用记录沿用当前数据库，结构另行核对。",
        ),
        same_contract("startup", "服务启动代码", current.startup, target.startup, "入口脚本与迁移执行代码一致。"),
    )


def evidence_state(evidence: RollbackEvidence, configuration: bytes, backup_hash: str) -> str:
    canonical: Final = json.dumps(canonical_configuration(configuration), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(
        (repr(evidence) + hashlib.sha256(canonical.encode()).hexdigest() + backup_hash).encode()
    ).hexdigest()
