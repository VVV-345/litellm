<!-- 本文件说明 Account Pool 的定位、配置、启动方式、接口与安全边界。 -->

# LiteLLM Account Pool

Account Pool 是 LiteLLM 旁路运行的账号级控制面。LiteLLM 继续负责供应商协议、加密保存 Key 和 Deployment；
Account Pool 负责渠道配置、共享并发、租约、额度快照、健康状态和模型到具体 Deployment 的选择。Rust AI Gateway
读取其无密钥运行时快照，负责客户端请求的调度和转发

## 一体启动

从仓库根目录执行：

```powershell
# 首次启动前在 .env 中设置随机强令牌，两个服务会读取同一个值。
$env:ACCOUNT_POOL_INTERNAL_TOKEN = "replace-with-a-random-service-token"
$env:ACCOUNT_POOL_ACTOR_SECRET = "replace-with-another-random-secret-at-least-32-bytes"
docker compose up --build
```

启动后：

- 号池调度器 UI：`http://127.0.0.1:4100/`
- LiteLLM Admin UI：`http://127.0.0.1:4000/ui/`
- Rust 号池网关：`http://127.0.0.1:4001/`
- 号池健康检查：`http://127.0.0.1:4100/healthz`
- 网关就绪检查：`http://127.0.0.1:4001/health/readiness`
- Redis 仅在 Compose 内网提供服务

进入 4100 调度器 UI，使用 LiteLLM 管理令牌登录，即可配置渠道、调度策略和实时路由。渠道创建仍复用
LiteLLM `/model/new` 的 Deployment 管理链路。LiteLLM 与号池必须使用相同的 `ACCOUNT_POOL_INTERNAL_TOKEN` 和
`ACCOUNT_POOL_ACTOR_SECRET`；Compose 会把 `.env` 中的值同步给两个服务。前者证明内部服务身份，后者用于签发
绑定管理员、请求 ID 和授权动作的短时 actor 信封

`account-pool/config/accounts.yaml` 是可写的非敏感配置。API Key 通过 LiteLLM `/model/new` 写入其数据库，
不会进入该 YAML、Redis、日志或管理 API 响应

## 渠道模块

当前仅保留一个通用解析器：

```text
account_pool/provider_services/
├── contracts.py          # 所有渠道实现的统一协议
├── registry.py           # 模块注册和按 provider_id 分发
├── parser_registry.py    # 组装无凭证解析器选择注册表
├── http_response.py      # 共用响应体大小限制
└── generic/
    ├── manifest.py       # 通用解析器的能力声明
    ├── schemas.py        # 兼容模型和价格接口的响应模型
    ├── client.py         # HTTPS 请求、登录重试和地址安全限制
    ├── service.py        # 模型发现与价格读取结果转换
    └── parser.py         # 转换为统一 ParserRun
```

渠道厂商只负责资源侧模型发现，解析器独立负责读取模型价格、套餐和额度。当前通用解析器支持兼容 `GET /models`，
并尝试读取兼容 `GET /api/pricing`；价格接口不可用时仍保留发现到的模型，供管理员人工补充按量价格或套餐余量

解析器选择使用独立 `ParserRegistry`。选择请求的 schema 不包含 Key，并拒绝额外凭证字段，因此选择过程不会把 Key
依次发送给多个厂商试探。未知的显式 parser 不会静默降级到其他自动 parser，而是进入人工修正

解析框架按职责拆分：`models.py` 定义统一结果，`registry.py` 只做无凭证选择，`persistence.py` 定义持久化与
导出状态，`postgres/` 分离 SQL、行编解码和事务编排，`worker.py` 编排解析、提交和快照。新增厂商解析器不需要
修改 PostgreSQL 仓储或 Worker 中的渠道分支

## 通用解析器

通用解析器只允许 HTTPS，拒绝 URL 用户信息、查询参数、片段、重定向、私网解析结果和超出大小限制的响应。
它先使用 Key 获取 `/models`，然后尝试从 API Base 去除 `/v1` 后请求 `/api/pricing`。如价格接口要求管理员身份，
填写的管理员账号和密码会用于登录后重试一次。接口仍不可用时，模型发现结果不会丢失，用户可以在解析数据中补充价格
或套餐。套餐必须显式勾选覆盖的模型，余量为零时只停用对应模型；套餐内模型以零边际成本参与成本排序

## 公开元数据后台队列

