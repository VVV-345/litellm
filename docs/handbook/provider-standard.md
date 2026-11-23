# 号池供应商接入与配置标准

本文以现有 Codex 卡片为能力参照，规定新增供应商需要说明、实现和验证的边界。能力相同才复用实现，不把 Codex 的 OAuth、指纹或 Access 降级接口套到其他供应商。本文描述源码行为，不代表某个线上账号已通过真实推理验证

## 1. 接入身份和职责

| 标识 | 含义和约束 |
| --- | --- |
| `provider_family` | 页面上的产品家族，例如 GLM、OpenCode、Codex；不是运行容器 ID |
| `channel` | 实际接入通道，当前为 `cliproxyapi` 或 `openai_compatible` |
| `supplier` | 通道中的认证和协议适配；GLM、OpenCode 与通用兼容接入共用 `openai_compatible` |
| 卡片 `id` | 不可变 UUID；名称、邮箱、模型和上游地址不能代替它作为资源路径或路由身份 |

Dashboard 负责交互，LiteLLM 负责管理员鉴权和数据面入口，Manager 负责状态、版本与配置编排，通道负责供应商协议。目录发现不能创建卡片、启动 OAuth、重建容器或发送模型推理

供应商目录入口是 [`provider_families.py`](../../account-pool/account_pool/provider_families.py)，通道注册入口是 [`channels/registry.py`](../../account-pool/account_pool/channels/registry.py)。新供应商先确认复用兼容协议还是确实需要独立适配，不根据显示名称复制一套生命周期实现

## 2. 能力说明必须包含的项目

每个供应商文档应填写以下表格，不能只写“支持”或只提供一个勾选开关

| 项目 | 必填内容 |
| --- | --- |
| 身份与认证 | 家族、通道、供应商、OAuth/API Key/设备授权方式、凭据唯一归属、替换和删除行为 |
| 上游地址 | 官方与自定义地址范围、目录接口与推理接口是否不同、代理和自定义头的来源 |
| 模型目录 | 来源、账号权限范围、前缀、手填模型、失败回退、是否支持资源侧重新获取 |
| 模型启用 | 用户选择与目录分离；是否需要运行时同步，何时才允许进入路由 |
| 配置生效 | 保存版本、期望/观测版本、运行时状态、失败补偿；不能用 HTTP 200 代替生效证据 |
| 额度 | 解析来源、窗口、余额、重置时间、未知与零的区别、缓存与主动刷新 |
| 协议与传输 | 实际支持的 chat/responses/messages/images、超时、流式输出、WebSocket 和直通边界 |
| 专属能力 | 指纹、安装 ID、Access 降级、客户端限制等；不支持的能力明确标为不支持 |
| 验证 | API 鉴权、失败脱敏、旧卡兼容、重启保留、真实服务检查、是否执行过真实推理 |

当前 `PolicyCapability.status` 的值是 `gateway`、`metadata`、`unsupported`。它描述处理位置，不等于“该账号已经可用”；运行时验证结果另行记录，不新增一套含义相近的状态枚举

## 3. 模型目录与启用模型

### 当前接入方式

| 卡片 | 目录来源 | 卡片配置中的操作 |
| --- | --- | --- |
| Codex | CPA 的账号绑定接口 `/v0/management/auth-files/model-catalog` | 重新发现模型；不能用已经过滤的注册表代替完整目录 |
| GLM / Z.AI | 保存的 Base URL 对应 `/models`，使用本卡凭据和代理 | 从资源侧获取模型 |
| OpenCode | 保存的 Base URL 对应 `/models`；Go 的已知兼容地址映射到现有 Go 目录地址 | 从资源侧获取模型 |
| 通用 OpenAI 兼容 | 保存的 Base URL 对应 `/models` | 从资源侧获取模型 |
| 其他 CPA 供应商 | 当前读取认证文件的已注册模型 | 未实现账号级完整目录前，不能声称具备 Codex 同等发现能力 |

