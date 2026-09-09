# Cockpit 能力整合到 LiteLLM 号池的核实与实施计划

## 1. 文档目的

本文把 Cockpit Tools v1.3.47 已核实的页面能力、配置能力和请求执行能力，映射到当前 LiteLLM 号池项目，形成后续开发可以逐项验收的计划

本文的目标使用场景是：管理员在 LiteLLM 号池中维护多个上游账号，为每张号池卡片生成一个卡片 Key；NewAPI 保存这些卡片 Key 并负责下游用户、权限、额度和限流，号池负责卡片 Key 校验、账号配置、配额、切换和上游转发

本文不把本机 Codex、Cursor 的桌面进程管理当作服务端号池的必要条件。它们属于第二阶段的本机客户端能力

## 2. 已核实版本与现有项目基线

| 项目 | 已核实版本或状态 | 结论 |
| --- | --- | --- |
| Cockpit Tools | v1.3.47，commit `deacbe44` | 页面、桌面客户端和账号策略的参考实现 |
| Cockpit 内嵌 CLIProxyAPI | v7.2.155，commit `7fac6b15` | Cockpit 当前绑定的上游代理版本 |
| 当前号池 CLIProxyAPI | v7.2.146 | 与 Cockpit 内嵌版本有版本差异，需要单独升级或适配 |
| LiteLLM Responses Compact | 已有 `/v1/responses/compact` 支持 | 需要验证号池链路是否完整转发 |
| 当前号池 | 已有环境创建、OAuth、配置、配额观测、冷却、代理、模型和 LiteLLM Deployment 对账 | 已有生命周期基础，缺少卡片级 Key 和高级请求路由闭环 |

当前项目的关键位置如下

- Manager 后端：`account-pool/account_pool/`
- LiteLLM 管理代理：`litellm/proxy/management_endpoints/account_pool_endpoints.py`
- LiteLLM Deployment 对账：`litellm/proxy/management_endpoints/account_pool_reconciler.py`
- 号池页面：`ui/litellm-dashboard/src/app/(dashboard)/account-pool/`
- Cockpit 侧车与 CLIProxyAPI 参考代码：外部审计目录中的 `sidecars/cockpit-cliproxy/`

## 3. 总体边界

最终数据流应按下面的职责划分实现

```text
下游客户端
  -> NewAPI 下游用户和 Key
  -> NewAPI 选择号池卡片并使用卡片 Key
  -> 号池卡片 Key 校验和卡片绑定
  -> 号池路由层
  -> 账号范围过滤、模型过滤、优先级、权重、会话粘性
  -> 单账号 CLIProxyAPI 环境
  -> Codex 或其他上游服务
```

控制面和数据面必须分开

- 控制面保存账号、卡片 Key、配置、路由规则和审计数据
- 数据面处理真实模型请求、账号选择、会话绑定、冷却和故障切换
- Manager 负责环境生命周期，不承载模型请求
- NewAPI 负责下游用户身份和下游 Key；LiteLLM 或号池网关只负责卡片 Key，不把 Manager 当作公共模型网关
- CLIProxyAPI 负责单账号的上游协议适配和账号凭据使用

这样做后，多个下游用户可以共用一个集中式号池，账号密钥不会下发给下游用户。上游看到的是服务端发出的请求和被选择的上游账号，但不能由此保证上游始终把所有请求判定为同一个人。出口 IP、TLS 连接、并发、请求节奏、请求内容、上游风控和账号本身的状态仍可能产生差异

## 4. Cockpit 页面功能完整矩阵

下表是页面层需要覆盖的功能。页面只负责展示、编辑和发起操作，真正的权限判断、配置保存和请求执行必须在服务端或代理层再次校验