公开元数据解析使用独立 PostgreSQL 队列，可由多个 Account Pool worker 通过 `FOR UPDATE SKIP LOCKED` 竞争认领。
任务只保存渠道、Provider、解析运行和状态 ID；执行时从渠道目录读取当前 URL。URL、Key、凭证引用、请求或响应正文
不会写入任务或运行事件。传输失败采用有上限的指数退避，worker 失联任务会恢复为等待重试或永久失败，每次重试使用
新的 parser run ID，避免不同结果复用同一幂等键

Provider 只有显式注册无凭证公开来源后才会进入该队列。通用解析器没有无凭证账户元数据接口，因此默认不注册，
后台队列不会请求它，也不会从公开页面猜测余额、套餐或实际价格。新增来源
需在对应 `provider_services/{provider_id}/public_metadata.py` 内实现，具体安全和字段契约见 `PARSER_TEMPLATE.md`

可通过以下环境变量调整队列：

- `ACCOUNT_POOL_PUBLIC_METADATA_POLL_INTERVAL_SECONDS`：扫描周期，默认 300 秒
- `ACCOUNT_POOL_PUBLIC_METADATA_REFRESH_INTERVAL_SECONDS`：同一渠道刷新间隔，默认 86400 秒
- `ACCOUNT_POOL_PUBLIC_METADATA_RETRY_BASE_SECONDS`：指数退避基数，默认 30 秒
- `ACCOUNT_POOL_PUBLIC_METADATA_BATCH_SIZE`：单轮最大任务数，默认 25
- `ACCOUNT_POOL_PUBLIC_METADATA_MAX_ATTEMPTS`：最大尝试次数，默认 3

## 解析运行持久化与 JSON 快照

统一解析结果可通过 `ParserSnapshotStore` 导出到 `account-pool/data/parser-snapshots/`。`latest.json` 以渠道 UUID
为键，history 按 `{channel_id}/{parser_run_id}.json` 保存；每个文件都包含 schema 版本、原始规范化结果、人工
覆盖后的有效结果、模型发现和脱敏问题报告。写入采用同目录临时文件原子替换，history 或 latest 失败会返回结构化
结果供后续 worker 重试，不破坏已有 latest；旧运行补写 history 时不会覆盖同渠道更新的 latest

快照只接受强类型解析结果，写入前拒绝 URL、认证头、Cookie、credential reference 和 Key 指纹等敏感内容。
`account-pool/data/parser-snapshots/` 已加入 Git 忽略规则

PostgreSQL 是权威数据源。parser run、套餐、额度窗口、按量分组、模型价格和计费路由分别写入规范化表，问题报告、
证据和未解析字段只以通过 Pydantic 验证的 JSONB 附属字段保存。`ParserWorker` 的固定顺序是：选择并执行解析器、
提交完整数据库事务、读取人工覆盖并合成有效结果、导出 JSON、记录导出状态。覆盖读取或快照失败不会回滚原始解析
数据；待导出的运行由 `retry_exports()` 再处理。同一 `parser_run_id` 与相同内容幂等，不同内容会返回冲突

人工覆盖以不可变事件链保存设置、修改和撤销，使用字段语义 ID 定位，不依赖数组下标。事件记录来源 parser run、
修改前后值、操作者、角色、请求 ID、原因和时间；重新解析只产生新的 raw result，当前有效覆盖会重新合成到
effective result。无效或已不存在的目标会形成脱敏结构化失败，不阻止其他有效覆盖导出

解析历史和 raw/effective 有效数据已分别通过 `GET /api/channels/{id}/parser-runs` 与
`GET /api/channels/{id}/effective-data` 提供，并由 LiteLLM 同域代理转发。人工覆盖的设置和撤销也已通过 LiteLLM
管理员代理接入；代理在服务端签发短时 actor 信封。4100 独立 UI 的渠道生命周期、模型策略和候选覆盖写操作会把当前管理员令牌
转发给 LiteLLM 的固定管理代理端点，由 LiteLLM 校验真实管理员并签发 actor；浏览器不接触内部服务令牌或 actor
密钥。解析字段覆盖仍在 LiteLLM Dashboard 管理，4100 只展示其进入运行路由后的结果。
`GET /api/channels/{id}/snapshot` 和 `/export` 从 PostgreSQL 最新结果即时生成以渠道 ID 为键的 schema v1 脱敏文档，
后者附带下载响应头。`POST /api/channels/{id}/parse` 接收一次性 Key，在当前 Account Pool 实例接管后返回任务 ID；
任务所有权、心跳和结果写入 PostgreSQL，但 URL、Key 和 Key 指纹不会进入任务记录。进程中断后的超时任务会变为
`interrupted_requires_key`，不会由其他实例接管或自动重试。`POST /api/channels/{id}/import` 接受单渠道 schema v1
脱敏文档，只把 effective result 差异原子转换为可审计人工
覆盖，不替换 parser run 或快照文件。后台导出重试循环与 Dashboard 字段差异、人工覆盖界面均已接入

