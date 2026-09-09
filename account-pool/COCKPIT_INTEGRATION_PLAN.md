# Cockpit 能力整合到 LiteLLM 号池的核实与实施计划

## 1. 文档目的

本文把 Cockpit Tools v1.3.47 已核实的页面能力、配置能力和请求执行能力，映射到当前 LiteLLM 号池项目，形成后续开发可以逐项验收的计划

本文的目标使用场景是：管理员在 LiteLLM 中维护多个上游账号，向多个下游用户发放 LiteLLM Virtual Key；下游用户只能使用被授权的账号范围和模型，LiteLLM 统一负责鉴权、额度、限流和审计，号池网关负责账号选择和上游转发

本文不把本机 Codex、Cursor 的桌面进程管理当作服务端号池的必要条件。它们属于第二阶段的本机客户端能力

## 2. 已核实版本与现有项目基线

| 项目 | 已核实版本或状态 | 结论 |
| --- | --- | --- |
| Cockpit Tools | v1.3.47，commit `deacbe44` | 页面、桌面客户端和账号策略的参考实现 |
| Cockpit 内嵌 CLIProxyAPI | v7.2.155，commit `7fac6b15` | Cockpit 当前绑定的上游代理版本 |
| 当前号池 CLIProxyAPI | v7.2.146 | 与 Cockpit 内嵌版本有版本差异，需要单独升级或适配 |
| LiteLLM Responses Compact | 已有 `/v1/responses/compact` 支持 | 需要验证号池链路是否完整转发 |
| 当前号池 | 已有环境创建、OAuth、配置、配额观测、冷却、代理、模型和 LiteLLM Deployment 对账 | 已有生命周期基础，缺少高级请求路由和多用户账号授权闭环 |

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
  -> LiteLLM Virtual Key
  -> LiteLLM 鉴权、账号授权、用户预算、限流、审计
  -> 号池路由层
  -> 账号范围过滤、模型过滤、优先级、权重、会话粘性
  -> 单账号 CLIProxyAPI 环境
  -> Codex 或其他上游服务