| 页面模块 | Cockpit 能力 | 当前号池情况 | 整合后的目标 |
| --- | --- | --- | --- |
| Dashboard | 总账号数、可用数、冷却数、错误数、额度和请求概况 | 有账号列表和状态，缺少统一汇总 | 增加汇总卡、可用率、近期错误、额度更新时间和路由健康 |
| 账号卡片 | 邮箱或显示名、套餐、额度窗口、重置时间、状态、启用模型、标签 | 已有基础卡片、额度、状态和模型 | 补充套餐、标签、优先级、权重、备用标记、会话绑定数和最近错误 |
| 账号详情 | OAuth 状态、授权时间、账号类型、模型、配额、配置和操作历史 | 已有授权和环境详情 | 增加账号级路由策略、模型排除、各供应商身份或指纹策略、客户端策略和 Compact 投影配置 |
| 创建账号 | 选择供应商和渠道，启动 OAuth 或设备码授权 | 已支持多渠道定义和授权流程 | 沿用现有流程，补充默认策略快照和权限归属初始化 |
| 重新授权 | 重新打开授权流程、处理过期和失败 | 已有重新授权和 state 防重放 | 增加授权失败原因、最近一次成功授权和后台恢复状态 |
| 导入导出 | 导出账号配置或批量导入账号 | 当前以 OAuth 环境为主 | 只允许导出非敏感元数据；凭据导入必须采用受控后台流程，禁止页面返回完整 token |
| 删除账号 | 摘除路由、停止实例、删除数据和元数据 | 已有清理检查点和删除流程 | 增加删除前卡片 Key 引用检查、Key 撤销和审计记录 |
| 标签和分组 | 标签、分组、筛选、批量选择 | 当前缺少完整标签分组 | 增加账号标签、业务分组、供应商分组和批量操作 |
| 批量操作 | 批量刷新额度、授权、启停、冷却、删除、修改路由策略 | 当前已有部分批量能力基础 | 所有批量动作使用异步任务、幂等键和逐账号结果 |
| API Service | 启停服务、Base URL、端口、服务 Key、健康状态 | 当前服务由 LiteLLM 和 Manager 配置控制 | 页面展示服务状态；公共入口只保留 LiteLLM，不向每个账号发布宿主机端口 |
| API Key 管理 | Key 名称、账号范围、优先账号、模型权限、Token 限额、状态 | 当前号池还没有卡片级 Key | 每张卡片生成一个卡片 Key，支持显示、复制、撤销、重置和卡片绑定；下游用户权限由 NewAPI 管理 |
| 路由策略 | 自动、随机、单账号、配额、套餐、到期、自定义 | 当前主要通过 Deployment 对账和 LiteLLM 路由 | 增加号池路由层，先过滤候选，再执行策略排序和选择 |
| 账号优先级 | 优先账号、权重、备用账号、固定顺序 | 当前缺少请求级优先级 | 增加规则表和运行时排序，优先账号失败后才进入备用账号 |
| 会话粘性 | 按会话或客户端把请求尽量固定到同一账号 | 当前缺少 | 按卡片 Key、会话 ID 和模型建立隔离的粘性键，并设置 TTL |
| 模型策略 | 全局模型排除、账号模型排除、模型别名、Key 模型白名单 | 当前已有启用模型，缺少完整排除和别名 | 统一在请求进入代理层时校验，目录展示与真实可用模型保持一致 |
| 配额策略 | 配额不足过滤、额度保留、窗口重置恢复 | 当前已有配额观测和自动冷却 | 增加请求前额度保留、请求后释放或确认、快照过期保护 |
| 故障处理 | 重试、冷却、备用账号、错误分类和故障切换 | 当前已有冷却和生命周期恢复 | 在代理层实现可重试错误分类和单请求故障切换，避免重复提交不可重试请求 |
| 高级传输 | WebSocket、图片策略、超时、调试日志 | 当前部分由 CLIProxyAPI 提供 | 统一透传策略，按账号和 Key 记录可审计的请求元数据 |
| 统计 | 账号、模型、Key 维度的请求数、Token、延迟、成本和错误 | 当前 LiteLLM 有通用日志，账号维度不足 | 增加 account_id、card_key_id、route_reason 和 upstream_status 维度 |
| 健康诊断 | 单账号测试、模型测试、账号池健康检查 | 当前有模型发现和健康状态 | 增加不消耗真实额度的配置检查和明确标注会产生上游请求的测试 |
| 批量测评 | Cockpit v1.3.47 的鹈鹕测智，可对多个账号发送相同提示并比较 | 当前没有 | 作为独立的管理员诊断功能，默认关闭，增加成本和隐私确认 |
| 设置中心 | 全局默认路由、重试、额度和协议设置 | 当前配置分散在环境和 LiteLLM | 建立全局默认值、账号覆盖值和 Key 覆盖值的优先级 |

## 5. Cockpit 设置和数据执行矩阵

设置字段必须区分“展示设置”和“会改变请求行为的设置”。保存位置只是控制面归属，执行位置才决定它是否真正生效

