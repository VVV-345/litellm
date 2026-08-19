<!-- 本文件定义 Account Pool Phase 4 正式调度表、排序策略、人工调整和原子回退的实施边界。 -->

# Phase 4：正式调度表与策略实施计划

## 1. 目标

Phase 4 把现有基础调度器升级为按模型维护、可持久化、可解释和可人工调整的正式路由表

请求链路保持为：

```text
公共模型 -> 资格筛选 -> 策略排序 -> 原子 reserve -> 失败时尝试下一候选 -> 返回 deployment_id
```

排序不能绕过管理员禁用、健康排除、冷却、额度窗口、余额耗尽或并发上限等硬限制

## 2. 模块边界

新增 `account_pool/routing/`，内部按以下职责拆分：

- `models.py`：策略、候选、价格证据、排序原因、人工覆盖和路由表版本契约
- `ordering.py`：纯排序逻辑，不访问数据库、Redis 或网络
- `projection.py`：把渠道目录、解析结果、运行指标投影为候选
- `repository.py`：策略与人工覆盖的持久化协议
- `postgres.py`：PostgreSQL 实现和并发版本控制
- `service.py`：查询路由表、更新策略、调整顺序、暂停绑定和刷新运行配置

现有 `scheduler.py` 只负责调用排序模块并逐个执行原子 reserve；`store.py` 继续负责并发、额度和租约原子性

## 3. 策略语义

正式支持：

- `priority`：人工优先级。先使用模型级人工顺序，再使用渠道档位和稳定 ID
- `random`：每次 acquire 使用运行序列生成可重放的伪随机顺序，不使用进程随机状态
- `lowest_latency`：按成功请求的延迟 EWMA 排序；没有样本的候选排在已有样本之后
- `highest_remaining_quota`：按最紧额度窗口的剩余比例从高到低排序
- `lowest_effective_cost`：按解析器给出的倍率修正后标准化价格排序
- `least_inflight`：按当前并发占用比例排序
- `weighted_round_robin`：按模型级权重轮询
- `quota_aware_least_inflight`：兼容现有配置，并发占用相同时优先剩余额度更多的候选

所有策略最后都使用人工优先级和稳定候选 ID 作为确定性兜底

## 4. 人工调整

人工调整按模型和 Deployment 绑定保存，不修改渠道全局身份：

- `manual_order`：显式顺序，可空
- `weight`：模型级权重，可空；为空时继承渠道权重
- `paused`：只暂停该模型绑定

渠道侧优先级继续提供四档：最高、高、中、低。数据库保存数值，UI 只提交固定档位，避免任意数字造成难以解释的顺序

人工设置只能改变合格候选之间的顺序，不能恢复被硬限制排除的候选

## 5. 成本比较

最低成本只使用解析结果中的 `effective_prices` 或 `normalized_per_million_tokens`，不重新计算厂商倍率

价格必须满足以下条件才可直接比较：

- 对应当前 Deployment 绑定和公共模型
- 币种一致
- 单位已经标准化，或解析器明确提供换算说明
- 数据来自最新有效解析结果和人工覆盖合成结果

请求尚无输入、输出 token 比例时，排序分数使用标准化输入价与输出价之和；缺少任一方向时保留部分证据并在路由表中标明。完全缺价的候选排在有可比价格的候选之后，但仍可作为失败回退

只有 `billing_route` 带有独立可执行选择机制时，调度器才能在同一 Deployment 下切换计费路由；厂商自行决定扣费时只展示估算证据，不伪装成可切换路由

## 6. 延迟指标

成功结算的 `latency_ms` 按 Deployment 维护 EWMA、样本数和最后观测时间。失败、主动健康探测和零延迟占位值不更新业务延迟

Redis 保存实时指标，PostgreSQL 保存可恢复快照。没有样本时不得把延迟默认为零

## 7. PostgreSQL 数据

扩展模型策略表并新增模型候选覆盖表：

```text
LiteLLM_AccountPoolModelPolicy
  model, strategy, policy_order, version, created_at, updated_at

LiteLLM_AccountPoolModelCandidateOverride
  model, binding_id, manual_order, weight, paused, created_at, updated_at
```

候选覆盖通过 `binding_id` 关联渠道目录，不保存 URL、Key 或请求内容。策略和覆盖更新使用版本号做乐观并发控制，成功后刷新调度运行配置

## 8. API 与 UI

API 提供：

- 模型列表及策略、候选数量、路由表版本
- 完整路由表，包括资格、排序位置、排序原因、价格证据、额度、并发和延迟
- 更新模型策略
- 更新或清除模型候选人工覆盖

4100 调度器 UI 保留并作为完整调度工作台；LiteLLM Dashboard 复用同一服务端 API，不复制排序逻辑

动态策略的路由表预览不推进轮询或随机序列，页面读取不能改变下一次真实请求的选择结果

## 9. 原子性与失败回退

排序只是候选建议，最终资格由 `StateStore.reserve` 原子确认。第一候选在排序后变为并发满、额度不足或进入冷却时，scheduler 继续 reserve 下一候选

同一 `request_id` 的 acquire 必须返回原租约。流式响应已经向客户端发送内容后，不自动重放到其他渠道

无可用渠道错误返回结构化候选拒绝原因，不返回 Key、内部 Authorization 或上游响应正文

## 10. 提交顺序

1. 候选、策略和排序解释契约，修正 route table 与 acquire 共用排序
2. PostgreSQL 策略和模型候选覆盖迁移、仓储与服务
3. 解析价格与可执行 billing route 投影，最低有效成本策略
4. 延迟指标实时记录与恢复，延迟优先策略
5. 4100 UI 和 LiteLLM Dashboard 管理界面
6. 集成测试、计划状态更新和本地提交

## 11. 验收

- 每个模型可查看资格筛选后的完整候选和排序原因
- 所有策略有确定性契约测试，动态策略预览不改变真实序列
- 人工顺序、权重和暂停持久化后立即生效
- 第一候选并发竞争失败时自动尝试下一候选
- 最低成本只比较可追溯的倍率修正价格，缺价和不可控计费模式有明确说明
- 不健康、冷却、额度耗尽和管理员暂停候选不会因人工排序重新进入 acquire
- API、日志、PostgreSQL、Redis 和浏览器存储不出现真实 Key
