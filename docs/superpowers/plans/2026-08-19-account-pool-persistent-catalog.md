# Account Pool Persistent Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Account Pool 建立 PostgreSQL 权威渠道目录，支持安全、严格、幂等地导入现有 YAML，并无副作用地投影回当前 `PoolConfig`

**Architecture:** Account Pool 复用 LiteLLM canonical Prisma schema 和迁移目录，但通过独立、强类型的 repository 边界访问三张 Account Pool 表。第一阶段只提供持久化、导入和读取投影，不切换现有 scheduler、管理 API 或 LiteLLM 同步路径，因此当前运行行为保持不变

**Tech Stack:** Python 3.11、Pydantic 2、psycopg 3、PostgreSQL、Prisma schema/migrations、pytest

**Spec:** `account-pool/PLAN.md`

## Global Constraints

- 真实 Key 只由 LiteLLM 加密保存，Account Pool PostgreSQL、Redis、YAML、JSON、日志和 API 响应均不得持久化真实 Key
- `credential_ref` 可空，只能指向已经存在的 LiteLLM 凭据或 Deployment，不得用占位值伪装成可重试凭据
- YAML 导入只创建缺失数据，完全相同的重复导入返回 `unchanged`，任何同标识不同内容的导入必须整批拒绝
- 导入事务通过 PostgreSQL transaction-level advisory lock 串行化，避免缺失行无法被 `FOR UPDATE` 锁定的竞态
- 旧 YAML 的 `managed_by_pool=true` 映射为 `pool_managed`，否则映射为 `externally_managed`
- 迁移由根目录 `schema.prisma` 和 `litellm-proxy-extras/litellm_proxy_extras/migrations` 统一管理，三个 Prisma schema 文件必须完全一致
- SQL 必须通过 `uv run --with testing.postgresql python ci_cd/run_migration.py add_account_pool_catalog` 生成，不得手写表、索引或外键 SQL，也不得绕过 freshness/destructive guards；任一 guard 拒绝时立即停止并告知用户
- 本阶段不调用 LiteLLM 管理 API，不切换 scheduler 初始化，不修改 Redis，不实现 reconciler、解析器、健康检查、正式 UI 或管理端点
- 所有新模型和返回值保持不可变、强类型、`extra="forbid"`，不引入 `Any`、可变集合或不带原因的 lint/type suppression
- 时间字段在 Python 中使用 Pydantic `AwareDatetime`，在 PostgreSQL 中使用 `TIMESTAMPTZ(6)`；调用方传入 naive datetime 时必须在进入 repository 前失败
- Python 最大行长为 120
- 每个任务先写会失败的行为测试，再写最少实现，并单独提交 conventional commit
- 完整构建由用户执行；本计划执行者只运行列出的聚焦测试和静态检查
- 不推送任何提交

## Resolved Boundary Decisions

1. **Migration ownership:** Account Pool 表进入 LiteLLM canonical Prisma schema。Compose 已共享 LiteLLM PostgreSQL，现有 schema 同步和迁移验证可阻止独立 migration lineage 漂移
2. **Stable identity:** YAML 渠道用固定 namespace 下的 `uuid5(legacy_account_id)` 生成 `channel_id`，绑定用同一 namespace 下的 `uuid5(channel_id + litellm_model_id)` 生成 `binding_id`。数据库新增渠道仍使用随机 UUID，本规则只用于 legacy import
3. **Legacy mapping:** `legacy_account_id` 只用于从当前 YAML 投影回 `PoolConfig`，数据库允许它为空。第一阶段的 `project_pool_config` 遇到没有 legacy ID 的渠道时明确拒绝，直到管理 API 和 scheduler 完成数据库切换
4. **Credential lifecycle:** `credential_ref` 在导入阶段为 `null`，因为现有 YAML 只有 Deployment ID，没有经过验证的独立凭据 ID。后续创建或轮换阶段只在 LiteLLM 成功返回真实引用后写入
5. **Import conflicts:** `legacy_account_id`、`channel_id`、`binding_id`、`litellm_deployment_id` 或 model policy 任一命中不同内容时，repository 回滚整个事务并返回结构化冲突，不做覆盖或部分导入
6. **Ordering:** YAML 中渠道、绑定和策略的顺序会影响现有 scheduler 的稳定候选顺序，因此目录显式保存 `account_order`、`deployment_order` 和 `policy_order`，投影不得按名称重新排序
7. **Policies:** 当前 YAML 的模型策略也进入 PostgreSQL，否则从 catalog 投影 `PoolConfig` 会丢失现有调度行为。它是第三张表，但本阶段不切换 scheduler
8. **Synchronization deferral:** 同步 operation、desired/applied revision、`requires_key` 和 reconciler 同属下一阶段的管理 mutation 边界。本阶段不创建不完整的 operation 表或字段
9. **Runtime packaging:** `account-pool` 保持独立 uv project 和独立 `account-pool/uv.lock`。Account Pool 镜像从固定 digest 的 uv image 复制 uv，并使用 `uv sync --project /app/account-pool --frozen --no-dev --no-editable` 安装依赖。不把 Account Pool 加入根 workspace