| 设置类别 | 字段或能力 | 默认值建议 | 保存位置 | 真正执行位置 |
| --- | --- | --- | --- | --- |
| 账号基础 | `name`、`enabled`、`tags`、`group_id` | 名称必填，启用 | Manager 数据库 | Manager 状态机和代理候选过滤 |
| 生命周期 | `manual_cooldown`、`cooldown_until`、`automatic_cooldown` | 不手动冷却，无自动冷却 | Manager 数据库 | 代理候选过滤和后台恢复任务 |
| 并发 | `concurrency_limit` | 沿用当前账号环境默认值 | Manager 数据库或 LiteLLM Deployment | LiteLLM 和代理层的账号级信号量 |
| 出站代理 | `proxy_mode`、`proxy_profile_id` | 默认网关 | Manager 数据库，代理地址写入期望配置 | CLIProxyAPI 容器出站连接 |
| 模型 | `enabled_models`、`excluded_models`、`model_aliases` | 发现模型中默认启用，显式排除优先 | Manager 数据库 | 模型目录、请求校验和上游模型改写 |
| 路由 | `routing_strategy` | `auto` | 号池路由配置 | 代理层账号选择器 |
| 路由排序 | `priority`、`weight`、`is_backup`、`preferred_account_ids` | 优先级 0，权重 1，不是备用 | 路由规则表 | 代理层候选排序 |
| 会话 | `session_affinity`、`session_affinity_ttl` | 关闭或按部署策略开启，TTL 1 小时 | 路由配置表 | 代理层会话缓存 |
| 配额 | `quota_reserve`、`reserve_percent`、`snapshot_max_age` | 不保留，快照过期即不参与保留判断 | 账号策略表 | 代理层请求前筛选和额度保留状态 |
| 重试 | `retryable_statuses`、`max_attempts`、`backoff` | 只重试明确可重试错误，默认 1 次切换 | 全局策略和账号覆盖 | 代理层请求编排 |
| 故障切换 | `fallback_enabled`、`fallback_scope` | 开启文本请求的账号切换，关闭不可重试请求切换 | Key 或路由组配置 | 代理层 |
| 客户端准入 | `codex_cli_only`、`codex_cli_only_allow_app_server`、`codex_cli_only_allow_app_server_clients` | 沿用 CLIProxyAPI 默认 | 账号或渠道运行配置 | CLIProxyAPI 根据 User-Agent 和 Originator 判断 |
| 上游身份或指纹策略 | `identity_fingerprint_mode` 和供应商扩展字段 | 按供应商能力定义，未设置时使用该供应商默认值 | 账号策略表，投影到对应代理适配层 | 对应代理或供应商适配器的请求改写、响应反向映射或连接策略 |
| Codex Compact 投影 | `compact_ui`、`model_context_window`、`model_auto_compact_token_limit`、`context_management_experimental` | 分开设置，默认不强制打开 | 账号策略表或本机客户端配置 | 本机 Codex 配置或请求数据面，不能混成一个字段 |
| 图片 | `image_generation_policy`、并发和图片账号范围 | 继承账号能力 | Key 或账号策略 | 代理层图片请求选择器 |
| WebSocket | `websocket_enabled`、超时和 Origin 策略 | 按供应商默认 | 渠道配置 | CLIProxyAPI 和网关连接层 |
| 调试 | `debug_log_enabled`、保留时间、敏感字段脱敏 | 关闭 | 管理配置 | 日志管道，不记录 token 和完整请求体 |
| 统计 | `usage_account_id`、`usage_key_id`、`route_reason` | 自动写入 | 请求日志和用量表 | LiteLLM 回调、代理事件和统计查询 |

### 5.1 身份和指纹策略的准确边界

前一版计划把指纹策略错误地收窄成了 Codex 专属，这是范围定义错误。整合目标是覆盖 Cockpit 中出现的全部设置，包括不同供应商的身份、指纹、客户端和连接策略。实现时采用统一的策略模型，但由供应商适配器决定字段、默认值和可执行能力

目前已经详细核实的是 Cockpit 对 Codex 的请求标识改写。它实际改写的是请求中的一组标识字段，不是把三台设备的全部硬件特征统一。Codex 已核实的模式如下

| 模式 | 已核实行为 |
| --- | --- |
| `off` | 不改写 |
| `device` | 改写 installation、工作区路径、Git remote、commit SHA，保留 session 和 thread |
| `session` | 在 device 基础上，按账号和原始会话改写 session、thread、turn、window 及父子关系 |
| `full` | 进一步把 thread 和父线程收敛到映射后的 session |

它只对符合条件的标准 Codex OAuth 账号自动启用。API Key、Agent Identity、Web Session 和 Access Token 不自动套用这套规则。账号没有设置时，Cockpit 投影层会对符合条件的 OAuth 账号下发 `session`；代理没有收到字段时，CLIProxyAPI 默认是 `off`

其他供应商的相关能力必须按各自源码和协议继续核实，不能把 Codex 字段直接套到 Claude、Antigravity、Kimi、xAI 或其他渠道。计划中的统一字段只负责表达策略意图，具体执行由供应商能力清单映射到对应的 CLIProxyAPI 或代理适配器

任何供应商的身份或指纹策略都不能自动统一 IP、TLS 指纹、操作系统、浏览器、硬件、并发节奏或请求内容。产品描述应使用“上游身份策略”或“请求标识策略”，不能承诺“统一全部设备指纹”或“保证上游识别为同一个人”

