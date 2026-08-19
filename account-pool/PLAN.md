<!-- 本文件记录 Account Pool 的总体架构、阶段计划、验收标准和实施进度。 -->

# LiteLLM Account Pool 总体建设计划

## 1. 文档定位

本文是 Account Pool 的总体蓝图，定义系统边界、数据模型、模块关系、实施顺序和验收标准

项目包含数据底座、解析器、健康检测、冷却、调度、管理 UI 和日志等多个子系统，不适合在一份实施任务中一次完成。后续按本文阶段拆分独立实施计划，每个阶段都必须交付可运行、可测试、可回滚的结果

## 2. 建设目标

Account Pool 是 LiteLLM 旁路运行的渠道管理与账号级调度服务

LiteLLM 保留以下职责：

- 对外提供兼容 API
- provider 协议转换
- 加密保存真实 API Key
- 创建和调用 Deployment
- 用户、团队与鉴权管理
- 请求级 usage 和费用信息采集

Account Pool 负责：

- 统一添加、编辑、导入和删除渠道
- 汇总渠道、解析结果、健康状态、额度、并发和调度状态
- 根据 URL、Key 和厂商能力解析套餐与按量信息
- 管理 5 小时、周、月、余额、并发等限制
- 排除不健康、冷却、额度耗尽和并发已满的候选渠道
- 按模型和策略生成可解释的调度顺序
- 原子选择并占用渠道，失败时继续尝试下一候选
- 提供解析、健康、冷却、调度和审计日志
- 通过 LiteLLM Admin UI 提供一致的管理体验

最终请求链路固定为：

```text
客户端
  |
  v
Account Pool Gateway
  |- 资格筛选
  |- 策略排序
  |- 原子占用
  |- 失败回退
  |
  | 将公共模型名改写为 LiteLLM deployment_id
  v
内网 LiteLLM Proxy
  v
上游厂商
```

生产环境必须限制客户端绕过 Account Pool 直连 LiteLLM，否则账号级并发、额度和冷却约束无法成立

## 3. 已确认的关键决策

### 3.1 管理入口

日常渠道管理统一在 Account Pool 中完成，不要求管理员再到 LiteLLM 单独创建或删除 Deployment

Account Pool 后端调用 LiteLLM 管理接口完成 Deployment 同步：

```text
新增渠道
-> 校验 URL 与 Key
-> 调用 LiteLLM /model/new
-> 保存 deployment_id 与 credential_ref
-> 执行解析
-> 执行健康检测
-> 加入模型候选池
```

Deployment 绑定分为两种所有权：

- `pool_managed`：由 Account Pool 创建，允许 Account Pool 更新或在收到 `delete_managed_deployment` 语义时删除对应 LiteLLM Deployment
- `externally_managed`：从 LiteLLM 导入，Account Pool 默认只解除绑定，不删除原 Deployment

所有权保存在每条渠道与 Deployment 绑定上，而不是保存在渠道本身。同一渠道可以同时包含两种所有权，删除渠道时逐条绑定执行对应语义

删除操作提供两种明确语义：

- `detach_only`：停止调度并从号池解除全部绑定，保留所有 LiteLLM Deployment
- `delete_managed_deployment`：停止调度，等待现有租约释放或到达绝对期限，删除 `pool_managed` Deployment，并从号池解除 `externally_managed` 绑定但保留其 LiteLLM Deployment

两种语义都必须先让渠道进入不可获取的 `pending_delete` 状态，且只有在全部绑定已从号池解除后才完成渠道删除。渠道删除永远不删除 `externally_managed` Deployment，未授权的外部绑定不阻塞渠道删除，也不会留在已删除渠道上。若管理员确实要删除外部 Deployment，必须在删除渠道前对单条绑定调用独立的 `delete_external_deployment` 操作并再次确认；该操作不得作为渠道删除请求的参数或隐式分支

Account Pool 与 LiteLLM 不使用分布式事务。每次创建、更新或删除都生成稳定的 `operation_id`，并把同步状态保存为：

```text
pending_create | pending_update | pending_delete | applied | failed
```

调用 LiteLLM 前先提交 desired state，成功后再记录 applied state。Account Pool 创建的 Deployment 在 LiteLLM `model_info` 中写入不含凭证的 `channel_id`、`binding_id`、`operation_id` 和 `managed_by=account_pool`。后台 reconciler 重试幂等操作，并扫描带该标记但没有有效绑定的 Deployment。系统允许短暂不一致，但不得把失败静默视为成功；UI 必须显示待同步或失败状态，并允许管理员重试或明确清理

### 3.2 凭证边界

真实 Key 只由 LiteLLM 加密保存

Account Pool 仅保存：

- `credential_ref`
- `deployment_id`
- Key 掩码
- Key 指纹

创建、轮换、校验和重新解析时，Key 可以短暂经过 Account Pool 进程并直接发送给 LiteLLM 或目标厂商，但不得写入 PostgreSQL、Redis、YAML、JSON 快照、日志、API 响应或浏览器持久存储

UI 不提供完整 Key 回显。编辑渠道时留空表示不轮换，输入新值表示替换凭证。需要访问厂商凭证接口的解析只能由管理员提交一次性 Key 后执行，Key 仅存在于接收该请求的 Account Pool 实例及其本地解析任务内存中；任务记录和任务 ID 可以持久化，但 Key 不得进入任务记录、跨实例消息、持久队列或重试载荷。接收实例必须先在本进程成功接管任务再返回任务 ID，任务不得由其他实例接管或自动重试。实例退出时只允许在有上限的优雅关闭期内等待该任务；进程丢失后，后台 sweeper 将无心跳的任务标记为 `interrupted_requires_key`，管理员必须重新提交 Key 才能重试。Account Pool 不从 LiteLLM 取回 Key，也不建立第二套凭证存储。定时任务只能运行无需凭证的公开元数据解析，以及使用真实请求返回的 usage、响应头和安全错误字段进行被动校准

### 3.3 状态分层

采用四层状态分离：

| 层 | 权威内容 | 不保存的内容 |
| --- | --- | --- |
| LiteLLM | 加密 Key、Deployment、上游调用配置 | 解析历史、人工覆盖、号池调度状态 |
| PostgreSQL | 渠道、解析结果、人工覆盖、限制、策略、事件和审计 | 实时租约和完整 Key |
| JSON 快照 | 可导出的版本化解析结果 | URL、Key 和实时运行状态 |
| Redis | 并发、租约、窗口计数、短期冷却、游标和候选缓存 | 长期权威配置和完整 Key |

PostgreSQL 是 Account Pool 的持久化权威数据源。JSON 是可预览和可导出的派生快照，不允许 UI 绕过数据库直接修改文件

### 3.4 人工修正

解析原始值与人工修正分开保存

有效值按以下优先级合并：

```text
人工覆盖值 > 最新有效解析值 > 安全默认值或 null
```