```

控制面和数据面必须分开

- 控制面保存账号、授权、配置、路由规则、用户授权和审计数据
- 数据面处理真实模型请求、账号选择、会话绑定、冷却和故障切换
- Manager 负责环境生命周期，不承载模型请求
- LiteLLM 负责下游用户身份和 Virtual Key，不把 Manager 当作公共模型网关
- CLIProxyAPI 负责单账号的上游协议适配和账号凭据使用

这样做后，多个下游用户可以共用一个集中式号池，账号密钥不会下发给下游用户。上游看到的是服务端发出的请求和被选择的上游账号，但不能由此保证上游始终把所有请求判定为同一个人。出口 IP、TLS 连接、并发、请求节奏、请求内容、上游风控和账号本身的状态仍可能产生差异

## 4. Cockpit 页面功能完整矩阵

下表是页面层需要覆盖的功能。页面只负责展示、编辑和发起操作，真正的权限判断、配置保存和请求执行必须在服务端或代理层再次校验

| 页面模块 | Cockpit 能力 | 当前号池情况 | 整合后的目标 |
| --- | --- | --- | --- |
| Dashboard | 总账号数、可用数、冷却数、错误数、额度和请求概况 | 有账号列表和状态，缺少统一汇总 | 增加汇总卡、可用率、近期错误、额度更新时间和路由健康 |
| 账号卡片 | 邮箱或显示名、套餐、额度窗口、重置时间、状态、启用模型、标签 | 已有基础卡片、额度、状态和模型 | 补充套餐、标签、优先级、权重、备用标记、会话绑定数和最近错误 |
| 账号详情 | OAuth 状态、授权时间、账号类型、模型、配额、配置和操作历史 | 已有授权和环境详情 | 增加账号级路由策略、模型排除、指纹策略、客户端策略和 Compact 投影配置 |
| 创建账号 | 选择供应商和渠道，启动 OAuth 或设备码授权 | 已支持多渠道定义和授权流程 | 沿用现有流程，补充默认策略快照和权限归属初始化 |
| 重新授权 | 重新打开授权流程、处理过期和失败 | 已有重新授权和 state 防重放 | 增加授权失败原因、最近一次成功授权和后台恢复状态 |
| 导入导出 | 导出账号配置或批量导入账号 | 当前以 OAuth 环境为主 | 只允许导出非敏感元数据；凭据导入必须采用受控后台流程，禁止页面返回完整 token |
| 删除账号 | 摘除路由、停止实例、删除数据和元数据 | 已有清理检查点和删除流程 | 增加删除前引用检查、用户授权解绑和审计记录 |
| 标签和分组 | 标签、分组、筛选、批量选择 | 当前缺少完整标签分组 | 增加账号标签、业务分组、供应商分组和批量操作 |
| 批量操作 | 批量刷新额度、授权、启停、冷却、删除、修改路由策略 | 当前已有部分批量能力基础 | 所有批量动作使用异步任务、幂等键和逐账号结果 |
| API Service | 启停服务、Base URL、端口、服务 Key、健康状态 | 当前服务由 LiteLLM 和 Manager 配置控制 | 页面展示服务状态；公共入口只保留 LiteLLM，不向每个账号发布宿主机端口 |
| API Key 管理 | Key 名称、账号范围、优先账号、模型权限、Token 限额、状态 | 当前 LiteLLM 有 Virtual Key，但未与账号范围闭环 | 以 LiteLLM Virtual Key 为主对象，保存账号授权、模型权限、预算和限流 |
| 路由策略 | 自动、随机、单账号、配额、套餐、到期、自定义 | 当前主要通过 Deployment 对账和 LiteLLM 路由 | 增加号池路由层，先过滤候选，再执行策略排序和选择 |
| 账号优先级 | 优先账号、权重、备用账号、固定顺序 | 当前缺少请求级优先级 | 增加规则表和运行时排序，优先账号失败后才进入备用账号 |
| 会话粘性 | 按会话或客户端把请求尽量固定到同一账号 | 当前缺少 | 按 Virtual Key、会话 ID 和模型建立隔离的粘性键，并设置 TTL |
| 模型策略 | 全局模型排除、账号模型排除、模型别名、Key 模型白名单 | 当前已有启用模型，缺少完整排除和别名 | 统一在请求进入代理层时校验，目录展示与真实可用模型保持一致 |
| 配额策略 | 配额不足过滤、额度保留、窗口重置恢复 | 当前已有配额观测和自动冷却 | 增加请求前额度保留、请求后释放或确认、快照过期保护 |
| 故障处理 | 重试、冷却、备用账号、错误分类和故障切换 | 当前已有冷却和生命周期恢复 | 在代理层实现可重试错误分类和单请求故障切换，避免重复提交不可重试请求 |
| 高级传输 | WebSocket、图片策略、超时、调试日志 | 当前部分由 CLIProxyAPI 提供 | 统一透传策略，按账号和 Key 记录可审计的请求元数据 |
| 统计 | 账号、模型、Key 维度的请求数、Token、延迟、成本和错误 | 当前 LiteLLM 有通用日志，账号维度不足 | 增加 account_id、virtual_key_id、route_reason 和 upstream_status 维度 |
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
| Codex 请求策略 | `codex_fingerprint_mode` | 账号未设置时解析为 `session`，代理未收到字段时为 `off` | 账号策略表，投影到 CLIProxyAPI | CLIProxyAPI 请求改写和响应反向映射 |
| Codex Compact 投影 | `compact_ui`、`model_context_window`、`model_auto_compact_token_limit`、`context_management_experimental` | 分开设置，默认不强制打开 | 账号策略表或本机客户端配置 | 本机 Codex 配置或请求数据面，不能混成一个字段 |
| 图片 | `image_generation_policy`、并发和图片账号范围 | 继承账号能力 | Key 或账号策略 | 代理层图片请求选择器 |
| WebSocket | `websocket_enabled`、超时和 Origin 策略 | 按供应商默认 | 渠道配置 | CLIProxyAPI 和网关连接层 |
| 调试 | `debug_log_enabled`、保留时间、敏感字段脱敏 | 关闭 | 管理配置 | 日志管道，不记录 token 和完整请求体 |
| 统计 | `usage_account_id`、`usage_key_id`、`route_reason` | 自动写入 | 请求日志和用量表 | LiteLLM 回调、代理事件和统计查询 |

### 5.1 指纹策略的准确边界

Cockpit 内的 Codex 指纹代码实际改写的是 Codex 请求中的一组标识字段，不是把三台设备的全部硬件特征统一。已核实的模式如下

| 模式 | 已核实行为 |
| --- | --- |
| `off` | 不改写 |
| `device` | 改写 installation、工作区路径、Git remote、commit SHA，保留 session 和 thread |
| `session` | 在 device 基础上，按账号和原始会话改写 session、thread、turn、window 及父子关系 |
| `full` | 进一步把 thread 和父线程收敛到映射后的 session |

它只对符合条件的标准 Codex OAuth 账号自动启用。API Key、Agent Identity、Web Session 和 Access Token 不自动套用这套规则。账号没有设置时，Cockpit 投影层会对符合条件的 OAuth 账号下发 `session`；代理没有收到字段时，CLIProxyAPI 默认是 `off`

该能力不能统一 IP、TLS 指纹、操作系统、浏览器、硬件、并发节奏或请求内容。实现时应把它命名为 Codex 请求标识策略，避免在产品中承诺“统一设备指纹”或“保证上游识别为同一个人”

### 5.2 Compact 必须拆分

产品中不能只设计一个“Compact 模式”开关，因为它对应多个不同层次

| 名称 | 作用 | 所属层 |
| --- | --- | --- |
| 紧凑视图 | 页面显示方式 | Dashboard UI |
| `model_context_window` | 本机模型上下文窗口 | Codex `config.toml` |
| `model_auto_compact_token_limit` | 本机自动压缩阈值 | Codex `config.toml` |
| `features.context_management.experimental_mode` | 官方客户端实验开关 | Codex 客户端配置 |
| `/v1/responses/compact` | 对话压缩数据面接口 | LiteLLM、CLIProxyAPI 和上游适配 |
| DCP 额度注入 | 本机页面或会话辅助能力 | 桌面端 |

## 6. 两类功能边界

### 6.1 可以直接放进服务端控制面

- 账号卡片、状态、套餐、配额窗口和更新时间
- OAuth 创建、重新授权、删除和恢复
- 标签、分组、搜索、筛选和批量任务
- 账号启停、人工冷却、并发、代理配置和模型启用
- Virtual Key、用户、团队和账号授权关系
- Virtual Key 的模型白名单、排除列表、预算、Token 限额和速率限制
- 路由组、账号优先级、权重、备用标记和自定义顺序
- 统计索引、请求审计、错误分类和健康检查
- 全局默认配置、账号覆盖配置和 Key 覆盖配置
- LiteLLM Deployment 的创建、更新、删除和对账
- CLIProxyAPI 镜像版本、配置版本和期望状态管理

服务端只保存策略和状态，不应该把下游用户的请求直接转发给 Manager。Manager 也不应该持有对外模型网关职责

### 6.2 必须放进代理或数据面

- 根据 Virtual Key、模型和会话选择真实账号
- 过滤未授权、禁用、冷却、额度不足或模型不支持的账号
- 执行优先级、权重、随机、配额优先、套餐优先和备用账号策略
- 生成和读取会话粘性键，并按 TTL 管理绑定
- 请求级并发占用和释放
- 对明确可重试的错误执行备用账号切换
- Codex 请求字段改写和响应反向映射
- User-Agent、Originator 等客户端准入判断
- 模型别名转换和上游模型名恢复
- 图片、WebSocket、Compact 等协议级路由
- 账号级、Key 级和模型级用量事件记录

如果只在页面保存这些字段，而没有在请求进入代理时执行，它们只是假配置，不会影响真实请求

### 6.3 暂时保留在桌面端

- 本机 Codex 或 Cursor 进程启动、停止和多开
- 本机账号切换和本地配置文件写入
- WSL 同步
- 本机会话 JSONL 管理
- 本机 Compact 配置和 DCP 页面注入
- 本机 Codex、Cursor 实例与窗口状态

桌面端可以调用服务端的账号和策略 API，但不能把桌面进程能力硬塞进 Linux 号池 Manager。服务端部署不需要安装完整 Cockpit

## 7. 多用户和 Virtual Key 模型

目标应定义为“一个集中式账号池服务多个下游用户”，而不是把每个下游用户映射成一个上游账号

### 7.1 关系模型

```text
User 或 Team
  -> Virtual Key
  -> Account Pool Policy
  -> 允许的账号集合
  -> 允许的模型集合
  -> 预算、速率和并发限制
  -> 号池代理选择具体账号
