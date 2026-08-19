"""读取环境变量和 YAML 配置，并以原子替换方式持久化号池配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

import yaml
from pydantic import TypeAdapter

from account_pool.models import PoolConfig

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
    database_url: str | None = None
    database_schema: str = "public"
    actor_secret: str | None = None
    reconcile_interval_seconds: int = 30
    parser_export_retry_interval_seconds: int = 30
    parser_export_retry_batch_size: int = 25

    @classmethod
    def from_env(cls) -> Settings:
        root: Final = Path(__file__).resolve().parents[1]
        mode_value: Final = os.environ.get("ACCOUNT_POOL_STORE", "memory").lower()
        if mode_value not in {"memory", "redis"}:
            raise ValueError("ACCOUNT_POOL_STORE must be memory or redis")
        return cls(
            config_path=Path(os.environ.get("ACCOUNT_POOL_CONFIG", root / "config" / "accounts.yaml")),
            store_mode=cast(StoreMode, mode_value),
            redis_url=os.environ.get("ACCOUNT_POOL_REDIS_URL", "redis://127.0.0.1:6379/0"),
            litellm_url=os.environ.get("ACCOUNT_POOL_LITELLM_URL", "http://127.0.0.1:4000").rstrip("/"),
            litellm_admin_key=os.environ.get("ACCOUNT_POOL_LITELLM_ADMIN_KEY"),
            lease_ttl_seconds=int(os.environ.get("ACCOUNT_POOL_LEASE_TTL_SECONDS", "120")),
            internal_token=os.environ.get("ACCOUNT_POOL_INTERNAL_TOKEN"),
            database_url=os.environ.get("DATABASE_URL"),
            database_schema=os.environ.get("ACCOUNT_POOL_DATABASE_SCHEMA", "public"),
            actor_secret=os.environ.get("ACCOUNT_POOL_ACTOR_SECRET"),
            reconcile_interval_seconds=int(os.environ.get("ACCOUNT_POOL_RECONCILE_INTERVAL_SECONDS", "30")),
            parser_export_retry_interval_seconds=int(
                os.environ.get("ACCOUNT_POOL_PARSER_EXPORT_RETRY_INTERVAL_SECONDS", "30")
            ),
            parser_export_retry_batch_size=int(
                os.environ.get("ACCOUNT_POOL_PARSER_EXPORT_RETRY_BATCH_SIZE", "25")
            ),
        )


def load_pool_config(path: Path) -> PoolConfig:
    loaded: Final = cast(object, yaml.safe_load(path.read_text(encoding="utf-8")))
    raw: Final = TypeAdapter(dict[str, object]).validate_python(loaded)
    return PoolConfig.model_validate(raw)


def save_pool_config(path: Path, config: PoolConfig) -> None:
    temporary: Final = path.with_suffix(f"{path.suffix}.tmp")
    rendered: Final = yaml.safe_dump(
        config.model_dump(mode="json"),
        allow_unicode=True,
        sort_keys=False,
    )
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)
