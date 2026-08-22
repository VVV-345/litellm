<!-- 本文件说明 Account Pool Redis 数据丢失、代次隔离和多 Worker 恢复演练流程。 -->

# Redis 恢复演练手册

本演练验证 Redis 数据集丢失后不会把并发或额度当成零，并验证多个 Account Pool Worker 只加入同一个恢复代次。
必须使用隔离的演练 Redis 数据库和演练 PostgreSQL 数据库，禁止对生产 Redis 执行清空命令

## 前置条件

- 已执行全部 Prisma migration，包括 `20260822070000_add_account_pool_quota_recovery_isolation`
- 两个 Account Pool 进程使用相同的 `DATABASE_URL`、`ACCOUNT_POOL_REDIS_URL` 和非敏感渠道配置
- `ACCOUNT_POOL_STORE=redis`
- `ACCOUNT_POOL_LEASE_TTL_SECONDS=30`
- `ACCOUNT_POOL_MAXIMUM_LEASE_SECONDS=120`
- 演练模型至少有一个额度窗口，便于确认预占和恢复下界

建议分别在 `4101`、`4102` 启动两个 Worker，并保留各自日志。不要把管理令牌、Key、Authorization 或上游响应正文
写入演练记录

## 基线

1. 通过任一 Worker 调用 `/internal/acquire`，保留返回的 `lease_id`、`generation_id` 和 `absolute_expires_at`
2. 在 PostgreSQL 查询当前 `active` 代次及其 `isolation_until`
3. 在专用 Redis 数据库读取 `pool:quota:generation`，确认与 PostgreSQL active 代次一致
4. 不结算也不释放该租约，模拟故障发生时仍有请求执行

## 数据集丢失

1. 再次确认连接的是专用演练 Redis 数据库，然后清空该数据库
2. 同时重启两个 Account Pool Worker
3. 两个 Worker 启动后立即调用 `/internal/acquire`

预期结果：

- PostgreSQL 只有一个新的 `initializing` 或 `active` 后继代次，两个 Worker 不会各自创建可用代次
- Redis `pool:quota:generation` 与 PostgreSQL 新 active 代次一致
- `isolation_until` 不早于故障前租约的 `absolute_expires_at`
- 隔离期内 acquire 返回 `quota_recovery_isolation`
- 额度窗口按 PostgreSQL 快照和 usage 事件恢复，不以零用量或无限额度启动

## 迟到回调

在隔离期内和隔离结束后，分别使用故障前的 `lease_id` 调用 `/internal/settle`、`/internal/release` 和
`/internal/heartbeat`。三个操作都应返回 `ok=false`，Redis 新代次的 inflight、reserved 和 remaining 不应变化

等待 `isolation_until` 后再次 acquire。新请求应获得新代次租约；正常 settle 和 release 后，inflight 与预占归零，
实际 usage 只累计一次

## 故障注入

分别在 acquire、settle 写前事件和运行快照保存期间暂停 PostgreSQL 或 Redis。请求可以失败或返回结构化不可用，
但不能成功绕过并发、余额、额度窗口或恢复隔离。依赖恢复后重启两个 Worker，并重复检查共享代次和隔离截止时间

## 通过标准

- 同一前置代次最多一个初始化中的后继代次
- 任意 Worker 观察到的 Redis 代次、PostgreSQL active 代次和隔离截止时间一致
- 隔离期不会提前结束，heartbeat 不能越过 `absolute_expires_at`
- 旧代次回调不修改新代次状态
- PostgreSQL 或 Redis 不可用时没有请求被默认放行