```

建议增加以下概念

| 对象 | 主要字段 | 作用 |
| --- | --- | --- |
| `account_pool_account` | `environment_id`、供应商、状态、标签、套餐、路由策略 | 对应一个隔离的上游账号环境 |
| `account_pool_route_group` | 名称、策略、默认模型、会话设置 | 定义一组账号的选择规则 |
| `account_pool_route_member` | `route_group_id`、`environment_id`、优先级、权重、备用标记、模型覆盖 | 定义账号在路由组中的位置 |
| `account_pool_key_policy` | Virtual Key ID、路由组、账号范围、模型白名单、排除列表、预算和限额 | 把 LiteLLM Key 绑定到允许的上游范围 |
| `account_pool_session_binding` | Key ID、会话哈希、模型、账号 ID、创建和过期时间 | 保存会话粘性 |
| `account_pool_quota_state` | 账号 ID、窗口、剩余量、快照时间、保留量 | 支持配额优先和额度保留 |
| `account_pool_request_event` | 请求 ID、Key ID、账号 ID、模型、路由原因、状态、延迟、Token | 支持审计和统计 |

下游用户只接触 LiteLLM Virtual Key。账号 OAuth、refresh token、CLIProxyAPI 内部 Key 和账号容器地址都不能返回给下游用户

### 7.2 请求时的权限顺序

每个请求必须按以下顺序处理

1. LiteLLM 校验 Virtual Key、用户或团队状态
2. 检查模型、预算、速率、并发和请求格式
3. 读取 Key 对应的账号范围和路由组
4. 从账号范围中排除禁用、冷却、未授权、模型不匹配和配额不足账号
5. 根据会话粘性、优先级、权重、套餐和配额策略排序
6. 选择账号并建立请求级占用
7. 代理层把请求转发到目标账号的 CLIProxyAPI
8. 根据结果更新额度、冷却、会话绑定和用量事件
9. 只有明确可重试的错误才切换到下一个候选账号

权限必须在 LiteLLM 和代理层各校验一次。不能只依赖页面隐藏账号，也不能只依赖 CLIProxyAPI 的全局账号列表

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
Virtual Key 请求模型
  -> LiteLLM 身份、预算、模型和限流
  -> Account Pool Router 读取 Key Policy
  -> 账号范围过滤
  -> 会话粘性命中则优先原账号
  -> 否则按策略选择候选账号
  -> 账号并发占用
  -> CLIProxyAPI 请求策略改写
  -> 上游 Codex
  -> 响应返回并反向映射
  -> 更新配额、统计、会话和冷却状态
```