重新解析不得覆盖人工修正。管理员可以逐字段撤销覆盖并恢复自动解析值

所有人工修正必须记录修改前值、修改后值、操作者、原因和时间

### 3.5 UI 方向

后端继续保持独立的 Account Pool 服务，但正式管理界面直接集成到 LiteLLM Admin UI，不采用 iframe，也不把独立 4100 页面作为最终产品入口

优先复用 LiteLLM Dashboard 已有的：

- 页面布局和左侧导航
- Card、Form、Modal、Table 和状态提示
- Provider 图标与字段展示
- Deployment 新建和编辑交互模式
- 可创建模型下拉框
- `/public/providers/fields` 的字段元数据
- `/public/litellm_model_cost_map` 的模型元数据
- TanStack Query 请求与缓存模式

Account Pool 页面通过 LiteLLM 的服务端反向代理访问 Account Pool API，内部服务令牌不得进入浏览器。LiteLLM 先使用现有管理员会话和 RBAC 对每个 Account Pool 路由执行资源与动作授权，再代理请求；代理层必须沿用 LiteLLM 同域管理接口的 CSRF 校验和安全 Cookie 策略，不接受浏览器自行提供的内部服务令牌。授权成功后，LiteLLM 以签名且短时有效的内部 actor 信封传递不可伪造的 user_id、role、request_id 和授权动作，Account Pool 验证签名、受众、过期时间和 request_id 后再写审计事件。内部服务身份只证明调用来自 LiteLLM，不得代替实际操作者身份

不能直接把 LiteLLM 支持的所有 Provider 都作为“可解析 Provider”开放。Provider 下拉框可以复用外观与元数据，但可选项必须与 Account Pool 的 parser manifest 能力合并展示：

- 有专用解析器：显示“支持自动解析”
- 仅 OpenAI 兼容：显示“通用解析”
- 无解析器：显示“需人工补充”

现有 Account Pool 原生 HTML/JS 页面在 LiteLLM Dashboard 新页面功能完整前保留为开发和故障入口。新页面完成验收并稳定运行一个发布周期后，删除旧页面及 `/api/accounts` 兼容端点，避免长期维护两套管理入口

## 4. 总体架构

```text
LiteLLM Admin UI
  |
  | 同域管理请求
  v
LiteLLM Account Pool Proxy Endpoints
  |
  | 服务端注入内部令牌
  v
Account Pool API
  |- Channel Catalog
  |- Parser Registry and Workers
  |- Effective Data Merger
  |- Health Engine
  |- Cooldown and Limit Engine
  |- Scheduler
  |- Event and Audit Service
  |- JSON Snapshot Exporter
  |
  +--> PostgreSQL
  +--> Redis
  +--> LiteLLM Management API
  +--> Provider APIs

业务请求
  |
  v
Account Pool Gateway
  |- 从 Redis 取得模型候选
  |- 读取有效限制与状态
  |- 原子 acquire
  |- 转发到 LiteLLM
  |- settle/release
  v
LiteLLM Proxy
```

每个模块只通过明确接口交换已验证的领域对象，不允许管理 API、解析器或 UI 直接操作调度器内部 Redis Key

## 5. 渠道总览

### 5.1 总览不是数据库大表

“总览表”是聚合查询视图，不把所有数据重复塞进单张表

它从渠道目录、有效解析数据、实时状态和最近事件组合出一行渠道摘要，避免解析历史、运行状态和静态配置互相污染

### 5.2 默认列

渠道总览默认展示：

- 渠道 ID
- 显示名称
- Provider
- 标准化 Base URL
- Key 掩码与指纹
- 所有权类型
- 解析器与解析状态
- 套餐名称
- 套餐可用模型数量
- 按量分组数量
- 可调度模型数量
- 余额
- 5 小时、周和月额度摘要
- 最大并发、当前并发和可用并发
- 健康状态
- 冷却状态和最近不可用原因
- 是否进入调度器
- 最近解析、探测、请求和错误时间

URL 入库前必须标准化，不允许包含用户名、密码或敏感查询参数。UI 和日志使用脱敏后的显示值

### 5.3 详情页

点击渠道后按以下区域展示：

- 基础配置与 LiteLLM Deployment 绑定
- 解析状态、解析器版本和未解析字段
- 自动解析值与人工覆盖值对比
- 套餐信息与额度窗口
- 按量分组与标准化价格
- 模型绑定
- 当前健康、冷却、并发和资格状态
- 最近解析、健康、冷却、调度和审计事件
- JSON 预览与导出

### 5.4 调度资格

总览必须区分：

- `configured`：配置存在
- `parsed`：解析完成或人工数据完整
- `healthy`：健康检查允许使用
- `capacity_available`：并发与额度允许使用
- `schedulable`：满足所有硬条件并已进入候选池

“启用”不等于“可调度”，UI 不得只根据 enabled 字段计算可用渠道数

## 6. 解析器系统

### 6.1 解析器职责

解析器是使用渠道 URL 与临时 Key 获取和标准化厂商信息的工具

每个厂商可以实现独立解析器，例如：

- GLM 官方平台
- OpenAI 官方平台
- OpenAI 兼容平台
- 其他具有独立套餐、分组或价格接口的平台

解析器不得负责调度、健康判定、额度扣减或写 Redis。它只返回经过 schema 校验的解析结果和问题报告

### 6.2 解析器选择顺序

选择顺序固定为：

1. 渠道显式指定的解析器
2. Provider 与标准化 origin 精确匹配的专用解析器
3. OpenAI 兼容通用解析器
4. 静态模板与人工录入

不允许携带 Key 依次试探所有厂商解析器，避免错误识别和凭证泄露

解析器客户端必须：

- 默认仅允许 HTTPS
- 将 Key 发送到管理员确认的标准化 origin
- 禁止跨 origin 重定向
- 执行 SSRF 防护
- 设置连接和总超时
- 限制响应体大小
- 对允许的响应字段做 schema 校验
- 从日志、异常和证据中移除凭证及敏感头

开发环境访问 localhost 必须通过明确配置开启，不能成为生产默认值

### 6.3 统一输出

统一解析结果包含两个可独立为空的模块：

```text
ParsedChannelData
  |- subscription: SubscriptionData | null
  |- metered: MeteredData | null
  |- billing_routes: tuple[BillingRoute, ...]
  |- capabilities
  |- unresolved_fields
  |- evidence
  |- warnings
```

同一渠道可以只有套餐数据、只有按量数据，或同时具有两类数据。这里的两个模块表示权益与成本证据，不默认表示 Account Pool 能选择上游如何扣费

只有当套餐和按量分别绑定到不同 Key、URL、Deployment、厂商分组，或厂商支持可控且已验证的请求参数时，系统才创建可执行的 `billing_route`。每条路由至少包含 `route_id`、`deployment_binding_id`、`mode`、可选 `provider_group_id` 和经过白名单验证的请求参数引用；路由本身不得包含 Key。否则 `billing_routes` 为空，调度器只选择 Deployment，并把套餐余额与按量价格合并用于资格判断和成本估算，不声称能够强制走套餐或按量