## 本地开发

不使用 Compose 时，需要单独启动服务：

```powershell
$env:PYTHONPATH = "account-pool"
$env:ACCOUNT_POOL_STORE = "memory"
$env:ACCOUNT_POOL_CONFIG = "account-pool/config/accounts.yaml"
$env:ACCOUNT_POOL_LITELLM_URL = "http://127.0.0.1:4000"
$env:ACCOUNT_POOL_LITELLM_ADMIN_KEY = "your-litellm-master-key"
$env:ACCOUNT_POOL_INTERNAL_TOKEN = "your-service-token"
$env:ACCOUNT_POOL_ACTOR_SECRET = "your-separate-random-secret-at-least-32-bytes"
$env:ACCOUNT_POOL_LEASE_TTL_SECONDS = "120"
$env:ACCOUNT_POOL_MAXIMUM_LEASE_SECONDS = "3600"
$env:DATABASE_URL = "postgresql://user:password@127.0.0.1:5432/litellm"
.\.venv\Scripts\python.exe -m uvicorn account_pool.app:app --host 127.0.0.1 --port 4100
```

所有 `/api/*` 和 `/internal/*` 接口都要求 `X-Account-Pool-Token`；未配置服务令牌时接口会拒绝服务，
不会退化为无认证访问。人工覆盖和调度写接口还要求 LiteLLM 使用 `ACCOUNT_POOL_ACTOR_SECRET` 签发 actor 信封；
4100 调度工作台通过 `/ui-api/channels*` 使用正式渠道契约，并通过受限的 `/ui-api/models/*` 管理调度；所有写请求
由后端交给 LiteLLM 管理代理，不能由浏览器直接调用内部写接口。旧 `/ui-api/accounts*` 仅保留为发布期兼容别名，
4100 页面不再依赖。`/healthz` 保持无认证，供容器健康检查使用

`DATABASE_URL` 未配置时，现有调度和渠道接口仍可启动，4100 路由表保持只读；正式策略版本、候选人工覆盖、解析
历史和有效数据接口返回 503。可通过 `ACCOUNT_POOL_DATABASE_SCHEMA` 指定非 `public` schema

`ACCOUNT_POOL_LEASE_TTL_SECONDS` 是心跳租约长度，默认 120 秒；
`ACCOUNT_POOL_MAXIMUM_LEASE_SECONDS` 是单次请求不可延长的绝对上限，默认 3600 秒且不能小于心跳租约。
流式请求由 Rust 网关（4001）在后台续租，但到达绝对上限后仍会中止。Redis 数据集丢失后，新代次至少隔离到故障前租约的
绝对截止时间，旧代次的 settle、release 和 heartbeat 不会修改新代次

Redis 丢失、多 Worker 和迟到回调的目标环境验证步骤见 `deploy/redis/RUNBOOK.md`
额度运行态使用 Redis schema v2 标记；升级后首次发现旧状态时会创建恢复代次并执行最长租约隔离

## Worker 监控与 Prometheus 指标

`GET /metrics` 提供 Prometheus exposition 文本，无需认证，便于集群抓取。该端点只使用固定 Worker 名称标签，
不包含渠道 ID、URL、Key、request_id、模型名、上游错误或响应正文。`GET /api/workers` 返回带时间戳的 Worker
状态详情，并要求 `X-Account-Pool-Token`

当前监控覆盖 `lease_reaper`、`channel_reconciler`、`parser_export_retry`、`public_metadata` 和
`active_health_probe`。状态包括 `disabled`、`starting`、`healthy`、`degraded`、`stalled` 和 `stopped`；运行中
Worker 超过两个预期间隔没有完成周期时动态标记为 `stalled`。指标提供启用与存活状态、周期总数、成功与失败总数、
连续失败、最后成功或失败时间及最近周期耗时