## File Structure

- `account-pool/account_pool/catalog/models.py`: 持久化领域实体、枚举、导入结果和冲突类型
- `account-pool/account_pool/catalog/identity.py`: legacy YAML 的确定性 UUID 规则
- `account-pool/account_pool/catalog/importer.py`: `PoolConfig` 到原子导入命令的纯转换
- `account-pool/account_pool/catalog/projection.py`: catalog snapshot 到当前 `PoolConfig` 的纯投影
- `account-pool/account_pool/catalog/repository.py`: repository protocol
- `account-pool/account_pool/catalog/postgres.py`: psycopg PostgreSQL 实现和严格行解码
- `account-pool/account_pool/catalog/service.py`: 依赖注入的受控导入与读取服务
- `account-pool/account_pool/catalog/__init__.py`: 稳定公共接口
- `account-pool/tests/catalog/test_identity.py`: 确定性 ID 测试
- `account-pool/tests/catalog/test_importer.py`: ownership、顺序、时间和凭据隔离测试
- `account-pool/tests/catalog/test_projection.py`: 无损读取投影和拒绝条件测试
- `account-pool/tests/catalog/test_postgres_repository.py`: 实际迁移上的事务、并发、冲突和读取集成测试
- `account-pool/tests/catalog/test_catalog_service.py`: service 依赖注入和无副作用测试
- `schema.prisma`: Account Pool 三张权威表的 canonical 定义
- `litellm/proxy/schema.prisma`: canonical schema 同步副本
- `litellm-proxy-extras/litellm_proxy_extras/schema.prisma`: migration generator 使用的同步副本
- `litellm-proxy-extras/litellm_proxy_extras/migrations/<generated_timestamp>_add_account_pool_catalog/migration.sql`: 官方脚本生成的迁移
- `account-pool/pyproject.toml`: psycopg 运行依赖
- `account-pool/uv.lock`: Account Pool 独立项目锁文件
- `account-pool/Dockerfile`: 从独立 lock 安装 Account Pool runtime 依赖

---

### Task 1: Persistent catalog domain and deterministic identities

**Files:**
- Create: `account-pool/account_pool/catalog/__init__.py`
- Create: `account-pool/account_pool/catalog/models.py`
- Create: `account-pool/account_pool/catalog/identity.py`
- Create: `account-pool/tests/catalog/test_identity.py`

**Interfaces:**
- Consumes: `AccountId`, `ModelName`, `QuotaConfig`, `Strategy`, and `FrozenModel` from `account_pool.models`
- Produces: `AdministrativeState`, `BindingOwnership`, `ChannelRecord`, `DeploymentBindingRecord`, `ModelPolicyRecord`, `CatalogSnapshot`, `CatalogImport`, `ImportConflict`, `ImportResult`, `legacy_channel_id(account_id: str) -> UUID`, and `legacy_binding_id(channel_id: UUID, deployment_id: str) -> UUID`

- [ ] **Step 1: Write deterministic identity and domain validation tests**

