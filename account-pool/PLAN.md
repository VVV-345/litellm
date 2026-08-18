# LiteLLM 号池系统实施计划

## 1. 建设目标

在 LiteLLM Proxy 旁增加一个独立的号池系统，为每个模型维护一组可用上游账号，并负责账号选择、并发占用、归还、额度统计、失败冷却和资源展示

LiteLLM 保留现有职责：

- 对外提供兼容 API
- API Key 鉴权、团队和用户管理
- provider 协议转换
- deployment 管理
- 请求日志与费用统计

account-pool 增加以下职责：

- 按模型维护账号池
- 根据策略选择具体 deployment
- 原子管理最大并发和租约
- 统计总额度、5 小时额度和周额度
- 根据请求结果更新健康状态和冷却状态
- 提供管理 API，并通过 LiteLLM Admin UI 管理

这些能力合在一起就是号池的调度内核，不再额外建设一个独立的“调度器”产品

## 2. 当前阶段的关键决策

### 2.1 Phase 0 结论：`gateway-required`

LiteLLM 的配置只接受内置 `routing_strategy` 名称，不能配置任意 Python 类路径

```yaml
router_settings:
  routing_strategy: usage-based-routing
```

自定义策略只能通过代码注册：

```python
router.set_custom_routing_strategy(AccountPoolRoutingStrategy(...))
```

代码核查发现 LiteLLM 提供 `LITELLM_WORKER_STARTUP_HOOKS`，但该钩子在 `proxy_startup_event` 创建 `llm_router` 之前执行。此时没有可稳定注册策略的 Router 实例，也没有找到 Router 创建完成后、开始接收请求前的公开钩子

因此插件策略路线停止，Phase 0 输出为 `gateway-required`。当前实现采用号池网关：客户端先访问 account-pool，由 account-pool 原子选号并把公共模型名改写为 LiteLLM deployment ID，再转发给内网 LiteLLM Proxy

### 2.2 Phase 1 暂不上 PostgreSQL

Phase 1 的目标只是跑通 `acquire -> 上游请求 -> settle/release` 闭环。此时引入 PostgreSQL 会提前增加 migration、repository 和部署成本

Phase 1 采用：

- YAML 保存账号与 deployment 映射
- Redis 保存并发、租约、冷却、简单额度和健康状态
- 文件日志记录管理与调度事件
- 保留正式数据库 schema 设计，但不创建数据库代码和 migration

Phase 1 属于功能验证版本，不作为生产持久化方案。Redis 数据丢失或重启后，额度可能不准确；测试环境应启用 AOF，并允许从配置重新初始化状态

Phase 2 再引入 PostgreSQL，迁移配置、策略、额度事件和审计记录

### 2.3 Phase 1 只做被动健康检测

API Key 由 LiteLLM deployment 持有，不写入 account-pool 的 Redis、配置文件、日志或接口响应

由于 account-pool 没有上游 Key，Phase 1 不主动向供应商发送探测请求。健康状态完全由 LiteLLM callback 上报：

- 请求成功：清除或降低连续失败数，更新延迟
- 请求超时、429 或 5xx：增加连续失败数并按规则冷却
- 401 或确定的鉴权失败：禁用账号并等待管理员处理
- 冷却到期：账号重新进入候选池

Phase 2 如需主动检测，优先通过 LiteLLM 定向调用指定 deployment，或者从独立密钥库获取凭证。不要设计从 LiteLLM 管理接口读取明文 Key 的流程

## 3. 总体架构

### 3.1 已采用架构：号池网关

```text
客户端
  |
  v
account-pool gateway
  |- Redis
  |- accounts.yaml
  |- 调度内核
  |- 被动健康检测
  |- 管理 API
  |
  | 将 model 改写为 LiteLLM deployment ID
  v
内网 LiteLLM Proxy
  v
上游供应商
```

LiteLLM Proxy 必须只允许 account-pool 和运维网络访问。若客户端能绕过网关直接调用 LiteLLM，号池的并发和额度限制就无法成立

### 3.2 已停止架构：Router 插件注入

callback dotted path 可以正常作为 LiteLLM 扩展加载，但 callback 只能上报请求结果，不能在 Router 创建前完成账号选择。除非 LiteLLM 后续提供稳定的 Router-ready 钩子，否则不恢复此路线

