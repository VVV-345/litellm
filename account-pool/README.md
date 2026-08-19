<!-- 本文件说明 Account Pool 的定位、配置、启动方式、接口与安全边界。 -->

# LiteLLM Account Pool

Account Pool 是 LiteLLM 旁路运行的账号级调度服务。LiteLLM 继续负责供应商协议、加密保存 Key 和 Deployment；
Account Pool 负责渠道配置、共享并发、租约、额度快照、健康状态和模型到具体 Deployment 的选择

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
- 号池健康检查：`http://127.0.0.1:4100/healthz`
- Redis 仅在 Compose 内网提供服务

进入 4100 调度器 UI，使用 LiteLLM 管理令牌登录，即可配置渠道、调度策略和实时路由。渠道创建仍复用
LiteLLM `/model/new` 的 Deployment 管理链路。LiteLLM 与号池必须使用相同的 `ACCOUNT_POOL_INTERNAL_TOKEN` 和
`ACCOUNT_POOL_ACTOR_SECRET`；Compose 会把 `.env` 中的值同步给两个服务。前者证明内部服务身份，后者用于签发
绑定管理员、请求 ID 和授权动作的短时 actor 信封

`account-pool/config/accounts.yaml` 是可写的非敏感配置。API Key 通过 LiteLLM `/model/new` 写入其数据库，
不会进入该 YAML、Redis、日志或管理 API 响应

## 渠道模块

每个供应商位于独立目录：

```text
account_pool/provider_services/
├── contracts.py          # 所有渠道实现的统一协议
├── registry.py           # 模块注册和按 provider_id 分发
├── parser_registry.py    # 组装无凭证解析器选择注册表
├── http_response.py      # 共用响应体大小限制
├── glm/
    ├── manifest.py       # 渠道标识、默认 URL 和能力声明
    ├── schemas.py        # 上游响应模型
    ├── client.py         # 官方 HTTP 请求和 URL 安全限制
    ├── service.py        # 转换为渠道校验结果
    └── parser.py         # 转换为统一 ParserRun
└── openai_compatible/
    ├── manifest.py       # 通用 OpenAI 兼容能力声明
    ├── schemas.py        # GET /models 响应模型
    ├── client.py         # 自定义 HTTPS URL 与 SSRF 基础防护
    ├── service.py        # 模型发现与渠道校验结果转换
    └── parser.py         # 转换为套餐、按量和问题报告统一结构
```

新增渠道时复制同一职责边界，并在 `app.py` 的 `ProviderServiceRegistry` 注册。公共管理层不按渠道名写分支，
各模块可以并行开发，也不会把某个供应商的余额、套餐或价格规则带到其他供应商

解析器选择使用独立 `ParserRegistry`，固定顺序为显式 parser、Provider 与标准化 origin 精确匹配、已声明的
OpenAI 兼容通用 parser、人工模板。选择请求的 schema 不包含 Key，并拒绝额外凭证字段，因此选择过程不会把 Key
依次发送给多个厂商试探。未知的显式 parser 不会静默降级到其他自动 parser，而是进入人工修正

解析框架按职责拆分：`models.py` 定义统一结果，`registry.py` 只做无凭证选择，`persistence.py` 定义持久化与
导出状态，`postgres/` 分离 SQL、行编解码和事务编排，`worker.py` 编排解析、提交和快照。新增厂商解析器不需要
修改 PostgreSQL 仓储或 Worker 中的渠道分支

## GLM 官方国内平台

首个模块使用智谱官方国内开发平台：`https://open.bigmodel.cn/api/paas/v4`

| 能力 | 当前状态 | 实现 |
| --- | --- | --- |
| URL 与 Key 校验 | 支持 | `GET /models`，Bearer Key |
| 当前 Key 可见模型 | 支持 | 解析并去重官方模型列表 |
| 自定义模型名 | 支持 | UI 可输入列表外模型并回车 |
| 账户下 Key 列表 | 不支持 | 官方推理 API 无公开管理接口 |
| 余额、套餐、周/月限额 | 不支持 | 官方推理 API 无稳定公开查询接口 |
| 账户实际价格 | 不支持 | 模型接口不返回分组折扣、资源包或成交价 |