```python
from datetime import datetime
from typing import Final
from uuid import UUID

import pytest
from pydantic import ValidationError

from account_pool.catalog.identity import legacy_binding_id, legacy_channel_id
from account_pool.catalog.models import AdministrativeState, ChannelRecord
from account_pool.models import QuotaConfig


def test_legacy_ids_are_stable_and_namespaced() -> None:
    channel: Final = legacy_channel_id("primary-east")

    assert isinstance(channel, UUID)
    assert channel == legacy_channel_id("primary-east")
    assert channel != legacy_channel_id("backup-west")
    assert channel.version == 5
    assert legacy_binding_id(channel, "deployment-a") == legacy_binding_id(channel, "deployment-a")
    assert legacy_binding_id(channel, "deployment-a") != legacy_binding_id(channel, "deployment-b")


def test_channel_requires_aware_timestamp() -> None:
    with pytest.raises(ValidationError):
        ChannelRecord(
            channel_id=legacy_channel_id("primary-east"),
            legacy_account_id="primary-east",
            account_order=0,
            display_name="Primary",
            provider="openai",
            base_url_display="https://api.openai.com",
            administrative_state=AdministrativeState.ENABLED,
            max_concurrency=1,
            priority=0,
            weight=1,
            quotas=QuotaConfig(),
            created_at=datetime(2026, 8, 19),
            updated_at=datetime(2026, 8, 19),
        )
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `uv run --project account-pool --extra test pytest account-pool/tests/catalog/test_identity.py -q`

Expected: FAIL because `account_pool.catalog` does not exist

- [ ] **Step 3: Add immutable domain models**

Implement these shapes in `catalog/models.py` using `AwareDatetime` for every timestamp:

```python
class AdministrativeState(StrEnum):
    ENABLED = "enabled"
    PAUSED = "paused"
    DISABLED = "disabled"


class BindingOwnership(StrEnum):
    POOL_MANAGED = "pool_managed"
    EXTERNALLY_MANAGED = "externally_managed"


class ChannelRecord(FrozenModel):
    channel_id: UUID
    legacy_account_id: AccountId | None = None
    account_order: int = Field(ge=0)
    display_name: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    group: str | None = None
    base_url_display: str = Field(min_length=1)
    administrative_state: AdministrativeState
    max_concurrency: int = Field(ge=1)
    priority: int
    weight: int = Field(ge=1, le=100)
    quotas: QuotaConfig
    credential_ref: str | None = None
    key_mask: str | None = None
    key_fingerprint: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class DeploymentBindingRecord(FrozenModel):
    binding_id: UUID
    channel_id: UUID
    deployment_order: int = Field(ge=0)
    public_model: ModelName
    provider_model: str | None = None
    litellm_deployment_id: str = Field(min_length=1)
    ownership: BindingOwnership
    enabled: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ModelPolicyRecord(FrozenModel):
    model: ModelName
    policy_order: int = Field(ge=0)
    strategy: Strategy
    created_at: AwareDatetime
    updated_at: AwareDatetime


class CatalogSnapshot(FrozenModel):
    channels: tuple[ChannelRecord, ...] = ()
    bindings: tuple[DeploymentBindingRecord, ...] = ()
    policies: tuple[ModelPolicyRecord, ...] = ()


class CatalogImport(FrozenModel):
    channels: tuple[ChannelRecord, ...]
    bindings: tuple[DeploymentBindingRecord, ...]
    policies: tuple[ModelPolicyRecord, ...]


class ImportConflict(FrozenModel):
    entity: Literal["channel", "binding", "policy"]
    identity: str
    reason: str


class ImportResult(FrozenModel):
    status: Literal["created", "unchanged", "conflict"]
    created_channels: int = Field(default=0, ge=0)
    created_bindings: int = Field(default=0, ge=0)
    created_policies: int = Field(default=0, ge=0)
    conflicts: tuple[ImportConflict, ...] = ()
```

Do not add synchronization revisions, operation state, `requires_key`, secret-bearing fields, or arbitrary JSON payloads

- [ ] **Step 4: Add deterministic UUID functions**

```python
_LEGACY_NAMESPACE: Final = UUID("6ad855d6-8eb9-5ef5-b52c-610a24a7fc55")


def legacy_channel_id(account_id: str) -> UUID:
    return uuid5(_LEGACY_NAMESPACE, f"channel:{account_id}")


def legacy_binding_id(channel_id: UUID, deployment_id: str) -> UUID:
    return uuid5(_LEGACY_NAMESPACE, f"binding:{channel_id}:{deployment_id}")
