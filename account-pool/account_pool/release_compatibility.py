"""本模块静态核对回退证据并生成可读结论，不读取业务数据或执行历史代码。"""

from __future__ import annotations

import ast
import hashlib
import shlex
from dataclasses import dataclass
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


def configuration_checks(current: bytes, target: bytes) -> tuple[RollbackCheck, ...]:
    current_config: Final = TypeAdapter(dict[str, JsonValue]).validate_json(current)
    target_config: Final = TypeAdapter(dict[str, JsonValue]).validate_json(target)
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
    return hashlib.sha256(
        (repr(evidence) + hashlib.sha256(configuration).hexdigest() + backup_hash).encode()
    ).hexdigest()