GLM 模块只允许 HTTPS、`open.bigmodel.cn` 和固定 `/api/paas/v4` 路径，且禁止重定向，避免把 Key
发送到用户输入的任意地址；模型列表响应限制为 1 MiB。校验结果只返回 Key 的 SHA-256 指纹前缀。统一解析结果
保留可见模型并返回 `partial`，套餐、按量和计费路由保持为空，等待人工补充可验证的账户数据

LiteLLM 的 provider 名称仍为 `zai`，但创建 Deployment 时会显式保存国内 API Base，因此不会使用
LiteLLM 的国际默认地址 `https://api.z.ai/api/paas/v4`

## OpenAI 兼容接口

`openai_compatible` 模块支持自定义 HTTPS API Base，并使用 `GET {api_base}/models` 校验 Key 和发现模型。
它会拒绝 URL 用户信息、查询参数、片段、重定向、私网解析结果和超过 1 MiB 的响应。当前通用协议不能可靠
获取余额、套餐、周期额度、分组倍率或账户实际价格，因此这些字段明确保持不支持，不从公开价格或模型名称猜测

模型发现成功后，解析器返回 `partial`：保留排序去重后的模型，套餐、按量和可执行计费路由为空，并生成结构化
未解析字段与人工补充建议。统一解析模型使用 `Decimal` 保存金额和倍率，支持套餐额度窗口、分组价格、并发限制及
脱敏问题报告，为后续厂商专用解析器复用；解析结果不包含 API Base、Key、Key 指纹或上游原始响应

自定义域名目前在请求前检查 DNS 结果，但底层 HTTP transport 没有固定已验证 IP；严格对抗 DNS rebinding 的
生产部署应在受控出口代理执行同等地址策略，或后续接入支持固定目标 IP 且保留原 TLS SNI 的 transport

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
管理员代理接入；代理在服务端签发短时 actor 信封，4100 独立 UI 不签发该信封，当前只读预览覆盖数据。
`GET /api/channels/{id}/snapshot` 和 `/export` 从 PostgreSQL 最新结果即时生成以渠道 ID 为键的 schema v1 脱敏文档，
后者附带下载响应头。`POST /api/channels/{id}/parse` 接收一次性 Key，在当前 Account Pool 实例接管后返回任务 ID；
任务所有权、心跳和结果写入 PostgreSQL，但 URL、Key 和 Key 指纹不会进入任务记录。进程中断后的超时任务会变为
`interrupted_requires_key`，不会由其他实例接管或自动重试。`POST /api/channels/{id}/import` 接受单渠道 schema v1
脱敏文档，只把 effective result 差异原子转换为可审计人工
覆盖，不替换 parser run 或快照文件。后台导出重试循环及字段差异 UI 仍按 Phase 2 后续步骤接入

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
$env:DATABASE_URL = "postgresql://user:password@127.0.0.1:5432/litellm"
.\.venv\Scripts\python.exe -m uvicorn account_pool.app:app --host 127.0.0.1 --port 4100
```

所有 `/api/*` 和 `/internal/*` 接口都要求 `X-Account-Pool-Token`；未配置服务令牌时接口会拒绝服务，
不会退化为无认证访问。人工覆盖写接口还要求 LiteLLM 使用 `ACCOUNT_POOL_ACTOR_SECRET` 签发 actor 信封，不能由
浏览器或 4100 独立 UI 直接调用。`/healthz` 保持无认证，供容器健康检查使用

`DATABASE_URL` 未配置时，现有调度和渠道接口仍可启动，但解析历史和有效数据接口返回 503；可通过
`ACCOUNT_POOL_DATABASE_SCHEMA` 指定非 `public` schema

客户端若需要账号池调度，应访问 Account Pool 的 `/v1/*` 网关；直连 LiteLLM 会绕过账号级并发和额度约束，
生产网络中应限制 LiteLLM Proxy 的直接访问
