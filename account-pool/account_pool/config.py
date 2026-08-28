"""读取环境变量和 YAML 配置，并以原子替换方式持久化号池配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

import yaml
from pydantic import SecretStr, TypeAdapter

from account_pool.models import PoolConfig, normalize_channel_priority

StoreMode = Literal["memory", "redis"]


@dataclass(frozen=True, slots=True)
class Settings:
    config_path: Path
    store_mode: StoreMode
    redis_url: str
    litellm_url: str
    litellm_admin_key: str | None
    lease_ttl_seconds: int
    internal_token: str | None
    maximum_lease_seconds: int = 3_600
    database_url: str | None = None
    database_schema: str = "public"
    actor_secret: str | None = None
    parser_snapshot_root: Path | None = None
    reconcile_interval_seconds: int = 30
    parser_export_retry_interval_seconds: int = 30
    parser_export_retry_batch_size: int = 25
    public_metadata_poll_interval_seconds: int = 300
    public_metadata_refresh_interval_seconds: int = 86_400
    public_metadata_retry_base_seconds: int = 30
    public_metadata_batch_size: int = 25
    public_metadata_max_attempts: int = 3
    health_probe_interval_seconds: int = 0
    health_idle_probe_after_seconds: int = 86_400
    event_retention_days: int = 90
    audit_event_retention_days: int = 90
    retention_interval_seconds: int = 300
    retention_batch_size: int = 10_000
    event_archive_path: Path | None = None
    event_archive_key: SecretStr | None = None
    event_archive_key_id: str = "default"

    @classmethod
    def from_env(cls) -> Settings:
        root: Final = Path(__file__).resolve().parents[1]
        mode_value: Final = os.environ.get("ACCOUNT_POOL_STORE", "memory").lower()
        archive_path_value: Final = os.environ.get("ACCOUNT_POOL_EVENT_ARCHIVE_PATH") or None
        archive_key_value: Final = os.environ.get("ACCOUNT_POOL_EVENT_ARCHIVE_KEY") or None
        lease_ttl_seconds: Final = int(os.environ.get("ACCOUNT_POOL_LEASE_TTL_SECONDS", "120"))
        maximum_lease_seconds: Final = int(os.environ.get("ACCOUNT_POOL_MAXIMUM_LEASE_SECONDS", "3600"))
        if mode_value not in {"memory", "redis"}:
            raise ValueError("ACCOUNT_POOL_STORE must be memory or redis")
        if lease_ttl_seconds < 1 or maximum_lease_seconds < lease_ttl_seconds:
            raise ValueError("ACCOUNT_POOL_MAXIMUM_LEASE_SECONDS must be at least ACCOUNT_POOL_LEASE_TTL_SECONDS")
        return cls(
            config_path=Path(os.environ.get("ACCOUNT_POOL_CONFIG", root / "config" / "accounts.yaml")),
            store_mode=cast(StoreMode, mode_value),
            redis_url=os.environ.get("ACCOUNT_POOL_REDIS_URL", "redis://127.0.0.1:6379/0"),
            litellm_url=os.environ.get("ACCOUNT_POOL_LITELLM_URL", "http://127.0.0.1:4000").rstrip("/"),
            litellm_admin_key=os.environ.get("ACCOUNT_POOL_LITELLM_ADMIN_KEY"),
            lease_ttl_seconds=lease_ttl_seconds,
            maximum_lease_seconds=maximum_lease_seconds,
            parser_snapshot_root=(
                Path(snapshot_root) if (snapshot_root := os.environ.get("ACCOUNT_POOL_PARSER_SNAPSHOT_ROOT")) else None
            ),
            internal_token=os.environ.get("ACCOUNT_POOL_INTERNAL_TOKEN"),
            database_url=os.environ.get("DATABASE_URL"),
            database_schema=os.environ.get("ACCOUNT_POOL_DATABASE_SCHEMA", "public"),
            actor_secret=os.environ.get("ACCOUNT_POOL_ACTOR_SECRET"),
            reconcile_interval_seconds=int(os.environ.get("ACCOUNT_POOL_RECONCILE_INTERVAL_SECONDS", "30")),
            parser_export_retry_interval_seconds=int(
                os.environ.get("ACCOUNT_POOL_PARSER_EXPORT_RETRY_INTERVAL_SECONDS", "30")
            ),
            parser_export_retry_batch_size=int(os.environ.get("ACCOUNT_POOL_PARSER_EXPORT_RETRY_BATCH_SIZE", "25")),
            public_metadata_poll_interval_seconds=int(
                os.environ.get("ACCOUNT_POOL_PUBLIC_METADATA_POLL_INTERVAL_SECONDS", "300")
            ),
            public_metadata_refresh_interval_seconds=int(
                os.environ.get("ACCOUNT_POOL_PUBLIC_METADATA_REFRESH_INTERVAL_SECONDS", "86400")
            ),
            public_metadata_retry_base_seconds=int(
                os.environ.get("ACCOUNT_POOL_PUBLIC_METADATA_RETRY_BASE_SECONDS", "30")
            ),
            public_metadata_batch_size=int(os.environ.get("ACCOUNT_POOL_PUBLIC_METADATA_BATCH_SIZE", "25")),
            public_metadata_max_attempts=int(os.environ.get("ACCOUNT_POOL_PUBLIC_METADATA_MAX_ATTEMPTS", "3")),
            health_probe_interval_seconds=int(os.environ.get("ACCOUNT_POOL_HEALTH_PROBE_INTERVAL_SECONDS", "0")),
            health_idle_probe_after_seconds=int(
                os.environ.get("ACCOUNT_POOL_HEALTH_IDLE_PROBE_AFTER_SECONDS", "86400")
            ),
            event_retention_days=int(os.environ.get("ACCOUNT_POOL_EVENT_RETENTION_DAYS", "90")),
            audit_event_retention_days=int(os.environ.get("ACCOUNT_POOL_AUDIT_EVENT_RETENTION_DAYS", "90")),
            retention_interval_seconds=int(os.environ.get("ACCOUNT_POOL_RETENTION_INTERVAL_SECONDS", "300")),
            retention_batch_size=int(os.environ.get("ACCOUNT_POOL_RETENTION_BATCH_SIZE", "10000")),
            event_archive_path=None if archive_path_value is None else Path(archive_path_value),
            event_archive_key=None if archive_key_value is None else SecretStr(archive_key_value),
            event_archive_key_id=os.environ.get("ACCOUNT_POOL_EVENT_ARCHIVE_KEY_ID", "default"),
        )


def load_pool_config(path: Path) -> PoolConfig:
    loaded: Final = cast(object, yaml.safe_load(path.read_text(encoding="utf-8")))
    raw: Final = TypeAdapter(dict[str, object]).validate_python(loaded)
    config: Final = PoolConfig.model_validate(raw)
    return config.model_copy(
        update={
            "accounts": tuple(
                account.model_copy(update={"priority": normalize_channel_priority(account.priority)})
                for account in config.accounts
            )
        }
    )


def save_pool_config(path: Path, config: PoolConfig) -> None:
    temporary: Final = path.with_suffix(f"{path.suffix}.tmp")
    rendered: Final = yaml.safe_dump(
        config.model_dump(mode="json"),
        allow_unicode=True,
        sort_keys=False,
    )
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)