建议至少配置以下告警：

- 已启用 Worker 的 `account_pool_worker_up` 连续两个周期为 0
- `account_pool_worker_consecutive_failures` 大于等于 3
- `rate(account_pool_worker_failures_total[5m])` 持续大于 0
- 关键 Worker 最后成功时间超过其预期间隔的两倍

周期异常不会终止 Worker 循环。公共日志仅写固定失败说明，不附加异常正文；业务失败详情通过既有稳定原因码事件查看

仓库内的 `account-pool/deploy/prometheus/account-pool-alerts.yml` 已由根目录 `prometheus.yml` 加载，Compose 启动后
可在 `http://127.0.0.1:9090/alerts` 查看。规则覆盖指标抓取失败、已启用 Worker 不可用、10 分钟内重复失败、启动后
超过两个预期周期从未成功，以及超过三个预期周期没有新成功记录。规则只按固定 `worker` 标签聚合，不携带渠道或请求字段

## 事件保留与加密归档

统一事件默认在线保留 90 天，并且只归档已经完整结束的月份。普通事件与管理审计分开归档，审计保留天数可以更长，
但配置短于普通事件时服务会拒绝启动。每轮最多归档一个范围内的固定批次，避免大月份占用无界内存；后续周期继续处理
同月剩余事件

归档只读取已经通过统一事件 Pydantic 模型验证的公共信封和单一关联事实，不读取渠道目录、URL、Key、凭证引用或上游
响应。每个归档目录包含 AES-256-GCM 密文和不含业务正文的清单，清单记录事件 ID、月份范围、密钥标识、明文 SHA-256
和密文 SHA-256。密文、清单和解密内容全部校验成功后，PostgreSQL 才会按清单中的精确事件 ID 事务删除关联事实与公共信封；
写入、校验或数据库删除失败都会保留在线数据，重复周期会验证已有归档后继续完成删除

归档默认关闭。启用时必须同时设置：

- `ACCOUNT_POOL_EVENT_ARCHIVE_PATH`：持久化归档目录，容器部署时必须挂载持久卷
- `ACCOUNT_POOL_EVENT_ARCHIVE_KEY`：URL-safe Base64 编码的 32 字节随机密钥，不得提交到仓库
- `ACCOUNT_POOL_EVENT_ARCHIVE_KEY_ID`：非敏感密钥版本标识，默认 `default`
- `ACCOUNT_POOL_EVENT_RETENTION_DAYS`：普通事件在线保留天数，默认 90
- `ACCOUNT_POOL_AUDIT_EVENT_RETENTION_DAYS`：审计事件在线保留天数，默认 90
- `ACCOUNT_POOL_RETENTION_INTERVAL_SECONDS`：归档扫描周期，默认 300 秒
- `ACCOUNT_POOL_RETENTION_BATCH_SIZE`：单个加密归档的最大事件数，默认 10000，最大 100000

PowerShell 可使用已有 Python 环境生成密钥：

```powershell
$env:ACCOUNT_POOL_EVENT_ARCHIVE_KEY = python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip('='))"
```

密钥必须由部署密钥管理系统保存。轮换时先保留旧密钥用于历史归档恢复，再使用新的 `KEY_ID` 和密钥写后续归档；当前
服务不会在数据库删除之后重新加密历史文件

## PostgreSQL 加密备份与恢复

`account-pool-postgres` 命令提供整库备份、离线校验和显式确认恢复。备份使用 PostgreSQL custom format，并把
`pg_dump` 标准输出直接进行 AES-256-GCM 流式加密；数据库 URL 仅通过受限子进程环境传递，日志和结果只包含稳定状态码。
恢复前必须同时通过密文摘要、认证标签、明文摘要和 `pg_restore --list` 校验，并把清单中的完整归档 ID 传给 `--confirm`

详细命令、密钥要求、恢复破坏性说明和验收清单见 `deploy/postgres/RUNBOOK.md`。工具要求执行环境已有兼容版本的
`pg_dump` 与 `pg_restore`，不会自动下载数据库客户端

客户端若需要账号池调度，应访问 Rust AI Gateway 的 `http://127.0.0.1:4001/v1/*`；直连 LiteLLM 会绕过账号级并发和额度约束，
生产网络中应限制 LiteLLM Proxy 的直接访问。4100 只保留控制面、管理 UI 和网关所需的内部结算接口