```

- [ ] **Step 5: Export the contract and run tests**

Run: `uv run --project account-pool --extra test pytest account-pool/tests/catalog/test_identity.py -q`

Expected: PASS

- [ ] **Step 6: Commit the domain contract**

```bash
git add account-pool/account_pool/catalog account-pool/tests/catalog/test_identity.py
git commit -m "feat(account-pool): define persistent catalog domain"
```

---

### Task 2: Order-preserving YAML import and projection

**Files:**
- Create: `account-pool/account_pool/catalog/importer.py`
- Create: `account-pool/account_pool/catalog/projection.py`
- Create: `account-pool/tests/catalog/test_importer.py`
- Create: `account-pool/tests/catalog/test_projection.py`

**Interfaces:**
- Consumes: `PoolConfig`, deterministic identities, and records from Task 1
- Produces: `catalog_import_from_pool_config(config: PoolConfig, imported_at: AwareDatetime) -> CatalogImport` and `project_pool_config(snapshot: CatalogSnapshot) -> PoolConfig`

- [ ] **Step 1: Write importer behavior tests**

Create a `PoolConfig` whose account, Deployment, and policy names are intentionally not alphabetic. Assert input tuple order and ownership are retained:

```python
result: Final = catalog_import_from_pool_config(config, imported_at)

assert tuple(channel.legacy_account_id for channel in result.channels) == ("z-channel", "a-channel")
assert tuple(channel.account_order for channel in result.channels) == (0, 1)
assert tuple(binding.deployment_order for binding in result.bindings if binding.channel_id == result.channels[0].channel_id) == (0, 1)
assert tuple(policy.policy_order for policy in result.policies) == (0, 1)
assert result.bindings[0].ownership == BindingOwnership.EXTERNALLY_MANAGED
assert result.bindings[1].ownership == BindingOwnership.POOL_MANAGED
assert all(channel.credential_ref is None for channel in result.channels)
assert "provider-secret" not in result.model_dump_json()
```

Add tests proving disabled YAML accounts map to `AdministrativeState.DISABLED`, IDs remain deterministic, and a naive `imported_at` raises `ValidationError`

- [ ] **Step 2: Run importer tests and verify they fail**

Run: `uv run --project account-pool --extra test pytest account-pool/tests/catalog/test_importer.py -q`

Expected: FAIL because `catalog_import_from_pool_config` does not exist

- [ ] **Step 3: Implement the pure importer**

Use tuple comprehensions and `enumerate` without mutation. Apply these mappings:

```text
AccountConfig.id                 -> ChannelRecord.legacy_account_id
account tuple index              -> ChannelRecord.account_order
AccountConfig.enabled=true       -> enabled
AccountConfig.enabled=false      -> disabled
DeploymentConfig.managed_by_pool -> pool_managed, otherwise externally_managed
deployment tuple index           -> DeploymentBindingRecord.deployment_order
DeploymentConfig.litellm_model_id -> binding identity and litellm_deployment_id
policy tuple index               -> ModelPolicyRecord.policy_order
credential_ref/key_mask/key_fingerprint -> null
created_at/updated_at            -> imported_at
```

Preserve source tuple order exactly. Do not sort by account ID, channel UUID, Deployment ID, or model

- [ ] **Step 4: Run importer tests**

Run: `uv run --project account-pool --extra test pytest account-pool/tests/catalog/test_importer.py -q`

Expected: PASS

- [ ] **Step 5: Write projection tests**

Use importer output as the snapshot and assert exact round-trip equality for deliberately non-alphabetic input:

```python
snapshot: Final = CatalogSnapshot(
    channels=imported.channels,
    bindings=imported.bindings,
    policies=imported.policies,
)