## 4. 核心资源模型

### 4.1 Account

`Account` 表示一个 URL + Key 对应的上游资源主体。Key 仍由 LiteLLM 保存，account-pool 只保存 LiteLLM deployment ID 与资源限制

```yaml
accounts:
  - id: account-openai-01
    provider: openai
    base_url_display: https://api.openai.com
    enabled: true
    max_concurrency: 5
    priority: 100
    weight: 1
    deployments:
      - public_model: gpt-4o
        litellm_model_id: deployment-id-1
      - public_model: gpt-4o-mini
        litellm_model_id: deployment-id-2
    quotas:
      total: 1000
      five_hour: 100
      weekly: 500
```

并发和额度属于 Account，而不是单个模型。这样同一账号被多个模型使用时，仍然共享同一个并发上限和额度

### 4.2 Deployment

`Deployment` 是 Account 对某个模型的 LiteLLM 映射，包含：

- 对外模型名
- LiteLLM deployment ID
- provider 实际模型名，由 LiteLLM 管理
- 启用状态

### 4.3 Route View

每个模型可以查看当前路由表：

- Account 和 deployment ID
- URL 摘要，不含敏感查询参数
- 最大并发、当前并发、可用并发
- 总额度、5 小时额度、周额度
- 健康、冷却、禁用状态
- 优先级、权重和调度分数
- 不可用原因

路由表只是可解释视图。实际选号必须由原子 `acquire` 完成，不能先读路由表第一名再单独占用并发

## 5. Redis 数据设计

Phase 1 使用以下运行时键：

| Key | 内容 |
| --- | --- |
| `pool:account:{id}:state` | enabled、health、cooldown_until、连续失败数 |
| `pool:account:{id}:inflight` | 当前并发数 |
| `pool:lease:{lease_id}` | account_id、deployment_id、request_id、过期时间、结算状态 |
| `pool:request:{request_id}` | 对应 lease_id，用于幂等 acquire |
| `pool:account:{id}:quota` | Phase 1 简单额度快照 |
| `pool:model:{model}:policy` | 当前策略和参数 |

Redis Lua 脚本负责：

- 检查账号是否启用、健康、未冷却
- 检查并发是否小于上限
- 检查简单额度是否大于安全阈值
- 原子递增并发并创建 lease
- 幂等释放 lease 和递减并发

租约带 TTL。长时间流式请求通过 heartbeat 续租；Proxy 崩溃或 callback 未执行时，由 lease reaper 回收过期租约

## 6. 请求调度流程

### 6.1 Acquire

`POST /internal/acquire`

输入：

```json
{
  "request_id": "req-123",
  "model": "gpt-4o",
  "estimated_tokens": 2000
}
```

流程：

1. 读取模型绑定的候选 Account
2. 排除 disabled、cooldown、额度不足和并发已满的账号
3. 根据策略得到候选顺序
4. 使用 Lua 原子尝试占用候选账号
5. 成功时创建 lease 并返回 deployment
6. 候选竞争失败时继续尝试下一账号
7. 全部不可用时返回明确的容量不足错误

输出：

```json
{
  "lease_id": "lease-456",
  "account_id": "account-openai-01",
  "litellm_model_id": "deployment-id-1"
}
```

相同 `request_id` 重试 acquire 时必须返回原 lease，不能重复增加并发

### 6.2 Settle 与 Release

`POST /internal/settle` 上报：

- lease_id 和 request_id
- 成功或失败
- 状态码和错误类别
- 输入、输出 Token
- 实际费用或供应商额度头
- 延迟和流式结束状态

`POST /internal/release` 幂等释放并发。正常成功、失败、客户端断开、超时和流式结束都必须释放

Phase 1 的额度处理只做简单减法，并明确标记为估算值。请求失败也可能产生费用，优先使用 LiteLLM 回调提供的实际 usage；没有 usage 时不伪造精确额度

## 7. Phase 1 路由策略

先实现最小策略集合：

- `priority`：优先级主备
- `least_inflight`：最少当前并发
- `weighted_round_robin`：加权轮询
- `quota_aware_least_inflight`：过滤额度不足后选择最少并发，作为推荐默认策略