### 5.2 全部 Cockpit 设置的纳入原则

“全部设置都要应用”表示每个 Cockpit 设置都必须进入本项目的功能清单，并有明确的保存位置、适用范围、执行位置、能力状态和验收项。它不表示所有设置都用同一种方式作用于所有供应商

每个设置都登记到供应商能力矩阵中，并分为四种情况

- 全局设置：所有渠道都适用，例如管理权限、日志、默认超时和公共路由默认值
- 账号或供应商设置：只对支持该协议的账号生效，例如某供应商的身份策略、模型能力、WebSocket 或图片策略
- 数据面设置：必须在请求代理时执行，例如账号选择、Key 作用域、模型改写、重试、冷却和会话粘性
- 桌面端设置：必须在本机客户端执行，例如 Codex 或 Cursor 进程、窗口、配置文件、WSL 和本机会话

对当前版本暂不支持的设置，页面不能静默保存后显示成功。应显示“已保存但当前渠道不支持”或阻止保存，并在能力矩阵中记录后续适配任务

### 5.3 Compact 必须拆分

产品中不能只设计一个“Compact 模式”开关，因为它对应多个不同层次

| 名称 | 作用 | 所属层 |
| --- | --- | --- |
| 紧凑视图 | 页面显示方式 | Dashboard UI |
| `model_context_window` | 本机模型上下文窗口 | Codex `config.toml` |
| `model_auto_compact_token_limit` | 本机自动压缩阈值 | Codex `config.toml` |
| `features.context_management.experimental_mode` | 官方客户端实验开关 | Codex 客户端配置 |
| `/v1/responses/compact` | 对话压缩数据面接口 | LiteLLM、CLIProxyAPI 和上游适配 |
| DCP 额度注入 | 本机页面或会话辅助能力 | 桌面端 |

## 6. 三类功能边界

### 6.1 可以直接放进服务端控制面

- 账号卡片、状态、套餐、配额窗口和更新时间
- OAuth 创建、重新授权、删除和恢复
- 标签、分组、搜索、筛选和批量任务
- 账号启停、人工冷却、并发、代理配置和模型启用
- 卡片 Key 的生成、撤销、重置、状态和账号绑定关系
- 卡片 Key 的模型白名单、排除列表、Token 限额和速率限制
- 路由组、账号优先级、权重、备用标记和自定义顺序
- 统计索引、请求审计、错误分类和健康检查
- 全局默认配置、账号覆盖配置和卡片 Key 覆盖配置
- LiteLLM Deployment 的创建、更新、删除和对账
- CLIProxyAPI 镜像版本、配置版本和期望状态管理

服务端只保存策略和状态，不应该把下游用户的请求直接转发给 Manager。Manager 也不应该持有对外模型网关职责

### 6.2 必须放进代理或数据面

- 根据卡片 Key、模型和会话选择真实账号
- 过滤未授权、禁用、冷却、额度不足或模型不支持的账号
- 执行优先级、权重、随机、配额优先、套餐优先和备用账号策略
- 生成和读取会话粘性键，并按 TTL 管理绑定
- 请求级并发占用和释放
- 对明确可重试的错误执行备用账号切换
- 各供应商身份或指纹字段改写、响应反向映射和连接级策略
- User-Agent、Originator 等客户端准入判断
- 模型别名转换和上游模型名恢复
- 图片、WebSocket、Compact 等协议级路由
- 账号级、卡片 Key 级和模型级用量事件记录

如果只在页面保存这些字段，而没有在请求进入代理时执行，它们只是假配置，不会影响真实请求

### 6.3 暂时保留在桌面端

- 本机 Codex 或 Cursor 进程启动、停止和多开
- 本机账号切换和本地配置文件写入
- WSL 同步
- 本机会话 JSONL 管理
- 本机 Compact 配置和 DCP 页面注入
- 本机 Codex、Cursor 实例与窗口状态

桌面端可以调用服务端的账号和策略 API，但不能把桌面进程能力硬塞进 Linux 号池 Manager。服务端部署不需要安装完整 Cockpit

## 7. 号池卡片和卡片 Key 模型

目标应定义为“每张号池卡片提供一个卡片 Key，NewAPI 负责下游用户”。不在 LiteLLM 号池中重复建设 NewAPI 已经具备的用户、团队和下游 Key 权限体系

### 7.1 关系模型

```text
NewAPI 下游用户
  -> NewAPI 下游 Key
  -> NewAPI 选择号池卡片
  -> 号池卡片 Key
  -> 卡片绑定的一个账号，或卡片绑定的账号集合
  -> 号池代理选择具体上游账号
```

建议增加以下概念