assert project_pool_config(snapshot) == source
```

Add focused tests proving projection:

1. Rejects orphan bindings
2. Rejects duplicate LiteLLM Deployment IDs through `PoolConfig` validation
3. Rejects a channel whose `legacy_account_id` is `None`
4. Maps `PAUSED` to `enabled=False` without inventing a legacy `Health` state
5. Uses `account_order`, per-channel `deployment_order`, and `policy_order`, regardless of snapshot row order

- [ ] **Step 6: Run projection tests and verify they fail**

Run: `uv run --project account-pool --extra test pytest account-pool/tests/catalog/test_projection.py -q`

Expected: FAIL because `project_pool_config` does not exist

- [ ] **Step 7: Implement pure projection**

Reject orphan bindings and channels without a legacy ID before constructing `PoolConfig`. Group bindings by `channel_id`, then construct immutable tuples ordered by persisted order columns:

```text
channels                    -> account_order
bindings within each channel -> deployment_order
policies                    -> policy_order
legacy_account_id           -> AccountConfig.id
administrative_state enabled -> AccountConfig.enabled=true
paused or disabled          -> AccountConfig.enabled=false
pool_managed ownership      -> DeploymentConfig.managed_by_pool=true
```

Let `PoolConfig` enforce account, Deployment, and policy identity uniqueness

- [ ] **Step 8: Run pure catalog tests**

```bash
uv run --project account-pool --extra test pytest account-pool/tests/catalog/test_identity.py account-pool/tests/catalog/test_importer.py account-pool/tests/catalog/test_projection.py -q
```

Expected: PASS

- [ ] **Step 9: Commit import and projection**

```bash
git add account-pool/account_pool/catalog account-pool/tests/catalog
git commit -m "feat(account-pool): map legacy config to catalog"
```

---

### Task 3: Canonical Prisma schema and generated migration

**Files:**
- Modify: `schema.prisma`
- Modify: `litellm/proxy/schema.prisma`
- Modify: `litellm-proxy-extras/litellm_proxy_extras/schema.prisma`
- Create: `litellm-proxy-extras/litellm_proxy_extras/migrations/<generated_timestamp>_add_account_pool_catalog/migration.sql`

**Interfaces:**
- Consumes: persisted fields and constraints from Tasks 1 and 2
- Produces: `LiteLLM_AccountPoolChannel`, `LiteLLM_AccountPoolBinding`, and `LiteLLM_AccountPoolModelPolicy` PostgreSQL tables

- [ ] **Step 1: Establish the pre-change migration check**

Run: `uv run prisma validate --schema schema.prisma`

Expected: PASS before schema edits, establishing that later failures come from this task

- [ ] **Step 2: Add three Prisma models to the canonical schema**

Use String columns for domain enum values and native timezone-aware timestamps:

```prisma
model LiteLLM_AccountPoolChannel {
  channel_id            String   @id @default(uuid())
  legacy_account_id     String?  @unique
  account_order         Int      @unique
  display_name          String
  provider              String
  channel_group         String?
  base_url_display      String
  administrative_state String
  max_concurrency       Int
  priority              Int
  weight                Int
  quota_unit            String
  quota_total           Float?
  quota_five_hour       Float?
  quota_weekly          Float?
  credential_ref        String?
  key_mask              String?
  key_fingerprint       String?
  created_at            DateTime @default(now()) @db.Timestamptz(6)
  updated_at            DateTime @updatedAt @db.Timestamptz(6)
  bindings              LiteLLM_AccountPoolBinding[]
}

model LiteLLM_AccountPoolBinding {
  binding_id            String   @id @default(uuid())
  channel_id            String
  deployment_order      Int
  public_model          String
  provider_model        String?
  litellm_deployment_id String   @unique
  ownership             String
  enabled               Boolean  @default(true)
  created_at            DateTime @default(now()) @db.Timestamptz(6)
  updated_at            DateTime @updatedAt @db.Timestamptz(6)
  channel               LiteLLM_AccountPoolChannel @relation(fields: [channel_id], references: [channel_id], onDelete: Cascade)

  @@index([channel_id])
  @@unique([channel_id, deployment_order])
}