价格、延迟和成功率组合评分推迟到 Phase 2。届时必须定义统计窗口、最小样本数和缺失数据行为

## 8. 被动健康与冷却

Phase 1 状态：

```text
unknown -> healthy -> degraded -> cooldown -> healthy
                         |
                         v
                      disabled
```

建议默认规则：

- 成功请求将连续失败数清零
- 429 立即冷却，并优先使用上游 `retry-after`
- 5xx 或超时连续达到阈值后冷却
- 401 直接禁用，不自动删除 deployment
- 冷却到期后允许少量试探请求，成功后恢复 healthy

健康失败只改变状态，不调用 `/model/delete`。只有管理员明确删除账号时才删除 LiteLLM deployment

## 9. LiteLLM 加载方式

### 9.1 Callback 加载

LiteLLM 已支持在配置中按 Python dotted path 加载 `CustomLogger` 实例。`state_tracker.py` 必须导出实例，而不是只导出类：

```python
proxy_handler_instance = AccountPoolStateTracker(...)
```

`proxy_config.yaml` 示例：

```yaml
litellm_settings:
  callbacks:
    - account_pool.litellm_plugin.state_tracker.proxy_handler_instance

router_settings:
  enable_pre_call_checks: true
```

这里不配置自定义 `routing_strategy`。callback 包通过以下任一方式进入 Proxy 的 Python 环境：

- 构建 LiteLLM 镜像时 `pip install` account-pool
- 本地 POC 使用 `pip install -e ./account-pool`
- 容器挂载代码并设置受控的 `PYTHONPATH`

生产环境推荐构建固定版本镜像，不使用运行时可变的代码挂载

### 9.2 Routing Strategy 加载结论

`router_settings.routing_strategy` 只能填写内置策略名，不能填写 Python 类路径。`LITELLM_WORKER_STARTUP_HOOKS` 又早于 Router 创建，因此当前版本不加载自定义 Routing Strategy

账号选择在 account-pool 网关完成。网关把 `model` 改写为选中的 `litellm_model_id`，LiteLLM 继续负责密钥、协议转换和真实上游调用

### 9.3 Callback 的定位

`AccountPoolStateTracker` 是可选的被动事件上报组件。网关自身已经在普通响应、失败响应、流式结束和流式中断时执行 `settle/release`；callback 重复上报时，Redis 和内存实现都按 lease 幂等处理

## 10. Phase 1 API

### 内部 API

| 方法与路径 | 用途 |
| --- | --- |
| `POST /internal/acquire` | 选账号、占并发、创建 lease |
| `POST /internal/settle` | 上报结果、usage、延迟和健康事件 |
| `POST /internal/release` | 幂等归还并发 |
| `POST /internal/heartbeat` | 长流式请求续租 |

### 最小管理 API

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/accounts` | 查看配置和运行状态 |
| `POST /api/accounts` | 新建渠道并在 LiteLLM 创建 deployment |
| `PUT /api/accounts/{id}` | 修改渠道并同步 LiteLLM deployment |
| `DELETE /api/accounts/{id}` | 删除渠道并清理由号池创建的 deployment |
| `GET /api/models` | 模型号池概览 |
| `GET /api/models/{model}/routing-table` | 查看脱敏路由表 |
| `PUT /api/models/{model}/policy` | 修改并持久化模型策略 |
| `GET /api/litellm/status` | 检查 LiteLLM 连接和管理认证 |
| `GET /api/stats` | 并发、额度与健康统计 |

渠道管理 API 支持增删改，当前 LiteLLM Admin UI 支持新增、查看和删除。API Key 只发送给 LiteLLM 管理接口，号池 YAML 仅保存 deployment ID 和非敏感调度配置。绑定已有 deployment 时不会取得或回显原 Key

## 11. 项目目录

```text
account-pool/
├── Dockerfile
├── PLAN.md
├── README.md
├── pyproject.toml
├── config/
│   ├── accounts.demo.yaml
│   └── proxy_config.example.yaml
├── account_pool/
│   ├── app.py
│   ├── config.py
│   ├── gateway.py
│   ├── models.py
│   ├── scheduler.py
│   ├── store.py
│   ├── domain/provider_source.py
│   ├── provider_services/
│   │   ├── contracts.py
│   │   ├── registry.py
│   │   └── glm/
│   │       ├── manifest.py
│   │       ├── schemas.py
│   │       ├── client.py
│   │       └── service.py
│   └── litellm_plugin/state_tracker.py
└── tests/
    ├── test_api.py
    └── test_scheduler.py