未知数据必须使用 `null` 或空集合表达，不得用 `0`、无限或猜测值代替

### 6.4 套餐模块

套餐模块提取用户实际已经订阅的权益，而不是厂商公开销售页面上的通用套餐描述

核心字段：

| 字段 | 含义 |
| --- | --- |
| `plan_id` | 厂商套餐标识，没有稳定标识时为 null |
| `plan_name` | 套餐显示名称 |
| `status` | active、trial、expired、suspended 或 unknown |
| `starts_at` | 套餐开始时间 |
| `expires_at` | 套餐到期时间 |
| `models` | 套餐内可用模型集合 |
| `balance` | 套餐余额或资源单位余额 |
| `currency` | 余额币种，没有货币语义时为 null |
| `channel_concurrency` | 渠道共享并发上限 |
| `model_concurrency` | 按模型并发上限 |
| `limits` | 5 小时、周、月及厂商自定义限制 |

每个限制统一表示为：

```text
QuotaLimit
  |- scope: channel | model | group
  |- subject_id
  |- kind: requests | tokens | credits | currency | provider_units
  |- window_type: rolling | fixed | reset_at | lifetime
  |- duration_seconds
  |- limit
  |- used
  |- remaining
  |- reset_at
  |- source
  |- observed_at
```

5 小时限制不假定为固定窗口。解析器必须明确厂商语义：

- 滚动 5 小时使用 `rolling + duration_seconds=18000`
- 固定周期使用 `fixed` 并提供边界
- 厂商只返回恢复时间时使用 `reset_at`
- 无法确认窗口语义时保留剩余值并报告 `window_type` 未解析

周和月限制同样不得仅按本地自然周或自然月猜测，优先使用厂商提供的 `reset_at` 和时区

### 6.5 按量模块

按量模块按厂商分组提取：

| 字段 | 含义 |
| --- | --- |
| `group_id` | 厂商分组稳定标识 |
| `group_name` | 分组显示名称 |
| `models` | 分组内可用模型 |
| `currency` | 原始计价币种 |
| `unit` | 原始计价单位 |
| `input_price` | 输入价格 |
| `output_price` | 输出价格 |
| `cache_read_price` | 缓存读取价格 |
| `cache_write_price` | 缓存写入价格 |
| `group_multiplier` | 分组倍率，默认 1 |
| `effective_prices` | 应用倍率后的标准化价格 |
| `concurrency` | 分组或模型并发限制 |

所有金额使用十进制定点类型，不使用二进制浮点

同时保存：

- 厂商原始价格与单位
- 分组倍率
- 应用倍率后的有效价格
- 标准化为每百万 token 的比较价格
- 汇率和单位无法转换时的未解析原因

有效价格计算规则为：

```text
effective_price = source_price * group_multiplier
```

如果厂商倍率语义不是乘法，专用解析器必须在结果中输出已经标准化的有效价格，并保留原始证据与转换说明

### 6.6 模型标识

解析结果同时保留：

- 厂商原始模型 ID
- LiteLLM model 名称
- Account Pool 公共模型名称

映射不能仅依赖字符串猜测。自动匹配结果允许管理员修正，并记录为覆盖层

### 6.7 解析器模板

解析器框架阶段必须创建：

```text
account_pool/provider_services/PARSER_TEMPLATE.md
```

模板必须说明：

- 目录结构和文件职责
- manifest 必填字段
- URL 与 Provider 匹配规则
- 认证方式和凭证生命周期
- 套餐字段与数据类型
- 按量字段与数据类型
- 模型映射方式
- 金额、币种、倍率与标准化规则
- 窗口和 reset_at 语义
- 支持、部分支持和不支持能力声明
- unresolved field 的编码规则
- 安全证据保留规则
- JSON 快照格式
- fixture 和契约测试要求
- 禁止记录或返回 Key 的要求

每个解析器目录继续按清晰职责拆分：

```text
provider_services/{provider_id}/
├── manifest.py
├── schemas.py
├── client.py
├── parser.py
└── fixtures/
```

### 6.8 解析失败与兜底

解析运行状态包括：

- `success`：支持字段均成功提取
- `partial`：得到有效数据，但存在未解析字段
- `unsupported`：平台不提供对应接口或能力
- `authentication_failed`：凭证无效或权限不足
- `transport_failed`：网络、超时或上游服务错误
- `invalid_response`：响应不符合声明的 schema
- `manual_required`：自动路径全部结束，需要人工补充

失败必须产生结构化问题报告：

- 解析器和版本
- 失败阶段
- 安全错误类别
- 未解析字段路径
- 是否可重试
- 建议的下一步
- 脱敏证据摘要
- 首次和最近发生时间

解析失败不等于渠道健康失败。只有凭证无效、目标不可达或实际探测失败等健康证据才改变健康状态

没有专用解析器时，系统尝试 OpenAI 兼容通用解析器。通用解析器仍无法得到套餐或价格时，保留模型发现结果，其余字段为空，并进入人工覆盖流程

## 7. JSON 快照与导出

### 7.1 存储位置

运行时默认写入：

```text
account-pool/data/parser-snapshots/
├── latest.json
└── history/
    └── {channel_id}/
        └── {parser_run_id}.json
```

该目录是运行数据，不提交到 Git

`latest.json` 以渠道 ID 为键，用于 UI 预览、离线审阅和批量导出。它是权威解析记录与覆盖层的完整脱敏投影，不用于把导出内容原样恢复成 parser run，也不包含租约、inflight、restriction 或额度计数，不能恢复调度运行态：

```json
{
  "channel-001": {
    "schema_version": 1,
    "parser_id": "openai-compatible",
    "parser_version": "1.0.0",
    "parser_run_id": "run-001",
    "parsed_at": "2026-08-18T10:00:00Z",
    "status": "partial",
    "raw_result": {
      "subscription": null,
      "metered": null,
      "billing_routes": [],
      "capabilities": ["model_discovery"],
      "unresolved_fields": ["subscription", "metered"],
      "evidence": [],
      "warnings": [
        "The provider exposes model discovery but no billing endpoint"
      ]
    },
    "effective_result": {
      "subscription": null,
      "metered": null,
      "billing_routes": [],
      "capabilities": ["model_discovery"],
      "unresolved_fields": ["subscription", "metered"],
      "evidence": [],
      "warnings": [
        "The provider exposes model discovery but no billing endpoint"
      ]
    }
  }
}
```

快照不得包含 URL、Key、credential_ref、Authorization 头或未经筛选的原始响应。快照 schema 必须覆盖 `ParsedChannelData` 的全部可导出字段，包括 subscription、metered、billing_routes、capabilities、unresolved_fields、evidence 和 warnings；示例中的空值只表示该次解析没有相应结果，不表示字段可以从 schema 中省略