GLM 与 OpenCode 家族不会因为模型名相似就使用其他厂商的地址。目录接口返回模型也不证明 Key 对每个模型都有推理权限；标准 API 与 Coding Plan 的权限、余额和模型支持必须分开看

### 目录字段的含义

`available_models` 是候选目录，`enabled_models` 是当前允许路由的模型。Codex 还使用 `model_selection`、期望配置和 `configured_models` 区分用户选择与 CPA 实际注册状态。不能把“新增候选”直接写成“全部启用”

兼容通道的 `openai_compatible.custom_models` 保留手工模型，作为候选补充和无目录接口时的兜底，不再覆盖整个上游发现结果。普通刷新合并手工项与发现项；发现失败时保留已有目录。已有卡片明确保存的空启用列表必须保持为空，不能把它当成首次初始化

资源侧发现成功只更新 `available_models`，不改版本、启用列表、代理、凭据、额度或未保存草稿。新模型默认不勾选；用户点击“启用全部”也只是编辑草稿，仍需“保存配置”才应用。目录中消失的旧选择不应让无关配置保存失败，也不自动获得新的路由权限

### 复用的接口与安全要求

Dashboard 调用 `POST /account_pool/environments/{id}/model-catalog`，由原生管理员鉴权代理到 Manager 的 `POST /api/environments/{id}/model-catalog`。已绑定卡片从服务端读取加密凭据和配置，不要求浏览器再输入或回传 API Key

创建前预览继续使用现有兼容模型预览接口，它不创建或修改卡片。两种入口共用地址校验、响应解析和已知供应商目录映射，不能为卡片按钮另写一个不受限制的 URL 请求器

显式兼容目录发现有 20 秒上限、2 MiB 响应限制和 500 个候选上限；模型名去空白、去重、过滤非法或过长项，前缀只加一次。空目录、格式错误、401/403、404、限流、超时和网络失败不清空旧目录，也不以手写预设冒充成功的上游响应

目录请求只允许 GET，不跟随重定向。沿用 DNS/私网/元数据地址限制、卡片代理和保护头校验；错误中不得包含 Key、认证头、代理密码或上游原始正文。OAuth 自动化、配置应用、创建、验证及删除中的卡片不能并发改目录，保存使用现有卡片锁、凭据归属锁和版本检查

前端复用 `RuntimeModelChoices`、`useRuntimeModelCatalog` 与 `accountPoolQueryKeys`，缓存必须包含当前用户令牌、卡片 ID 和版本。一次目录请求不能清除其他用户的缓存、覆盖草稿或解除已有 401/403 状态

## 4. Codex 配置基线

以下字段来自 [`policies.py`](../../account-pool/account_pool/policies.py)，是扩展供应商时的能力清单，不是要求所有供应商都实现相同协议

### 通用配置

| 配置组 | 现有字段或入口 | 扩展要求 |
| --- | --- | --- |
| 卡片运行配置 | 名称、启停、并发、手动冷却、出站代理、启用模型 | 沿用配置版本和对账；发现目录不能顺带保存这些字段 |
| 模型策略 | `excluded_models`、`model_aliases` | 保留原模型身份、别名目标和账号范围校验 |
| 归组与范围 | `tags`、`group`、`account_ids` | 不能扩大原有虚拟密钥或卡片访问范围 |
| 路由与重试 | `routing` 的优先级、权重、备用、粘性、额度保留、预算、尝试次数及退避字段 | 当前跨卡路由和重试以 LiteLLM 原生 Router Settings 为准；旧单卡字段存在不代表它仍是控制入口 |
| 传输 | `supported_protocols`、`passthrough_enabled`、`read_timeout_seconds`、`request_timeout_seconds` | 明确协议转换与读超时范围，不靠扩大超时掩盖错误 |
| 其他传输项 | `image_generation`、`websocket`、`debug_log_enabled` | 能力不等于实际模型支持；日志仍须脱敏 |
| 运行反馈 | `runtime_status`、`runtime_error`、`runtime_updated_at` | 区分已保存、已同步与失败，不把策略对象有字段视为已生效 |

### Codex 专属字段