model LiteLLM_AccountPoolModelPolicy {
  model        String   @id
  policy_order Int      @unique
  strategy     String
  created_at   DateTime @default(now()) @db.Timestamptz(6)
  updated_at   DateTime @updatedAt @db.Timestamptz(6)
}
```

The migration must additionally enforce enum-domain and numeric checks not expressible in Prisma:

```sql
CHECK ("administrative_state" IN ('enabled', 'paused', 'disabled'))
CHECK ("ownership" IN ('pool_managed', 'externally_managed'))
CHECK ("quota_unit" IN ('tokens', 'usd'))
CHECK ("account_order" >= 0)
CHECK ("deployment_order" >= 0)
CHECK ("policy_order" >= 0)
CHECK ("max_concurrency" >= 1)
CHECK ("weight" BETWEEN 1 AND 100)
```

Do not add operation tables, revision columns, `requires_key`, secret columns, or arbitrary JSON payloads

- [ ] **Step 3: Sync the schema copies byte-for-byte**

```bash
cp schema.prisma litellm/proxy/schema.prisma
cp schema.prisma litellm-proxy-extras/litellm_proxy_extras/schema.prisma
cmp schema.prisma litellm/proxy/schema.prisma
cmp schema.prisma litellm-proxy-extras/litellm_proxy_extras/schema.prisma
```

Expected: both `cmp` commands exit 0

- [ ] **Step 4: Generate the migration with the canonical script**

Run exactly:

```bash
uv run --with testing.postgresql python ci_cd/run_migration.py add_account_pool_catalog
```

Expected: a dynamically timestamped `*_add_account_pool_catalog/migration.sql` is created. If branch freshness or destructive migration protection refuses, stop and ask the user; never pass `--skip-freshness-check` or `--allow-destructive`

- [ ] **Step 5: Review generated SQL and add required checks if the generator omitted them**

Prisma diff generates tables, indexes, uniqueness, and foreign keys. It cannot model the domain checks above, so append only those `ALTER TABLE ... ADD CONSTRAINT ... CHECK` statements to the generated migration. Confirm the migration contains exactly three new tables, `TIMESTAMPTZ(6)`, the channel-to-binding cascade, all order uniqueness constraints, and no destructive statement

- [ ] **Step 6: Run schema and migration checks**

```bash
uv run prisma validate --schema schema.prisma
cmp schema.prisma litellm/proxy/schema.prisma
cmp schema.prisma litellm-proxy-extras/litellm_proxy_extras/schema.prisma
git diff --check
```

If `DATABASE_URL` is available, also run:

```bash
uv run pytest tests/proxy_migration_tests/test_db_schema_migration.py::test_schema_migration_in_sync -q
```

Expected: all available checks pass. Record the database migration test as skipped when `DATABASE_URL` is absent

- [ ] **Step 7: Commit schema and generated migration**

```bash
git add schema.prisma litellm/proxy/schema.prisma litellm-proxy-extras/litellm_proxy_extras/schema.prisma litellm-proxy-extras/litellm_proxy_extras/migrations
git commit -m "feat(account-pool): add persistent catalog schema"
```

---

### Task 4: PostgreSQL repository and runtime packaging

**Files:**
- Create: `account-pool/account_pool/catalog/repository.py`
- Create: `account-pool/account_pool/catalog/postgres.py`
- Create: `account-pool/tests/catalog/test_postgres_repository.py`
- Modify: `account-pool/account_pool/catalog/__init__.py`
- Modify: `account-pool/pyproject.toml`
- Create: `account-pool/uv.lock`
- Modify: `account-pool/Dockerfile`

**Interfaces:**
- Consumes: `CatalogImport`, `CatalogSnapshot`, and `ImportResult` from Task 1; generated migration from Task 3
- Produces: `CatalogRepository` protocol and `PostgresCatalogRepository(database_url: str, schema: str = "public")`

- [ ] **Step 1: Add and lock the runtime dependency**

Add `psycopg[binary]>=3.3,<4` to `account-pool/pyproject.toml`, then run:

```bash
uv lock --project account-pool
uv sync --project account-pool --extra test --frozen
uv run --project account-pool python -c "import psycopg"
```

Expected: `account-pool/uv.lock` is created, resolves independently of the root workspace, and psycopg imports successfully

- [ ] **Step 2: Write PostgreSQL integration fixtures using the actual migration**

The module skips only when `DATABASE_URL` is absent. Its fixture must:

1. Validate a generated migration directory matching `*_add_account_pool_catalog` exists exactly once
2. Read that committed `migration.sql`, rather than maintaining test-local DDL
3. Create a unique PostgreSQL schema using a generated safe identifier
4. Set the connection `search_path` to that schema and execute the actual migration SQL
5. Yield `PostgresCatalogRepository(database_url, schema=schema_name)`
6. Drop the schema with `CASCADE` in `finally`

The repository constructor validates `schema` against `^[A-Za-z_][A-Za-z0-9_]*$`. Each transaction uses parameterized `set_config('search_path', schema, true)` before queries; never interpolate an unchecked identifier

- [ ] **Step 3: Write repository behavior and concurrency tests**

Test these behaviors against the migrated isolated schema:

1. Empty `load_snapshot()` returns empty tuples
2. First `import_once()` returns `created` and exact counts
3. Repeating the exact command with different timestamps returns `unchanged` and no duplicate rows
4. Same `legacy_account_id` or `channel_id` with different business content returns channel conflicts and changes no table
5. Same `binding_id` or `litellm_deployment_id` with different business content returns binding conflicts and changes no table
6. A policy conflict rolls back channels and bindings from the same command
7. Snapshot reads return exact domain records ordered by persisted order columns
8. Two repositories on independent connections concurrently importing the same absent command produce one `created`, one `unchanged`, and one copy of every row
9. Two concurrent conflicting imports produce one accepted result and one structured conflict, never an uncaught uniqueness error or partial rows

Coordinate concurrent tests with an `asyncio.Event` start barrier and `asyncio.gather`; do not use sleeps or monkeypatch class attributes

- [ ] **Step 4: Run repository tests and verify they fail**

Run: `uv run --project account-pool --extra test pytest account-pool/tests/catalog/test_postgres_repository.py -q`

Expected: FAIL because repository modules do not exist, or SKIP only when `DATABASE_URL` is absent

- [ ] **Step 5: Define the repository protocol**

```python
class CatalogRepository(Protocol):
    async def load_snapshot(self) -> CatalogSnapshot: ...

    async def import_once(self, command: CatalogImport) -> ImportResult: ...