### 7.2 一致性

写入顺序为：

1. 在 PostgreSQL 事务内保存解析运行和规范化结果
2. 提交事务
3. 生成临时 JSON 文件
4. 原子替换目标快照
5. 记录导出成功或失败事件

快照生成失败不回滚已成功的解析结果，调度器继续读取 PostgreSQL 或现有缓存。后台任务负责重试快照生成

导入 JSON 时必须验证 schema 版本和字段类型，并作为新的人工覆盖或受控导入记录写入数据库，不能直接覆盖权威表

## 8. 健康检测

### 8.1 混合检测

采用真实请求被动检测、按需主动检测和管理员手动检测相结合的方式

被动检测来源：

- 请求成功
- 连接失败和超时
- HTTP 状态码
- LiteLLM 标准化错误类型
- usage、响应头和厂商错误体中的安全字段
- 流式开始、完成和中断

主动检测用于：

- 新增或修改渠道后的首次验证
- 长期没有真实流量的渠道
- 冷却结束后的半开恢复
- 管理员手动检测

主动检测通过 LiteLLM 定向调用指定 Deployment，使用该渠道支持的低成本模型和最小请求。额度耗尽、余额耗尽或管理员禁用的渠道不执行自动探测

### 8.2 健康维度

健康不能只保存一个布尔值，应按故障范围区分：

- 渠道级：网络、凭证、账户状态和全局服务错误
- 模型级：模型不存在、无权限或模型单独不可用
- 分组级：计费分组或路由分组不可用

健康状态包括：

```text
unknown
-> healthy
-> degraded
-> unhealthy
-> half_open
-> healthy
```

这是可能的状态转换示意，不要求按单一路径依次经过全部状态；每次转换都由明确事件触发。管理员配置的 `disabled` 与运行时 `unhealthy` 分开保存

### 8.3 错误分类

| 信号 | 默认处理 |
| --- | --- |
| 成功 | 更新延迟和成功时间，降低连续失败计数 |
| 401 或明确凭证无效 | 渠道进入 credential_invalid，停止调度，等待轮换 Key |
| 403 | 区分账户、模型或区域权限，不默认禁用整个渠道 |
| 404 模型不存在 | 只排除该渠道与模型绑定 |
| 408、连接失败、超时 | 增加传输失败计数，达到阈值后渠道冷却 |
| 429 | 交给限额分类器判断并发、速率、窗口额度或未知限流 |
| 5xx | 增加服务失败计数，达到阈值后短期冷却 |
| 余额不足 | 标记余额耗尽，停止按量调度 |

错误类别无法确认时，保留原始安全分类并采用短期、可恢复的保守冷却，不猜测永久状态

### 8.4 调度关系

不健康范围决定排除粒度：

- 渠道级不健康：渠道所有模型不进入调度
- 模型级不健康：只排除对应模型绑定
- 分组级不健康：只排除对应分组中的候选

恢复必须经过冷却到期后的半开探测或足够可信的真实成功请求

## 9. 冷却与限制机制

### 9.1 冷却不是单一时间字段

每条运行限制记录包含：

```text
Restriction
  |- scope
  |- subject_id
  |- reason_code
  |- source
  |- starts_at
  |- retry_at
  |- reset_at
  |- details
  |- state: active | half_open | cleared
```

原因码按状态来源分组：

- restriction：`rate_limited`、`five_hour_exhausted`、`weekly_exhausted`、`monthly_exhausted`、`balance_exhausted`
- health：`credential_invalid`、`transport_unhealthy`、`provider_unavailable`、`model_unavailable`
- administrative：`manual_pause`、`disabled`
- acquire 瞬时结果：`concurrency_full`、`candidate_version_stale`

只有第一组创建持久化 Restriction。其余原因分别由健康事实、管理员状态或单次 acquire 结果产生。UI、资格投影、路由表和日志使用同一原因码目录，但不得因为展示统一而把同一状态写入多个权威存储

### 9.2 并发已满

并发已满是瞬时容量状态，不应把渠道标记为不健康，也不创建长时间冷却

调度器原子尝试占用时发现并发已满，应立即尝试排序中的下一个渠道

只有所有候选都失败后才向调用方返回容量不足，并给出聚合原因和最早可重试提示

### 9.3 5 小时、周和月额度

额度窗口由解析器提供的 `window_type`、`duration_seconds` 和 `reset_at` 驱动

策略：

- `remaining > safety_reserve`：允许调度
- `0 < remaining <= safety_reserve`：标记低额度，可由策略降低排序
- `remaining <= 0`：排除对应 scope，直到 reset_at
- reset_at 到达：进入半开状态，先允许少量真实请求或主动健康探测；需要厂商凭证接口才能确认额度时，提示管理员输入 Key 重新解析
- 没有 reset_at：保持排除并提示管理员修正或输入 Key 重新解析

5 小时、周和月限制分别记录，不把其中一个耗尽误认为整个账户永久不可用

实际请求 usage 作为增量事件更新本地估算，下一次厂商解析作为校准点修正漂移

### 9.4 余额耗尽

余额耗尽时：

- 明确绑定到按量计费的 `billing_route` 停止进入调度
- 没有独立可控路由时，余额耗尽作为该 Deployment 绑定的资格证据；不得假定同一凭证下的套餐权益一定还能绕过余额错误
- 同一渠道存在经验证且使用独立凭证、Deployment、分组或请求参数的套餐路由时，该路由可以继续使用
- 后台不会发起需要 Key 的自动账单解析
- 管理员充值后输入 Key 手动重新解析
- 真实请求的安全响应字段确认余额恢复，或手动解析确认恢复后，自动清除限制

余额未知不能等同于余额为零

### 9.5 未知 429 与退避

429 优先使用：

1. 厂商结构化错误码
2. `Retry-After`
3. 解析器已知的窗口 reset_at
4. 有上限的指数退避

未知 429 只创建短期 `rate_limited` 冷却。它不能自动改写为周额度或月额度耗尽

### 9.6 状态持久化

Redis 保存正在生效的限制、窗口计数和 retry_at，供 acquire 原子判断

PostgreSQL 保存：

- 限制开始和清除事件
- 厂商额度快照
- 请求 usage 增量
- 人工操作
- 周期性运行快照

周期快照和事件只用于重建可证明的限制与计数下界，不能声称精确恢复 Redis 丢失时仍在执行的租约。每个租约必须有不可被 heartbeat 延长的 `absolute_expires_at`；配置的最大绝对租约时长必须覆盖系统允许的最长请求，超过该时长的请求由网关终止

Redis 连接中断或数据集丢失后，所有 Account Pool 实例进入共享的 `runtime_recovery` 代次并停止新 acquire。恢复程序重建已知限制和额度计数，但在故障检测时间加最大绝对租约时长之前保持 fail-closed，使故障前可能存在的最后一个租约必然结束；之后才以新的 Redis 代次开放 acquire。无法确认恢复代次、快照完整性或额度安全边界时继续拒绝调度并要求运维处理，不以零 inflight、零用量或无限额度启动。旧代次的 settle、release 和 heartbeat 只能记录为迟到事件，不得修改新代次计数