“切号”在服务端场景不是修改用户电脑上的登录账号，而是下一次请求或故障切换时选择另一个上游账号。只有桌面端切号才会写入本机 Codex 或 Cursor 配置文件

### 8.4 Compact 请求

```text
/v1/responses/compact
  -> LiteLLM 保留 Responses 请求语义
  -> 号池路由选择支持 Compact 的账号
  -> CLIProxyAPI 识别固定路径并转发
  -> 上游返回压缩结果
  -> LiteLLM 保留响应结构和用量记录
```

Compact 必须加入端到端测试，因为普通 `/v1/responses` 能通，不代表 `/v1/responses/compact`、流式请求和别名模型都能通

## 9. 数据模型和 API 设计

### 9.1 账号策略字段

建议在现有 `EnvironmentConfiguration` 基础上增加独立的策略对象，避免把生命周期字段、路由字段和 Codex 协议字段混成一个大配置

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
  session_affinity
  session_affinity_ttl
  quota_reserve
  quota_reserve_percent
  quota_snapshot_max_age
  retry_profile
  fallback_enabled
  codex_fingerprint_mode
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
| `GET` | `/account_pool/environments` | 管理员或授权用户读取可见账号摘要 |
| `GET` | `/account_pool/environments/{id}/details` | 读取账号详情、策略、配额和同步状态 |
| `PUT` | `/account_pool/environments/{id}/policy` | 保存账号策略，使用版本号防止覆盖 |
| `POST` | `/account_pool/environments/{id}/cooldown` | 手动冷却或解除冷却 |
| `GET` | `/account_pool/route-groups` | 读取路由组 |
| `PUT` | `/account_pool/route-groups/{id}` | 保存路由策略和账号顺序 |
| `GET` | `/account_pool/key-policies/{virtual_key_id}` | 读取 Virtual Key 的账号范围和模型权限 |
| `PUT` | `/account_pool/key-policies/{virtual_key_id}` | 保存 Key 与账号范围的授权关系 |
| `GET` | `/account_pool/usage` | 按账号、模型、Key 查询统计 |
| `POST` | `/account_pool/health-checks` | 发起健康检查或明确标注会消耗额度的测试 |
| `GET` | `/account_pool/sync-status` | 查询期望配置和观察配置的差异 |