| 对象 | 主要字段 | 作用 |
| --- | --- | --- |
| `account_pool_card` | 卡片 ID、名称、状态、供应商、账号范围、配置版本 | 对外提供一个可管理的号池卡片 |
| `account_pool_card_key` | 卡片 ID、Key 哈希、状态、创建时间、重置时间 | 将一个入口 Key 绑定到一张卡片，页面只展示一次明文 Key |
| `account_pool_route_group` | 名称、策略、默认模型、会话设置 | 当一张卡片包含多个账号时定义选择规则 |
| `account_pool_route_member` | `route_group_id`、`environment_id`、优先级、权重、备用标记、模型覆盖 | 定义账号在卡片或路由组中的位置 |
| `account_pool_session_binding` | 卡片 Key ID、会话哈希、模型、账号 ID、创建和过期时间 | 保存卡片内部的会话粘性 |
| `account_pool_quota_state` | 账号 ID、窗口、剩余量、快照时间、保留量 | 支持配额优先和额度保留 |
| `account_pool_request_event` | 请求 ID、卡片 Key ID、账号 ID、模型、路由原因、状态、延迟、Token | 支持审计和统计 |

如果一张卡片只绑定一个上游账号，请求直接固定到该账号，不需要完整的多账号调度。如果一张卡片绑定多个账号，才启用路由、会话粘性、配额优先和故障切换

当前号池的 `EnvironmentRecord` 基本对应一个隔离的上游账号，因此第一版可以让卡片 ID 复用环境 ID，不额外创建复杂的卡片聚合表。只有在一张卡片需要包含多个上游账号时，才增加卡片与环境的一对多关系

卡片 Key 不能只依赖 LiteLLM 的普通模型权限。多个账号可能暴露同名模型，模型白名单本身不能保证请求落到指定卡片。实现时必须在 LiteLLM 路由选择前读取卡片 Key 的服务端绑定信息，或者为卡片建立独立的逻辑路由入口，再选择对应 Deployment

当前环境中由 `SecretPurpose.GATEWAY` 派生的密钥是账号容器内部网关密钥，不能直接拿来给 NewAPI 使用，也不能返回到页面。卡片 Key 必须作为独立的外部访问凭证生成，数据库只保存不可逆哈希，明文只在创建或重置响应中返回

下游用户只接触 NewAPI 的下游 Key。NewAPI 或管理员只需要把号池生成的卡片 Key 配置为上游凭证。账号 OAuth、refresh token、CLIProxyAPI 内部 Key 和账号容器地址都不能返回给下游用户

### 7.2 请求时的权限顺序

每个请求必须按以下顺序处理

1. NewAPI 校验下游用户、下游 Key、模型、预算和限流
2. 号池校验卡片 Key 是否有效，以及它绑定的卡片和账号范围
3. 检查模型、并发、卡片状态和请求格式
4. 从卡片账号范围中排除禁用、冷却、未授权、模型不匹配和配额不足账号
5. 根据会话粘性、优先级、权重、套餐和配额策略排序
6. 选择账号并建立请求级占用
7. 代理层把请求转发到目标账号的 CLIProxyAPI
8. 根据结果更新额度、冷却、会话绑定和用量事件
9. 只有明确可重试的错误才切换到同一卡片的下一个候选账号

卡片 Key 的校验必须在号池入口和代理执行层都成立。NewAPI 负责下游用户权限，号池负责卡片与上游账号绑定，不能把两套权限混成一套

## 8. 端到端数据流

### 8.1 账号创建和授权

```text
管理员打开号池页面
  -> LiteLLM 管理接口检查管理员权限
  -> Manager 创建不可变 environment UUID
  -> 保存期望配置和默认策略
  -> 创建账号专用 Compose、网络和数据卷
  -> 启动 CLIProxyAPI
  -> 生成一次性 OAuth state 和回调地址
  -> 浏览器完成授权
  -> Manager 校验 state、写入凭据并验证模型
  -> 状态变为 ready
  -> LiteLLM 对账生成可路由 Deployment
  -> 管理员为卡片生成卡片 Key，并复制给 NewAPI
```

### 8.2 配置保存和执行

```text
管理员修改设置
  -> LiteLLM 校验权限和版本
  -> Manager 保存 desired_configuration
  -> 后台对账生成 CLIProxyAPI 配置或代理路由快照
  -> CLIProxyAPI reload 或容器重启
  -> Manager 读取 observed_configuration
  -> 页面显示 pending、成功或失败原因
```

保存成功不能等同于运行时已经生效。页面必须显示期望版本和观察版本，配置未被代理确认前显示同步中

### 8.3 模型请求和切号