```

The protocol exposes only domain values, never connections, cursors, SQL rows, or transactions

- [ ] **Step 6: Implement strict PostgreSQL row decoding and ordered reads**

Use psycopg async connections with `dict_row`. Validate each row through frozen private Pydantic models before converting to public records. Every enum string, UUID, aware timestamp, and nullable legacy ID must be validated before leaving `postgres.py`

For each transaction, call parameterized `set_config` first. Read channels by `account_order`, bindings by `(channel_id, deployment_order)`, and policies by `policy_order`, returning immutable tuples. Do not expose credentials beyond the allowed reference, mask, and fingerprint fields

- [ ] **Step 7: Implement serialized, all-or-nothing `import_once`**

Within one transaction:

1. Execute `SELECT pg_advisory_xact_lock(%s)` with a fixed Account Pool catalog lock key
2. Load rows matching all alternate identities: channel ID and legacy ID, binding ID and LiteLLM Deployment ID, policy model
3. Compare persisted business fields while excluding `created_at` and `updated_at`
4. Collect every mismatch as an `ImportConflict`
5. If conflicts exist, return `conflict` without inserting anything
6. Otherwise insert only missing rows and return `created` when any count is nonzero, else `unchanged`

Use parameter binding for every value. Do not turn connectivity, decoding, SQL, or integrity failures into business conflicts

- [ ] **Step 8: Run repository and pure catalog tests**

```bash
uv run --project account-pool --extra test pytest account-pool/tests/catalog -q
```

Expected: all tests pass when `DATABASE_URL` exists; only PostgreSQL integration tests skip when it does not

- [ ] **Step 9: Install the independent Account Pool environment in Docker**

Update `account-pool/Dockerfile` to copy uv from the same pinned version and digest used by the root Dockerfile, copy `account-pool/pyproject.toml`, `account-pool/uv.lock`, and the package, then run:

```dockerfile
RUN uv sync --project /app/account-pool --frozen --no-dev --no-editable \
    && /app/account-pool/.venv/bin/python -c "import psycopg"
```

Change the entrypoint to `/app/account-pool/.venv/bin/python`. Do not rely on root development dependencies and do not add Account Pool to the root uv workspace

- [ ] **Step 10: Verify lock and Docker contract without a full build**

```bash
uv lock --project account-pool --check
uv run --project account-pool python -c "import psycopg"
git diff --check
```

Expected: PASS. The user owns the complete Docker build, so report it as not run

- [ ] **Step 11: Commit repository and packaging**

```bash
git add account-pool/account_pool/catalog account-pool/tests/catalog account-pool/pyproject.toml account-pool/uv.lock account-pool/Dockerfile
git commit -m "feat(account-pool): add PostgreSQL catalog repository"
```

---

### Task 5: Controlled catalog service without runtime cutover

**Files:**
- Create: `account-pool/account_pool/catalog/service.py`
- Create: `account-pool/tests/catalog/test_catalog_service.py`
- Modify: `account-pool/account_pool/catalog/__init__.py`

**Interfaces:**
- Consumes: `CatalogRepository`, `catalog_import_from_pool_config`, and `project_pool_config`
- Produces: `CatalogService(repository: CatalogRepository)`, `async import_legacy_config(config: PoolConfig, imported_at: AwareDatetime) -> ImportResult`, and `async projected_config() -> PoolConfig`

- [ ] **Step 1: Write service tests with an injected fake repository**

Define a typed fake implementing `CatalogRepository`, without monkeypatching. Verify:

```python
result: Final = await service.import_legacy_config(config, imported_at)
projected: Final = await service.projected_config()