数据面可以继续复用 LiteLLM 的模型请求入口，但必须在请求上下文中携带不可伪造的内部字段，例如 Virtual Key ID、route group ID 和 request ID。下游传入同名 Header 不能覆盖这些字段

## 10. 分阶段实施计划

### 阶段 0：冻结边界和版本

- 固定 CLIProxyAPI 版本和 Cockpit 行为审计版本
- 记录每个能力是 Manager、LiteLLM、代理层还是桌面端
- 明确许可证边界，禁止直接复制受限的 Cockpit 页面或定制代码
- 保留现有账号数据、Compose 项目、数据卷和未提交工作区文件

验收：计划中的每项能力都有归属、字段和执行位置

### 阶段 1：完成账号卡片和配置页面

- 账号卡片补充套餐、配额窗口、重置时间、标签和最近错误
- 详情页增加模型选择、账号开关、并发、冷却、代理和同步状态
- 增加配置版本、保存成功、保存失败和后台同步中的状态
- 增加批量刷新配额、批量冷却和批量启停

验收：页面显示的数据来自公开 contract，不返回 OAuth token、refresh token、完整配置或代理密码；每个设置保存后可以在页面看到期望版本和观察版本

### 阶段 2：建立多用户账号授权

- 复用 LiteLLM Virtual Key、用户和团队身份
- 增加 Key 到账号或路由组的授权关系
- 增加 Key 模型白名单、排除列表、预算、Token 限额和速率限制
- 管理员可查看全部账号，普通用户只能看到授权范围的摘要
- 请求层和管理接口都做权限校验

验收：用户无法通过改 URL、改模型名、改 Header 或直接请求账号内部地址访问未授权账号

### 阶段 3：实现集中式账号路由

- 建立候选过滤器
- 实现自动、随机、优先级、权重、配额优先和套餐优先
- 实现备用账号和明确可重试错误的故障切换
- 实现账号级并发占用
- 实现请求、账号、模型和 Key 维度用量事件

验收：路由选择结果可以解释，例如“命中会话粘性”“账号额度不足”“账号处于冷却”“优先账号失败后切换备用账号”

### 阶段 4：实现会话粘性、配额保留和高级模型策略

- 按 Key、会话 ID 和模型隔离会话粘性
- 添加 TTL、清理和账号失效后的重新选择
- 增加额度快照过期判断
- 增加请求前保留、成功确认、失败释放和进程重启恢复
- 增加账号模型排除、Key 模型排除、别名和全局模型排除

验收：未授权账号不会因粘性缓存重新被选中；过期额度快照不会阻止所有账号；模型目录与真实请求权限一致