```text
NewAPI 使用卡片 Key 请求模型
  -> NewAPI 身份、预算、模型和限流
  -> 号池校验卡片 Key 并读取卡片绑定
  -> 卡片账号范围过滤
  -> 会话粘性命中则优先原账号
  -> 否则按策略选择候选账号
  -> 账号并发占用
  -> CLIProxyAPI 请求策略改写
  -> 上游供应商
  -> 响应返回并反向映射
  -> 更新配额、统计、会话和冷却状态
```

“切号”在服务端场景不是修改用户电脑上的登录账号，而是下一次请求或故障切换时选择另一个上游账号。只有桌面端切号才会写入本机 Codex 或 Cursor 配置文件

### 8.4 Compact 请求

```text
/v1/responses/compact
  -> 号池保留 Responses 请求语义
  -> 号池路由选择支持 Compact 的账号
  -> CLIProxyAPI 识别固定路径并转发
  -> 上游返回压缩结果
  -> 号池保留响应结构和用量记录
```

Compact 必须加入端到端测试，因为普通 `/v1/responses` 能通，不代表 `/v1/responses/compact`、流式请求和别名模型都能通

## 9. 数据模型和 API 设计

### 9.1 账号策略字段

建议在现有 `EnvironmentConfiguration` 基础上增加独立的策略对象，避免把生命周期字段、路由字段和供应商协议字段混成一个大配置

```text
AccountPolicy
  enabled
  tags
  group_id
  routing_strategy
  priority
  weight
  is_backup
  excluded_models
  model_aliases
  card_key_id
  session_affinity
  session_affinity_ttl
  quota_reserve
  quota_reserve_percent
  quota_snapshot_max_age
  retry_profile
  fallback_enabled
  identity_fingerprint_mode
  provider_settings
  codex_cli_only
  codex_cli_only_allow_app_server
  codex_cli_only_allow_app_server_clients
  compact_projection
```

`compact_projection` 内部继续拆成 `ui_mode`、`model_context_window`、`model_auto_compact_token_limit`、`experimental_context_management` 和 `responses_compact_enabled`

### 9.2 管理接口