## 10. 调度器

### 10.1 资格投影、排序与占用

管理员状态、健康状态和运行限制是三个独立维度：

- `administrative_state`：enabled、paused 或 disabled，由管理员操作
- `health_state`：按渠道、模型或分组保存 unknown、healthy、degraded、unhealthy 或 half_open
- `restriction_state`：按 scope 保存 reason_code、active/half_open/cleared 和恢复时间

`effective_eligibility` 是以上状态、模型绑定、额度和余额的只读投影，不作为第四份可独立修改的权威状态。`credential_invalid` 是渠道级健康证据；由它形成的排除结果通过资格投影呈现，不在健康与 restriction 中各维护一份相互竞争的状态

调度分为快照资格筛选、策略排序和逐候选原子占用：

```text
读取带版本的模型候选快照
-> 投影 administrative_state、health_state 和 restriction_state
-> 排除未绑定、模型不支持和快照时已知的硬限制
-> 按策略生成稳定排序和解释
-> 按顺序对候选执行原子 acquire
   |- 再检查候选版本、三类状态、额度与并发
   |- 成功时创建租约并递增 inflight
   `- 失败时返回机器原因码并尝试下一候选
```

筛选阶段不预占并发，也不承诺候选在 acquire 时仍可用。展示路由表与实际 acquire 使用同一资格投影和排序解释，但 UI 中的第一名不是预留结果。Redis 中的逐候选原子 acquire 才是最终选号依据

### 10.2 基础策略

首批正式策略：

- `manual_priority`：按管理员顺序主备切换
- `lowest_effective_cost`：按该模型的标准化有效价格排序
- `least_inflight`：优先当前并发较低的渠道
- `weighted_round_robin`：按权重轮询
- `quota_balanced`：优先剩余额度比例较高且并发较低的渠道

`lowest_effective_cost` 中：

- 经验证可执行的套餐 `billing_route` 在仍有套餐额度时，边际价格按 0 参与比较
- 经验证可执行的按量 `billing_route` 使用应用分组倍率后的标准化价格
- 没有独立计费路由时，使用同一 Deployment 的权益和价格证据计算估算成本，并在排序原因中标记 `billing_mode=provider_decided`
- 价格未知的候选排在已知价格之后，不伪造价格
- 同价时依次使用人工顺序、健康度、当前并发和稳定 ID 打破平局

后续只有在能够定义数据窗口、缺失值行为和最小样本数时，才增加延迟或成功率综合评分

### 10.3 人工调整

管理员可以：

- 为每个模型选择策略
- 拖动人工顺序
- 设置渠道权重
- 暂停某个渠道与模型的绑定
- 查看系统计算分数和排序原因

人工排序只影响候选优先级，不默认绕过凭证无效、额度耗尽、健康失败和并发已满等硬限制

### 10.4 模型调度表

模型列表按公共模型名分类。点击模型后展示排序后的渠道：

- 排名
- 渠道 ID
- Provider 与 URL 摘要
- Key 掩码
- Deployment ID
- 所有权
- 套餐名称、额度窗口和套餐并发
- 按量分组、输入、输出、缓存和有效价格
- 当前并发
- 健康与冷却状态
- 策略分数
- 不可用原因
- 最近成功和失败时间

如果渠道同时包含套餐与按量数据，两部分都展示。存在可执行 `billing_route` 时显示本次候选的明确计费路由；否则显示“厂商决定扣费”，并把套餐和按量信息标记为资格与成本估算证据

### 10.5 失败回退

例如请求 `gpt-5.6-sol`：

1. 读取该模型候选和策略
2. 对第一候选执行原子 acquire
3. 并发竞争失败时立即尝试下一候选
4. 第一候选在请求前失败且没有产生可计费输出时，根据错误分类尝试下一候选
5. 已经开始流式输出或存在重复计费风险时，不自动重放完整请求
6. 所有候选失败后返回结构化的无可用渠道错误

失败响应不得暴露完整 URL、Key、厂商敏感错误体或内部 Redis Key

## 11. 日志、事件与审计

### 11.1 事件类型

系统记录以下结构化事件：

- 渠道创建、修改、导入、解绑和删除
- LiteLLM Deployment 同步
- 解析开始、成功、部分成功和失败
- 人工覆盖创建、修改和撤销
- 主动与被动健康结果
- 冷却创建、延期、半开和清除
- acquire 候选筛选、选择和全部失败
- settle、release、租约超时和回收
- 策略与人工排序修改
- JSON 导出和导入

### 11.2 公共字段

事件至少包含：

- `event_id`
- `event_type`
- `occurred_at`
- `channel_id`
- `model_id`
- `deployment_id`
- `request_id`
- `lease_id`
- `reason_code`
- `actor_type`
- `actor_id`
- `safe_details`

不适用字段为 null。`pool_events` 是上述事件的统一、只追加查询入口；健康、限制、usage 和审计领域表保存可重建状态所需的规范化事实，并通过 `event_id` 关联同一个公共事件，不各自拼接成不稳定的日志 API

### 11.3 查询、索引与保留

`GET /api/events` 使用不透明游标分页。游标编码 `(occurred_at, event_id)`，排序固定为时间倒序和事件 ID 倒序，避免同一时间戳下重复或漏项。至少建立以下索引：

- `(occurred_at DESC, event_id DESC)`
- `(channel_id, occurred_at DESC)`
- `(model_id, occurred_at DESC)`
- `(request_id, occurred_at DESC)`
- `(event_type, occurred_at DESC)`
- `(reason_code, occurred_at DESC)`

每个 `event_type` 在代码中注册对应的 Pydantic `safe_details` 模型。写入前必须拒绝未声明字段并执行统一脱敏，禁止把任意字典或厂商原始响应直接写入 JSONB

默认保留最近 90 天的可查询明细。更早事件按月导出为加密、校验和可验证的归档后从在线表删除；归档不包含凭证或未筛选内容。审计事件的保留期限可以通过部署配置延长，但不能短于普通事件。归档失败时不得删除在线数据

### 11.4 脱敏

日志中禁止出现：

- 完整 Key
- Authorization、Cookie 和代理认证头
- 含凭证的 URL
- 未经筛选的厂商响应体
- 请求中的敏感用户内容

解析证据只保存允许字段的结构化摘要或散列，不保存整份响应作为捷径

### 11.5 UI

日志页面支持按以下条件筛选：

- 时间范围
- 渠道
- 模型
- 事件类型
- 健康状态
- 冷却原因
- request_id
- 成功或失败

总览和详情页可以链接到已经带筛选条件的日志视图

运行指标与审计事件分开：Prometheus 负责聚合监控，PostgreSQL 事件负责追溯具体发生了什么

## 12. PostgreSQL 数据设计

建议按职责拆分，避免单张万能表：

| 表 | 内容 |
| --- | --- |
| `pool_channels` | 渠道身份、Provider、标准化 URL、Key 掩码、指纹和管理员状态 |
| `pool_deployments` | 渠道与 LiteLLM Deployment、厂商模型和公共模型绑定，以及每条绑定的所有权 |
| `pool_parser_runs` | 每次解析的状态、解析器版本、问题摘要和时间 |
| `pool_subscription_snapshots` | 套餐解析快照 |
| `pool_quota_limits` | 规范化限制及窗口语义 |
| `pool_metered_groups` | 按量分组和倍率 |
| `pool_metered_prices` | 模型输入、输出与缓存价格 |
| `pool_billing_routes` | 经验证可执行的 Deployment 绑定、计费模式、厂商分组和参数引用 |
| `pool_field_overrides` | 人工字段覆盖与撤销状态 |
| `pool_events` | 统一、只追加的脱敏事件信封和 `safe_details` |
| `pool_health_events` | 主动和被动健康事实，通过 event_id 关联公共事件 |
| `pool_restriction_events` | 冷却和限制生命周期事实，通过 event_id 关联公共事件 |
| `pool_usage_events` | 与额度有关的请求增量，通过 event_id 关联公共事件 |
| `pool_runtime_snapshots` | 限制、额度计数下界、Redis 代次和恢复水位的周期快照；不包含可精确恢复的活跃租约承诺 |
| `pool_model_policies` | 模型策略与参数 |
| `pool_model_channel_order` | 人工顺序、权重和暂停状态 |
| `pool_audit_events` | 管理操作审计事实，通过 event_id 关联公共事件 |
| `pool_sync_state` | operation_id、desired/applied 内容、同步状态、尝试次数和最近安全错误 |

解析器返回值必须先通过 Pydantic 领域模型验证，再进入 repository，不允许用无类型字典贯穿系统

PostgreSQL schema 与 Redis key schema 都必须带版本，升级时提供迁移，不通过运行时猜测旧结构

## 13. Redis 运行设计

Redis 负责：

- 当前 inflight
- 租约及 TTL
- request_id acquire 幂等
- heartbeat
- 额度窗口计数
- 生效中的 restriction
- 半开探测令牌
- 模型候选缓存
- weighted round-robin 游标
- 最近运行快照版本

Lua 或等价原子事务负责：

- 检查候选版本
- 检查 enabled、health 和 restriction
- 检查 scope 对应的额度安全线
- 检查并发
- 创建租约并递增并发
- 幂等 release
- 防止重复 settle 扣减

长流式请求通过 heartbeat 续租，但不得越过租约的 `absolute_expires_at`。进程崩溃或 callback 丢失后，由 lease reaper 回收过期租约。Redis 不可用或代次不一致时 acquire 必须 fail-closed，并按 9.6 的隔离期恢复，不允许把缺失键解释为零 inflight

## 14. API 边界

### 14.1 渠道管理

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/channels` | 查询聚合总览 |
| `POST /api/channels` | 创建渠道并同步 LiteLLM |
| `GET /api/channels/{id}` | 查询渠道完整脱敏详情 |
| `PUT /api/channels/{id}` | 更新配置或轮换 Key |
| `POST /api/channels/{id}/detach` | 仅从号池解绑 |
| `POST /api/channels/{id}/bindings/{binding_id}/delete-external-deployment` | 对单条 externally_managed 绑定再次确认后删除 LiteLLM Deployment；必须在渠道删除前独立执行 |
| `DELETE /api/channels/{id}` | 按请求语义处理全部绑定；`detach_only` 保留所有 Deployment，`delete_managed_deployment` 删除 pool_managed 并保留但解绑 externally_managed |
| `POST /api/channels/import` | 导入已有 LiteLLM Deployment |
| `POST /api/channels/{id}/reconcile` | 重试 desired/applied 同步 |