assert result.status == "created"
assert projected == config
assert fake.last_import is not None
assert "provider-secret" not in fake.last_import.model_dump_json()
```

Add tests proving a structured conflict is returned unchanged without calling `load_snapshot`, and `projected_config()` does not call `import_once`

- [ ] **Step 2: Run service tests and verify they fail**

Run: `uv run --project account-pool --extra test pytest account-pool/tests/catalog/test_catalog_service.py -q`

Expected: FAIL because `CatalogService` does not exist

- [ ] **Step 3: Implement the minimal service**

```python
class CatalogService:
    def __init__(self, repository: CatalogRepository) -> None:
        self._repository = repository

    async def import_legacy_config(self, config: PoolConfig, imported_at: AwareDatetime) -> ImportResult:
        command: Final = catalog_import_from_pool_config(config=config, imported_at=imported_at)
        return await self._repository.import_once(command)

    async def projected_config(self) -> PoolConfig:
        snapshot: Final = await self._repository.load_snapshot()
        return project_pool_config(snapshot)
```

Do not add environment lookup, startup import, fallback to YAML, scheduler reconfiguration, LiteLLM calls, or management mutations

- [ ] **Step 4: Run focused Account Pool tests**

```bash
uv run --project account-pool --extra test pytest account-pool/tests/catalog -q
uv run --project account-pool --extra test pytest account-pool/tests -q
```

Expected: catalog and existing Account Pool suites pass. PostgreSQL tests may skip only when `DATABASE_URL` is absent

- [ ] **Step 5: Run focused static checks**

```bash
uv run ruff check account-pool/account_pool/catalog account-pool/tests/catalog
uv run basedpyright account-pool/account_pool/catalog account-pool/tests/catalog
git diff --check
```

Expected: PASS without new suppressions. If these changes lower any strict/type budget, run `make lint-budget-update` and include the reduced budget files in the relevant commit

- [ ] **Step 6: Verify scope exclusions**

```bash
git diff -- account-pool/account_pool/app.py account-pool/account_pool/management.py account-pool/account_pool/scheduler.py account-pool/account_pool/store.py docker-compose.yml
```

Expected: no diff. No parser, health, restriction, Redis, Dashboard, standalone UI, scheduler, management mutation, or synchronization execution file is changed

- [ ] **Step 7: Commit the controlled service**

```bash
git add account-pool/account_pool/catalog account-pool/tests/catalog
git commit -m "feat(account-pool): add controlled catalog service"
```

---

## Final Review

- [ ] Confirm the generated migration is additive, creates exactly three tables, and all three Prisma schema files are byte-identical
- [ ] Confirm repository integration tests execute the committed migration rather than a duplicate DDL fixture
- [ ] Confirm exact reruns are unchanged even when `imported_at` differs
- [ ] Confirm simultaneous imports are serialized by `pg_advisory_xact_lock` and never race into uniqueness errors
- [ ] Confirm conflicts leave all three tables unchanged
- [ ] Confirm YAML account, Deployment, and policy order round-trips exactly
- [ ] Confirm projection rejects channels without `legacy_account_id`
- [ ] Confirm Python timestamps reject naive values and PostgreSQL columns are `TIMESTAMPTZ(6)`
- [ ] Confirm `git grep -n -E 'api_key|Authorization|Cookie' -- account-pool/account_pool/catalog` finds no persisted field or SQL column
- [ ] Confirm no synchronization operation, revision, `requires_key`, parser, health, restriction, Redis, scheduler cutover, management mutation, reconciliation, or final UI implementation entered this delivery
- [ ] Confirm `account-pool/uv.lock` exists, root `uv.lock` is unchanged, and the Dockerfile uses the independent Account Pool environment
- [ ] Confirm focused test and static-check results are reported accurately, including PostgreSQL skips and the unrun full build
- [ ] Confirm commits remain local and no push occurred

## Deferred Deliveries

The next independently reviewable delivery will cut management mutations over to desired-state-first PostgreSQL operations and add LiteLLM synchronization and reconciliation. Scheduler startup cutover follows only after that path proves catalog consistency. Parser snapshots, health, restrictions, Redis recovery, final scheduling, and Dashboard UI remain separate plans