建议沿用现有 LiteLLM 到 Manager 的代理接口，逐步补充以下端点

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/account_pool/environments` | 管理员读取账号摘要 |
| `GET` | `/account_pool/environments/{id}/details` | 读取账号详情、策略、配额和同步状态 |
| `PUT` | `/account_pool/environments/{id}/policy` | 保存账号策略，使用版本号防止覆盖 |
| `POST` | `/account_pool/environments/{id}/cooldown` | 手动冷却或解除冷却 |
| `GET` | `/account_pool/route-groups` | 读取路由组 |
| `PUT` | `/account_pool/route-groups/{id}` | 保存路由策略和账号顺序 |
| `POST` | `/account_pool/cards/{card_id}/key` | 为号池卡片生成一个卡片 Key，只在创建或重置时返回明文 |
| `POST` | `/account_pool/cards/{card_id}/key/rotate` | 重置卡片 Key，使旧 Key 立即失效 |
| `DELETE` | `/account_pool/cards/{card_id}/key` | 撤销卡片 Key |
| `GET` | `/account_pool/cards/{card_id}/key/status` | 查询 Key 状态、创建时间和最近使用时间，不返回明文 |
| `GET` | `/account_pool/usage` | 按账号、模型、卡片 Key 查询统计 |
| `POST` | `/account_pool/health-checks` | 发起健康检查或明确标注会消耗额度的测试 |
| `GET` | `/account_pool/sync-status` | 查询期望配置和观察配置的差异 |

推荐由 NewAPI 访问 LiteLLM 的公共模型入口，并把卡片 Key 作为上游凭证。只有在明确需要时，才单独暴露号池公共入口。请求进入后必须从服务端校验结果中得到卡片 Key ID、card ID、route group ID 和 request ID。下游传入同名 Header 不能覆盖这些字段

## 10. 分阶段实施计划

按你的使用方式，计划调整为 **6 个阶段**。NewAPI 已负责下游用户、下游 Key、用户额度和下游权限，因此删除 LiteLLM 内部的多用户账号授权阶段

### 阶段 1：核实 Cockpit 全部能力并建立映射

- 逐项登记 Cockpit 页面、全局设置、账号设置、供应商设置、代理设置和桌面设置
- 为每项设置记录字段、默认值、适用供应商、保存位置、执行位置、当前状态和验收方式
- 区分通用设置、供应商专属设置、数据面设置和桌面端设置
- 固定 CLIProxyAPI 版本、Cockpit 行为审计版本和许可证边界

验收：Cockpit 的每个设置都有明确归属；不支持的设置不会被标记为已生效

### 阶段 2：完成号池卡片和管理页面

- 完善账号卡片、套餐、配额窗口、重置时间、状态、模型、标签和最近错误
- 增加账号开关、并发、冷却、代理、模型和各供应商策略设置
- 增加配置版本、同步状态、保存成功、保存失败和能力不支持提示
- 增加批量刷新配额、批量冷却、批量启停和批量策略更新

验收：页面不返回 OAuth token、refresh token、代理密码或完整敏感配置；每个设置都能看到实际同步状态

### 阶段 3：实现卡片级 Key 和 NewAPI 对接

- 每张号池卡片生成一个卡片 Key
- 卡片 Key 只绑定指定卡片，不建立下游用户到账号的二次授权关系
- 支持 Key 创建、只显示一次明文、复制、撤销、重置和状态查询
- 请求入口根据卡片 Key 找到卡片和账号范围
- NewAPI 使用卡片 Key 作为上游凭证，并继续负责下游用户权限、额度和限流

验收：卡片 A 的 Key 不能访问卡片 B；重置后旧 Key 立即失效；页面和日志不泄露 Key 明文；NewAPI 可以用卡片 Key 正常请求

### 阶段 4：让 Cockpit 设置进入代理和供应商适配器

- 建立所有 Cockpit 设置的供应商能力矩阵
- 将通用字段和供应商扩展字段保存到卡片或账号策略中
- 在对应 CLIProxyAPI 或代理适配层执行身份、指纹、客户端、连接和协议设置
- 接入模型排除、别名、图片、WebSocket、Compact、超时和日志等数据面行为
- Codex 先实现已核实的 `off`、`device`、`session`、`full`，其他供应商按核实结果实现
- 配置不支持时明确返回能力状态，不静默忽略

验收：每个纳入清单的数据面设置都能改变真实请求或明确报告不支持；设置关闭时不改写请求；不同供应商不会错误套用其他供应商字段

### 阶段 5：实现卡片内部切号、配额和故障处理

- 一张卡片只有一个账号时直接固定转发
- 一张卡片包含多个账号时，实现候选过滤、优先级、权重、随机、配额优先、套餐优先和备用账号
- 实现会话粘性、TTL、账号冷却、并发占用和故障切换
- 只对明确可重试的错误切换账号，避免重复提交不可重试请求
- 记录卡片 Key、账号、模型、路由原因、状态、延迟和 Token
- 完成 `/v1/responses/compact` 的端到端验证

验收：卡片内部可以按配额和状态自动切号；冷却或失效账号不会继续被选中；路由结果可解释；NewAPI 的下游用户不需要感知上游账号切换

### 阶段 6：实现本机 Codex、Cursor 多开和桌面联动

- 本机管理 Codex、Cursor 进程、实例、窗口和本地配置文件
- 支持本机账号切换、WSL 同步、本机会话和 Compact 配置
- 桌面端通过卡片和策略 API 获取服务端数据
- 桌面端功能与服务端号池解耦，桌面端不可用时不影响 NewAPI 到号池的请求

验收：服务端不安装 Cockpit 也能运行；桌面端可以复用卡片和策略；本机切号不会改变 NewAPI 的卡片 Key 绑定

## 11. 更新和同步策略

不能把 Cockpit 整个源码复制进每个账号隔离环境。正确的更新边界如下

### 11.1 CLIProxyAPI 更新

1. 记录当前版本、commit、镜像 digest 和配置 schema
2. 拉取新版本到独立审计目录
3. 比较请求路径、配置字段、账号选择、Codex 改写、Compact、WebSocket 和错误行为
4. 运行适配层兼容测试和配置渲染测试
5. 先在一个测试账号环境灰度升级
6. 通过健康检查和真实协议测试后，再更新默认镜像版本
7. 既有环境只更新容器镜像或配置，不改变环境 UUID、数据卷和 OAuth 凭据路径
8. 保留旧版本回滚信息和数据库 schema 版本

### 11.2 Cockpit 页面能力更新

Cockpit 新增页面功能时，不直接复制 UI 源码。先把功能拆成以下三部分

- 页面行为：按钮、表单、状态和展示字段
- 控制面数据：字段、API、权限和持久化
- 数据面行为：该字段如何改变请求

只把已确认的行为重新实现到 LiteLLM 的页面和服务端。对于 CLIProxyAPI 上游已有的 MIT 能力，可以按许可证复用或升级；Cockpit Tools 本身是 CC BY-NC-SA 4.0，商业使用受限，不能无条件复制 Cockpit 的 React、Rust 或定制 Go 源码。Cockpit 对上游 CLIProxyAPI 的修改还需要逐文件确认其来源和许可证

### 11.3 版本兼容矩阵

建议维护一份运行时矩阵

| 组件 | 版本 | 配置 schema | 支持指纹模式 | 支持 Compact | 回滚版本 |
| --- | --- | --- | --- | --- | --- |
| LiteLLM | 当前部署版本 | 号池 API 版本 | 由代理能力决定 | 是或否 | 上一个可用版本 |
| Account Pool Manager | 当前部署版本 | Manager schema | 期望字段 | 路由能力 | 上一个可用版本 |
| CLIProxyAPI | 固定镜像版本 | 配置 schema | off/device/session/full | 是或否 | 上一个镜像 |
| Dashboard | 当前部署版本 | 页面 contract | 仅展示 | 仅展示 | 上一个构建 |

启动或升级时，如果 Manager 要求的字段高于 CLIProxyAPI 实际支持能力，必须显示不兼容并阻止错误配置下发，不能静默忽略

## 12. 测试矩阵

| 范围 | 必测内容 |
| --- | --- |
| 页面 | 账号卡片、额度窗口、状态、模型、配置保存、错误提示、权限隐藏、移动端配置入口 |
| Manager | 创建、授权、重新授权、删除、重启恢复、幂等、版本冲突、配置对账、敏感信息脱敏 |
| 权限 | 管理员、有效卡片 Key、失效卡片 Key、卡片范围、模型范围和越权请求 |
| 路由 | 自动、随机、优先级、权重、备用、配额不足、套餐优先、模型排除和别名 |
| 会话 | 同卡片 Key 隔离、不同卡片 Key 隔离、TTL、账号冷却后重新绑定、并发请求竞争 |
| 配额 | 快照解析、快照过期、请求前保留、成功确认、失败释放、重启恢复和 reset 恢复 |
| 故障 | 连接失败、401、403、429、5xx、超时、不可重试请求、流式中断和切换边界 |
| 身份和指纹策略 | 各供应商模式、账号类型条件、请求改写、响应反向映射、父子关系和跨账号隔离 |
| 客户端策略 | User-Agent、Originator、app-server 例外和拒绝行为 |
| Compact | `/v1/responses/compact`、别名模型、流式、错误、未支持账号、用量记录 |
| 协议 | Chat、Responses、图片、WebSocket、压缩请求和请求体恢复 |
| 安全 | token 不出现在页面、日志和错误响应；内部账号地址不可从公共入口访问；Docker 网络隔离 |
| 升级 | 旧数据卷、旧凭据、旧配置、滚动升级、能力不匹配和回滚 |

需要优先写能够在功能缺失时失败的测试，不写只重复实现细节的浅层测试。对请求路由和权限尤其要使用跨账号、跨 Key 的反例验证

## 13. 验收标准

### 页面验收

- 账号卡片可以看到状态、套餐、配额、重置时间、启用模型和最近错误
- 设置可以修改账号开关、并发、冷却、代理、模型和路由策略
- 各供应商身份或指纹策略和 Compact 字段有独立说明，不把不同供应商能力或不同层次的 Compact 合成一个开关
- 保存后能显示同步状态和失败原因
- 管理员可以管理完整号池；NewAPI 下游用户不会直接看到号池账号

### 数据面验收

- NewAPI 只拿到卡片 Key，不拿到上游 OAuth 或 CLIProxyAPI 凭据
- 未授权账号、禁用账号、冷却账号和模型不匹配账号不会被选择
- 同一会话在 TTL 内按策略复用账号，不同卡片 Key 之间不会共享粘性绑定
- 可重试错误可以按规则切换账号，不可重试请求不会被盲目重放
- 账号、模型和卡片 Key 维度都有可查询的路由与用量记录
- Cockpit 纳入清单中的每个数据面设置实际影响代理请求，关闭或不支持时不会静默改写请求
- Compact 请求完成端到端验证

### 运维验收

- CLIProxyAPI 可以独立升级和回滚，不需要重建账号身份
- Cockpit 页面新增能力可以按字段和行为重新实现，不依赖每个账号安装 Cockpit
- Manager 重启、Docker 暂时失败和上游短暂不可用后可以恢复
- 既有 `account-pool/uv.lock` 和 `ui/litellm-dashboard/tsconfig.tsbuildinfo` 等用户已有修改不被覆盖

## 14. 当前最合理的落地顺序

先核实 Cockpit 全部设置并完成账号卡片、配额、状态、模型和配置页面，再实现卡片级 Key 和 NewAPI 对接，然后实现卡片内部的候选过滤、优先级、会话粘性、冷却和故障切换。之后接入各供应商的请求、身份和协议策略，最后再接本机 Codex、Cursor 多开

这样可以先实现你真正需要的服务端目标：NewAPI 的多个下游用户通过各自的下游权限使用号池卡片，NewAPI 使用卡片 Key 访问指定的上游账号或账号集合，号池统一管理账号、配额和切换。桌面切号和多开属于独立的客户端产品能力，不会阻塞服务端号池上线