创建、更新和删除必须支持幂等请求，并按 3.1 的 operation_id、同步状态、LiteLLM 标记和 reconciler 规则处理跨服务最终一致性

迁移期间保留现有 `/api/accounts` 下与渠道 CRUD 对应的端点作为 `/api/channels` 兼容别名，响应中发送弃用提示，但两套路由必须调用同一 application service，不维护双份业务逻辑。新 LiteLLM Dashboard 只调用 `/api/channels`；新页面切换完成并稳定运行一个发布周期后，在 Phase 5 删除兼容端点和旧页面

### 14.2 解析与覆盖

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/parser-services` | 查询解析器 manifest 和能力 |
| `POST /api/channels/{id}/parse` | 启动解析；需要凭证接口时请求体必须提供一次性 Key |
| `GET /api/channels/{id}/parser-tasks/{task_id}` | 查询不含凭证的解析任务状态 |
| `GET /api/channels/{id}/parser-runs` | 查询解析历史 |
| `GET /api/channels/{id}/effective-data` | 查询合并后的有效数据 |
| `PUT /api/channels/{id}/overrides` | 创建或修改字段覆盖 |
| `DELETE /api/channels/{id}/overrides/{field}` | 撤销字段覆盖 |
| `GET /api/channels/{id}/snapshot` | 预览 JSON |
| `GET /api/channels/{id}/export` | 导出脱敏 JSON |
| `POST /api/channels/{id}/import` | 受控导入 JSON |

解析启动接口返回任务 ID，不让可能耗时的厂商请求长期占用管理 HTTP 连接。公开元数据任务可以进入普通 worker 队列并幂等重试；携带一次性 Key 的任务必须遵守 3.2 的同实例内存执行、不可接管和 `interrupted_requires_key` 规则

### 14.3 健康、调度与日志

| 方法与路径 | 用途 |
| --- | --- |
| `POST /api/channels/{id}/health-check` | 手动主动检测 |
| `GET /api/channels/{id}/health` | 查询健康与限制详情 |
| `POST /api/channels/{id}/pause` | 管理员暂停 |
| `POST /api/channels/{id}/resume` | 清除人工暂停，不绕过其他硬限制 |
| `GET /api/models` | 模型号池概览 |
| `GET /api/models/{model}/routing-table` | 查询可解释路由表 |
| `PUT /api/models/{model}/policy` | 修改策略 |
| `PUT /api/models/{model}/order` | 修改人工顺序和权重 |
| `GET /api/events` | 以 `(occurred_at, event_id)` 不透明游标查询结构化事件与审计日志 |

现有 `/internal/acquire`、`settle`、`release` 和 `heartbeat` 保持内部服务接口，不暴露给普通浏览器用户

LiteLLM Dashboard 使用同域 `/account_pool/*` 代理路径。代理必须先完成 LiteLLM 管理员会话、RBAC 与 CSRF 校验，再由服务端注入 Account Pool 内部令牌和 3.5 定义的签名 actor 信封；浏览器不得设置或覆盖这两个内部凭据

## 15. Admin UI 信息架构

在 LiteLLM Dashboard 左侧导航增加 Account Pool 区域：

```text
Account Pool
├── 总览
├── 渠道
├── 模型调度
├── 解析任务
└── 事件日志
```

首期优先完成渠道页：

- 复用 LiteLLM Deployment 表单的 Provider 和模型选择体验
- 复用 Creatable Model Select
- 复用表格、抽屉、确认框和错误提示
- 创建后由 Account Pool 后端调用 `/model/new`
- 删除时明确展示“仅解绑”与“同时删除号池管理的 Deployment”
- 不让前端直接分别调用 Account Pool 和 `/model/new`，避免半成功状态

后续页面：

- 总览：渠道、模型、健康、额度、并发和问题统计
- 模型调度：策略、拖动排序、原因和实时路由表
- 解析任务：运行状态、字段差异、人工覆盖和 JSON 预览
- 事件日志：统一筛选和问题追踪

## 16. 当前实现基线

当前已有：

- Account Pool 网关路线
- YAML 渠道配置
- 内存与 Redis store
- acquire、settle、release、heartbeat 和租约回收基础
- priority、least inflight、weighted round-robin 和 quota-aware 基础策略
- 被动健康和固定冷却基础
- 渠道 CRUD 与 LiteLLM Deployment 同步
- GLM URL、Key 校验、模型发现和统一 partial 解析结果
- PostgreSQL catalog schema、repository、YAML 导入与只读投影基础
- OpenAI 兼容 URL、Key 校验、模型发现和统一 partial 解析结果
- 套餐、按量、额度窗口、价格、问题报告统一模型及脱敏 JSON 快照存储边界
- 无凭证解析器 registry 及显式、Provider+origin、OpenAI 兼容、人工兜底选择顺序
- PostgreSQL parser run、套餐、额度窗口、按量价格和 billing route 规范化仓储
- 先提交数据库再导出 JSON、记录失败并支持重试的 parser worker 核心
- 追加式人工覆盖事件仓储、稳定字段定位，以及 raw/effective 确定性合成和撤销
- parser worker 在数据库提交后加载人工覆盖，并将脱敏合成失败随结果返回
- 按渠道查询 parser run 历史和最新 raw/effective 数据的应用服务、Account Pool API 与 LiteLLM 服务端代理
- 绑定管理员、请求 ID 和授权动作的短时 actor 信封，以及人工覆盖设置和撤销 API
- 从 PostgreSQL 最新有效数据生成以渠道 ID 为键的脱敏快照预览和下载 API
- 一次性 Key 同实例解析任务、持久任务心跳、超时中断标记，以及启动和状态查询 API
- 原生 HTML/JS 调度控制台
- LiteLLM 到 Account Pool 的服务端管理代理基础

当前缺失或需要替换：

- PostgreSQL 权威数据层
- OpenAI 官方解析器，以及 parser worker 的公开元数据任务和后台导出重试循环
- 受控 JSON 导入和字段差异 UI
- 混合健康检测和半开恢复
- 按 scope 的额度窗口与 restriction
- Retry-After 和结构化 429 分类
- 余额耗尽策略
- 完整调度解释和最低有效成本策略
- 持久化事件、审计和日志 UI
- LiteLLM Dashboard 内的正式 Account Pool 页面
- Redis 恢复和 PostgreSQL 同步

现有计划中曾描述“Phase 1 有文件日志”“429 优先使用 Retry-After”“stats 包含完整额度与健康统计”，实际实现尚未满足。这些能力以本文后续阶段和验收标准为准

## 17. 分阶段实施路线

每个阶段开始前创建独立实施计划，明确迁移、测试、回滚和提交边界

### Phase 1：持久化数据底座与统一渠道管理

目标：建立 PostgreSQL 权威数据源，并让 LiteLLM Dashboard 成为统一渠道管理入口

交付：

- PostgreSQL schema、repository 和迁移
- YAML 到 PostgreSQL 的一次性受控导入
- Deployment 绑定 ownership、operation_id 与 desired/applied 同步
- 创建、编辑、导入、解绑和删除语义
- LiteLLM Dashboard 渠道页
- 复用 Provider、模型下拉和标准组件
- Account Pool 服务端代理端点补齐
- Redis 配置缓存从 PostgreSQL 构建
- 审计渠道管理操作

验收：

- 在 Account Pool 页面完成渠道全生命周期，不进入 LiteLLM Models 页面操作
- Key 不进入 Account Pool 持久化或浏览器存储
- 跨服务失败保留可重试状态；reconciler 能识别并修复或报告未绑定的号池标记 Deployment
- 外部导入的 Deployment 默认不会被误删
- 多实例读取相同渠道权威状态

### Phase 2：解析器框架、套餐、按量与 JSON 快照

目标：建立可扩展的解析器契约，并交付专用、通用和人工兜底完整链路

交付：

- `PARSER_TEMPLATE.md`
- 解析器 manifest、registry 和选择优先级
- Pydantic 套餐、额度窗口、按量分组、价格和可执行计费路由模型
- GLM 解析器迁移到新契约
- OpenAI 官方解析器
- OpenAI 兼容通用解析器
- parser run worker
- unresolved field 和问题报告
- 人工覆盖层
- JSON latest、history、预览、导入和导出
- 解析任务与字段差异 UI

验收：

- 同一渠道能独立表示套餐数据、按量数据或两者兼有
- 只有存在独立可控上游机制时才生成 `billing_route`，否则明确标记由厂商决定扣费
- 未知字段保持 null 并进入问题报告
- 分组倍率后的价格可追溯到原价和倍率
- 重新解析不覆盖人工修正
- JSON 以 channel_id 为键且不含 URL 和 Key
- 新解析器必须通过统一契约测试才能注册

### Phase 3：健康、额度窗口与冷却

目标：建立可解释、可恢复且按渠道、模型和分组区分的资格系统

交付：

- 被动健康事件标准化
- 新渠道、空闲渠道、半开和手动主动探测
- 401、403、404、429、5xx、超时和余额不足分类
- 5 小时、周、月和自定义窗口
- Retry-After、reset_at 和指数退避
- restriction 状态机
- 半开探测令牌
- usage 增量与厂商快照校准
- PostgreSQL 事件、Redis 生效状态和按 9.6 规则执行的重启恢复
- 健康与冷却详情 UI

验收：

- 不健康候选不进入调度器
- 并发满只跳过本次 acquire，不污染健康状态
- 周或月额度耗尽只阻止对应 scope 到 reset_at
- 余额耗尽只排除受影响的 Deployment 绑定或经验证的按量 `billing_route`，不假定无法控制的计费模式可切换
- 未知 429 不被误判为长期额度耗尽
- 重启后活动冷却和额度窗口能够重建；Redis 数据集丢失时必须隔离到故障前租约确定过期后再开放 acquire

### Phase 4：正式调度表与策略

目标：按模型提供稳定、原子和可人工调整的候选排序与回退

交付：

- 统一资格投影
- `manual_priority`
- `lowest_effective_cost`
- `least_inflight`
- `weighted_round_robin`
- `quota_balanced`
- 人工顺序、权重和模型绑定暂停
- 模型调度表与排序原因
- 原子候选竞争和下一渠道回退
- 套餐与按量数据并列展示，可执行计费路由与厂商决定扣费明确区分
- 结构化无可用渠道错误

验收：

- 每个模型可查看排序后的完整渠道详情
- 人工调整排序后立即生成新候选版本
- 人工排序不绕过硬限制
- 第一候选并发满时原子尝试下一候选
- 最低成本策略使用倍率修正后的价格，并区分可执行计费路由与估算证据
- 流式已开始后不会进行可能重复计费的自动重放

### Phase 5：总览、日志与生产化

目标：补齐运营视图、问题追踪和生产运行保障

交付：

- 聚合总览和详情页
- 结构化事件与审计日志页
- Prometheus 指标与告警
- parser worker、health worker、snapshot exporter 和 lease reaper 运行监控
- 数据保留与归档策略
- PostgreSQL 备份恢复演练
- Redis 丢失后的代次切换、最长租约隔离和 fail-closed 恢复演练
- 多 worker、流式中断和故障注入验证
- 新页面稳定运行一个发布周期后删除旧独立 UI 和 `/api/accounts` 兼容端点

验收：

- 总览能准确区分启用与可调度
- 每个不可用渠道都有机器原因码和人类可读说明
- 可从请求或渠道跳转到相关事件
- 日志、指标、导出和错误响应均不泄露 Key
- PostgreSQL 或 Redis 短暂故障不会静默绕过限制

## 18. 测试策略

### 18.1 单元与契约测试

- 每个解析器使用脱敏 fixture 验证完整、部分、无能力和异常响应
- 套餐窗口验证 rolling、fixed、reset_at 和未知语义
- 按量价格验证币种、单位、缓存价格、倍率和 Decimal 精度
- 覆盖层验证设置、重新解析和撤销
- 错误分类验证作用范围和 reason_code
- 策略验证缺失价格、同价、额度低和稳定排序

### 18.2 数据与并发测试

- PostgreSQL migration 升级和回滚
- YAML 导入幂等
- JSON 原子替换和 schema 版本验证
- Redis Lua 多请求竞争
- 同一渠道跨模型共享并发
- acquire、settle 和 release 幂等
- lease 超时与 heartbeat
- Redis 重启进入新代次并 fail-closed 至最大绝对租约时长，旧代次回调不能污染新计数

### 18.3 集成测试

- Account Pool 创建渠道并在 LiteLLM 创建 Deployment
- 更新和轮换 Key 不回显旧值或新值
- detach 不删除外部 Deployment
- 渠道删除永远只解绑外部 Deployment，外部 Deployment 只有通过单绑定独立确认操作才能删除
- managed delete 删除正确 Deployment
- 解析器经受控 HTTP 服务运行
- 一次性 Key 解析在实例中断后变为 `interrupted_requires_key`，任务记录和队列均不包含 Key
- 健康状态变化独立写入健康事实，并通过资格投影加入或移出候选；不得隐式创建同义 restriction
- 资格变化刷新模型候选版本
- LiteLLM 代理端点执行管理员 RBAC 和 CSRF 校验，不把内部令牌发送给浏览器
- Account Pool 拒绝缺失、过期、错误受众或 request_id 不匹配的 actor 信封，审计记录保留实际操作者

### 18.4 UI 验证

- 在 LiteLLM Admin UI 创建、编辑、导入、解绑和删除渠道
- Provider 和模型下拉与 manifest 能力一致
- 预览自动值与人工覆盖差异
- 查看 JSON 和 unresolved field
- 调整模型策略、权重和顺序
- 查看健康、冷却和日志筛选

### 18.5 真实链路验证

最终验收使用真实 LiteLLM Proxy 和受控的真实 Provider API：

- 正常非流式请求
- 正常流式请求
- 客户端中断
- 并发满后的下一渠道回退
- 429 与 Retry-After
- 额度耗尽与 reset_at 恢复
- 凭证无效与轮换恢复

测试命令本身不是 UI 修复的截图证明。UI 变更提供实际访问路径、点击步骤和预期结果，由人工完成浏览器验证和截图

## 19. 安全与运行约束

- 真实 Key 只由 LiteLLM 加密持久化
- Account Pool API 和内部接口必须鉴权；LiteLLM 代理路由必须先执行管理员 RBAC 与 CSRF 校验
- LiteLLM 与 Account Pool 的服务令牌只存在于服务端，签名 actor 信封必须校验受众、过期时间和请求绑定
- Provider URL 必须经过标准化和 SSRF 防护
- 解析请求禁止跨 origin 重定向
- URL、日志和错误体必须脱敏
- JSON 导入必须做 schema 验证，不能执行任意内容
- 人工覆盖、删除、轮换 Key 和策略修改必须审计
- 调度状态不可因依赖服务故障而默认放行
- 多实例部署必须使用 PostgreSQL 与 Redis，不使用进程内存作为生产权威状态

## 20. 明确不做

本总体计划不包含：

- 从 LiteLLM 读取或导出明文 Key
- 让浏览器直接持有 Account Pool 内部令牌
- 携带 Key 遍历未知厂商 URL 试探解析器
- 为未知套餐或价格编造默认值
- 把所有解析响应原样存入日志
- 让人工排序默认绕过健康、额度和并发硬限制
- 在请求已经产生输出后盲目重放
- 一次提交完成所有阶段

## 21. 总体验收标准

系统完成后必须满足：

- 管理员只在 LiteLLM Admin UI 的 Account Pool 页面完成日常渠道管理
- Account Pool 后端可靠同步 LiteLLM Deployment，并能恢复部分失败
- 总览覆盖渠道配置、解析、套餐、按量、健康、冷却、并发和调度资格
- 套餐支持模型、5 小时、周、月、余额和并发等可空字段
- 按量支持分组、模型、输入、输出、缓存、倍率和有效价格
- 无法解析的数据有明确报告和人工覆盖入口
- JSON 快照以 channel_id 为键，不重复保存 URL 和 Key
- 不健康、冷却、额度耗尽和并发满候选不会被错误选中
- 并发满自动尝试下一渠道，不创建错误的长期冷却
- 周、月和余额耗尽具有明确恢复策略和可见原因
- 每个模型可查看并人工调整候选排序
- 调度选择使用原子 acquire，竞争时不超过共享并发上限
- 日志能追踪问题且不泄露凭证或用户敏感内容
- PostgreSQL、Redis、Account Pool 或 LiteLLM 出现故障时不会静默绕过资源限制