```

Phase 1 未创建号池自己的 PostgreSQL repository、migration 和主动健康 worker。LiteLLM 可以继续使用自身 PostgreSQL 加密保存 provider Key；渠道管理统一复用 LiteLLM Admin UI

供应商获取服务按目录解耦。公共层只依赖 `ProviderService` 协议和统一结果，不在管理代码里写 GLM、火山等名称分支。当前 GLM 官方国内模块支持 URL、Key 和模型发现；余额、套餐、周期限额及账户实际价格由于没有稳定公开管理 API，明确返回不支持

## 12. 实施阶段

### Phase 0：接入 POC，已完成

仅实现固定候选的自定义策略、最小 callback 和一个假的 account-pool 接口。验证 LiteLLM 启动注入、请求关联、流式、失败和多 worker

输出结论：`gateway-required`

### Phase 1：Redis MVP，已完成可演示版本

实现：

- accounts.yaml 加载与校验
- Redis Lua 原子 acquire/release
- lease TTL、heartbeat 和回收
- 四个基础策略
- callback settle/release
- 被动健康、失败冷却
- 简单额度快照
- 路由表和统计 API
- OpenAI 兼容网关与 deployment ID 改写
- 内存状态存储模式
- 模型号池概览、路由表和并发模拟 UI
- LiteLLM 管理接口认证与连接状态
- 渠道增删改查和 deployment 同步
- 路由策略 API 与 YAML 持久化

当前调度闭环、渠道 CRUD API 和策略 API 已经可运行。渠道编辑与策略界面、号池自己的 PostgreSQL 额度账本和主动健康检测仍按计划留在后续阶段

### Phase 2：持久化与完整管理

引入 PostgreSQL：

- `accounts` 与 `deployments`
- `model_policies`
- `quota_policies` 与 `quota_events`
- `health_events`
- `audit_events`
- `sync_state`

Phase 2 将 accounts.yaml 迁移到数据库，将 Redis 简单额度升级为事件账本与快照，并实现 LiteLLM `/model/new`、`/model/delete` 的 desired/applied 同步和 reconciler

同时增加：

- 渠道 desired/applied 状态和后台 reconciler
- 主动健康检测
- 供应商额度头解析
- 固定窗口、滑动窗口和 reset_at
- Prometheus 指标和告警

### Phase 3：管理 UI

实现：

- 模型号池概览
- 点击模型查看路由表
- 渠道管理
- 策略选择
- 手动检测与恢复
- 并发、额度、冷却和失败趋势

UI 永远不展示完整 Key，URL 中的敏感路径和查询参数也必须脱敏

## 13. Phase 1 验收标准

- 网关路线已经明确，不保留未验证的双重实现
- 相同 request_id 重试不会重复占用并发
- 多请求竞争时账号并发不会超过 max_concurrency
- 同一 Account 下的多个模型共享并发和额度
- release 和 settle 重复调用不会重复释放或重复扣额度
- 成功、失败、超时、流式中断和进程异常后租约最终可回收
- 429、5xx、超时和 401 按规则更新状态，不自动删除 deployment
- account-pool、Redis 或插件不可用时不会绕过资源限制
- 路由表、日志和接口响应不包含明文 API Key
- 使用真实 LiteLLM Proxy 完成正常请求、失败请求和流式请求验证

## 14. Phase 1 已知限制

- Redis 是临时状态来源，重启后简单额度可能不准确
- 暂无持久化审计和完整额度账本
- 暂无主动健康探测
- 渠道和策略仍保存于 YAML，尚无号池数据库审计记录
- 暂无跨供应商统一的精确 5 小时和周额度

这些限制必须在部署说明中明确标记。Phase 1 只用于验证和小规模受控运行，生产化以 Phase 2 完成为准