| `codex` 下的字段 | 后端模型默认值 | 含义与约束 |
| --- | --- | --- |
| `allow_valid_access_after_refresh_failure` | `true` | RT 刷新失败后，仅可对仍有效且已验证的 AT 受限降级；不能修复 RT 或绕过邮箱验证 |
| `identity_fingerprint_mode` | `off` | `off/device/session/full/managed`；托管模式必须核实底层能力和稳定安装身份 |
| `cli_only` | `false` | Codex 客户端限制，不是通用上游模型权限 |
| `allow_app_server` | `false` | App Server 例外，不能无条件解除客户端限制 |
| `allow_app_server_clients` | `[]` | 客户端标识去重、归一化并校验格式与数量 |
| `responses_compact_enabled` | `false` | Codex Responses compact 专属配置，不代表其他厂商兼容 |
| `identity_confuse` | `false` | 供应商专属身份配置，需实际同步与边界验证 |
| `disable_codex_cloaking` | `false` | Codex 专属运行时配置，不复制为其他供应商的默认项 |

后端默认、前端未保存草稿和已有卡片的显式值必须分开。当前前端缺失策略时的 Codex 草稿默认值与部分后端默认值并不一致；以服务端持久化值和同步结果为准，扩展时不得借用草稿默认值覆盖旧卡，本次不改变这些既有默认值

托管身份与 Access 降级分别核实 CPA 的 `/v0/management/codex/managed-identity` 和 `/v0/management/codex/access-token-fallback`。版本、鉴权、支持标记和同卡状态都要符合现有校验。GLM、OpenCode 与通用兼容卡本次只增加模型发现，不暴露这些 Codex 专属开关

## 5. 新供应商落地顺序

1. 填写第 2 节能力表，注明账号套餐、接口地址与协议差异，不记录真实邮箱、Key 或 Token
2. 注册家族和通道。兼容协议复用现有通道；确需新协议时将解析和网络调用放在适配层，不复制 Manager 的状态机
3. 先通过现有服务/API 边界写回归：目录新增、失败保留、前缀、空启用列表、旧选择与凭据不变、鉴权及地址安全
4. 接入现有卡片配置组件，验证首次打开、手动重新获取、草稿保留、明确保存、并发请求与不同用户缓存隔离
5. 需要额度或专属策略时分别补解析器、能力声明和同步证据；不支持的项目保持禁用或明确提示，不能仅靠隐藏按钮实现后端限制
6. 检查持久化兼容性、旧配置读写和发布门禁。模型发现的这次扩展不增加数据库表、字段或凭据迁移，不需要修改旧卡 ID
7. 分别报告单元/接口测试、UI 集成、类型检查、真实服务检查和真实推理。未运行的项目明确列出，不用 Mock 响应证明上游权限
8. 按项目约定先验证、再提交；收到明确 push 指令后推送。push 触发镜像发布不等于已部署，不自动替换服务器业务或账号运行时

## 6. 验收清单

发现目录后，新模型可见但未自动启用；旧勾选、未保存名称、卡片 ID、版本、凭据、代理与额度不变。失败和空响应保留最后成功目录，手填模型仍能作为兜底。正常刷新不能用初始手填清单盖掉新目录，也不能重新启用“全部关闭”的卡片

代理和 Manager 鉴权都必须测试；覆盖私网 DNS、重定向、错误正文、响应大小和请求超时。供应商没有模型枚举接口时明确说明限制，保留手填入口，不把网页上的模型营销列表当成该账号的可用模型

实现位置：[兼容通道](../../account-pool/account_pool/channels/openai_compatible.py)、[目录编排](../../account-pool/account_pool/application/environments/auth_files.py)、[配置校验](../../account-pool/account_pool/application/environments/configuration.py)、[卡片模型界面](../../ui/litellm-dashboard/src/components/Settings/RuntimeSettings/RuntimeModelChoices.tsx)。其他生命周期和凭据要求继续遵循[卡片与环境](modules/environments.md)、[凭据与授权](modules/credentials.md)、[设置与策略](modules/settings-plugins.md)