### 阶段 5：接入 Codex 请求策略

- 在账号策略中加入 `codex_fingerprint_mode`
- 在 CLIProxyAPI 适配层投影 `off`、`device`、`session`、`full`
- 对符合条件的 OAuth 账号执行请求字段改写和响应反向映射
- 单独实现客户端准入字段，不把它和指纹策略混淆
- 增加配置版本和 CLIProxyAPI 能力探测

验收：模式为 `off` 时请求不变；模式为 `session` 时只改写已核实字段；API Key 等不符合条件的账号不会误套用；跨账号切换时映射不会泄露原账号会话关系

### 阶段 6：完成 Compact 数据面

- 确认 LiteLLM `/v1/responses/compact` 到账号代理的转发路径
- 确认 CLIProxyAPI 对 Compact 的路径识别、模型改写、流式和错误处理
- 将本机 Codex 配置字段与服务端 Compact 接口分开建模
- 增加成功、未支持、模型不可用和账号切换测试

验收：普通 Responses 和 Compact 的请求、响应、错误、模型别名和用量记录都符合约定

### 阶段 7：再做本机 Codex、Cursor 多开

- 只在需要桌面能力的环境安装桌面组件
- 服务端账号、路由组和策略通过 API 提供给桌面端
- 桌面端管理本地进程、配置文件、窗口和实例目录
- 本机切号不改变服务端 Virtual Key 的授权关系

验收：服务端在没有桌面客户端时仍能完整提供多用户 API；桌面端失败不会影响集中式号池请求

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
| 权限 | 管理员、普通用户、团队 Key、无权限 Key、账号范围、模型范围和越权请求 |
| 路由 | 自动、随机、优先级、权重、备用、配额不足、套餐优先、模型排除和别名 |
| 会话 | 同 Key 隔离、不同 Key 隔离、TTL、账号冷却后重新绑定、并发请求竞争 |
| 配额 | 快照解析、快照过期、请求前保留、成功确认、失败释放、重启恢复和 reset 恢复 |
| 故障 | 连接失败、401、403、429、5xx、超时、不可重试请求、流式中断和切换边界 |
| Codex 策略 | off、device、session、full，OAuth 条件，响应反向映射，父子关系和跨账号隔离 |
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
- 指纹策略和 Compact 字段有独立说明，不把不同层次的 Compact 合成一个开关
- 保存后能显示同步状态和失败原因
- 普通用户只看到授权账号，管理员可以管理完整号池

### 数据面验收

- 下游只拿到 LiteLLM Virtual Key，不拿到上游 OAuth 或 CLIProxyAPI 凭据
- 未授权账号、禁用账号、冷却账号和模型不匹配账号不会被选择
- 同一会话在 TTL 内按策略复用账号，Key 之间不会共享粘性绑定
- 可重试错误可以按规则切换账号，不可重试请求不会被盲目重放
- 账号、模型和 Key 维度都有可查询的路由与用量记录
- Codex 请求策略实际影响代理请求，设置为关闭时保持原始请求
- Compact 请求完成端到端验证

### 运维验收

- CLIProxyAPI 可以独立升级和回滚，不需要重建账号身份
- Cockpit 页面新增能力可以按字段和行为重新实现，不依赖每个账号安装 Cockpit
- Manager 重启、Docker 暂时失败和上游短暂不可用后可以恢复
- 既有 `account-pool/uv.lock` 和 `ui/litellm-dashboard/tsconfig.tsbuildinfo` 等用户已有修改不被覆盖

## 14. 当前最合理的落地顺序

先完成账号卡片、配额、状态、模型和配置页面，再建立 Virtual Key 到账号范围的授权模型，然后实现代理层候选过滤、优先级、会话粘性、冷却和故障切换。之后接入 Codex 请求策略和 Compact 数据面，最后再接本机 Codex、Cursor 多开

这样可以先实现你真正需要的服务端目标：多个用户通过各自的 API Key 使用同一个集中式号池，服务端统一管理账号和配额，上游请求由账号池代理集中发出。桌面切号和多开属于独立的客户端产品能力，不会阻塞服务端号池上线
