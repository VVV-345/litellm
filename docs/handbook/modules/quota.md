# 额度、冷却、兑换与恢复验证

## 职责与边界

额度解析和供应商差异留在 provider 适配层，生命周期与恢复编排留在服务层。额度观测、账户/模型冷却、重置兑换、验证及真实调用证据分别保存，不能互相替代

## 关键入口

[供应商解析](../../../account-pool/account_pool/providers/usage/)、[通用额度判断](../../../account-pool/account_pool/application/quota_state.py)、[额度调度](../../../account-pool/account_pool/quota_scheduler.py)、[自然恢复调度](../../../account-pool/account_pool/quota_recovery_scheduler.py)、[QuotaResetService](../../../account-pool/account_pool/quota_reset.py)、[重置 API](../../../account-pool/account_pool/quota_reset_api.py)、[操作记录](../../../account-pool/account_pool/quota_reset_repository.py)

## 输入输出与流程

上游观测包含来源、观测时间、窗口和每模型额度；解析后供页面及路由使用。未知额度不能当成 0，也不能当成已恢复；新鲜观测不能被旧缓存覆盖

兑换先 prepare 固定 request_id，显式 consume 后保存结果；超时或 uncertain 时查询原操作，不能创建新身份重试。验证使用指定模型，是否恢复路由与验证是否成功分别返回；独立模型冷却和其他阻断不能被一张卡的成功验证无条件清掉

## 配置与最小使用

管理 API 的单卡入口为 `GET /api/environments/{environment_id}/quota-reset`，只读请求但可能读取上游额度状态；`/prepare`、`/consume`、`/verify` 为 POST。consume 可能消耗重置权益，verify 可能产生真实模型请求及费用，必须明确授权

最小使用先通过页面查询当前观测和已有 request_id，再决定是否兑换或只验证。普通模型调用的完成证据独立展示，不等同于一次 reset 验证。文档检查不会请求这些接口

## 依赖与改动影响

依赖供应商 API、每卡唯一凭据、数据库操作锁和网关结算。修改窗口单位、时间戳、错误分类、恢复范围或 model_quotas 合并时，应同时验证解析、UI、准入和持久化，不能只改显示百分比

## 风险与禁止事项

不自动兑换，不因暂时网络超时把运行时判成必须升级，不把一次模型成功变成整卡解锁。管理/能力接口可用、模型列表、额度有余量都不是完整推理成功证据；no_credit、uncertain 与未运行验证不能写成已恢复

## 测试与通过标准

[解析及窗口](../../../account-pool/tests/account_pool/test_quota.py)、[兑换与恢复](../../../account-pool/tests/account_pool/test_quota_reset.py)、[API](../../../account-pool/tests/account_pool/test_quota_reset_api.py)、[自然恢复](../../../account-pool/tests/account_pool/test_quota_recovery_scheduler.py)、[PostgreSQL](../../../account-pool/tests/account_pool/test_quota_reset_postgres.py)。要求原兑换身份可续查，结果可追溯，并发状态不被覆盖，模型范围不扩大

## 故障排查

额度恢复后仍被拒绝，依次检查观测新鲜度、每模型数据、独立运行时冷却、配置 pending、卡片启停和租约。验证完成但未恢复时读取返回原因，不把 state_changed 等拒绝当成功。网络故障与能力 404 分别处理

## 恢复与回滚

重启后读取已持久化的操作与验证记录，继续首个未完成步骤。镜像回退不回滚已经消耗的上游权益，也不恢复旧 Token。数据库恢复需单独确认，边界见[数据与备份](../data-and-backups.md)
