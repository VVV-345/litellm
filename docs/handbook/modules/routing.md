# 请求分配、租约、排空与结算

## 职责与边界

LiteLLM 保留用户鉴权、原生计价和日志集成，号池网关限制卡片/模型范围并取得 Manager 租约。推理正文由网关转发到 CPA 或兼容上游，不经过 Manager；控制面健康不等于数据面正常

OpenAI 的 `gpt-6-astra` 请求在 Chat、Responses 和 Messages 入口统一校验 `reasoning_effort`、`thinking` 与原生 `reasoning`，冲突或非法值返回 400，不全局丢弃参数。Chat 只传递推理强度，摘要请求需使用 Responses；Messages 的摘要显示意图由 Responses 转换保留。这里解决入口适配，不保证上游账号或模型支持全部推理档位

## 关键入口

[GatewayService](../../../account-pool/account_pool/gateway_service.py)、[租约仓库及 lock_version](../../../account-pool/account_pool/gateway_repository.py)、[控制协议](../../../account-pool/account_pool/gateway_contracts.py)、[LiteLLM 网关](../../../litellm/proxy/management_endpoints/account_pool_gateway.py)、[转发](../../../litellm/proxy/management_endpoints/account_pool_gateway_forwarder.py)、[重试](../../../litellm/proxy/management_endpoints/account_pool_retry.py)、[流式处理](../../../litellm/proxy/management_endpoints/account_pool_stream.py)

## 输入输出与流程

卡片 Key 或受信任的卡片/Key 绑定进入解析，得到候选与策略快照；准入再次核对环境和策略版本、Key、模型冷却、并发与 token 预算，然后签发租约。转发结束后结算，记录请求结果及满足条件的调用证据

会话粘性不允许越权跨卡，模型级和整卡故障要分开处理。流式已输出或可能重复执行工具时，不能像未发送的普通请求一样重放

Responses 收到明确的旧响应 ID 失效错误时，只有完整、可重放历史可用且本轮尚未输出，才去掉旧 ID 在同一部署重试一次。已完成、可读的原生网页搜索记录可随完整历史转给同模型的 Codex CPA 备卡，缺搜索声明时按请求结构补齐；未完成搜索、外站引用和其他不支持的服务端工具不放行。不为 `store:false` 写入持久历史

同卡重建仍遇额度不足时，按原请求的候选范围、排除名单与回退开关尝试备卡，不携带同卡重试临时排除的备卡列表。所有尝试共享原有次数和截止时间；再次返回旧 ID 失效不循环重试。文字、推理摘要或工具参数已经输出后发生异常，直接结束并报错，不换卡重新吐字。换卡成功后保存新响应的归属，下一轮继续绑定新卡

每次续聊请求最多跨卡切换 5 次；按请求的 `max_fallbacks`（未指定时取路由默认值）与 5 的较小值执行，关闭回退仍有效。同卡旧 ID 重建只消耗总尝试次数，不消耗换卡次数；请求发出前因原卡额度不足而提前迁移也计入换卡次数。第 5 次切换后仍失败就返回错误，不再尝试第 6 张备卡；总尝试次数或截止时间先耗尽时提前停止

## 配置与最小使用

先在已授权的测试卡核对启用模型与并发，再用该卡作用域的 Key 测试正常网关入口。Manager token 只用于内部管理，不能当成推理 Key

真实请求会消耗额度并可能产生费用，必须另有授权。最小检查要记录卡片、模型、请求 ID、完成状态和结算结果，不能只看 HTTP 200。文档任务只引用[现有网关测试](../../../account-pool/tests/account_pool/test_gateway_service.py)，不发送上游请求

## 依赖与改动影响

路由依赖生命周期、策略、额度、凭据归属和数据库租约。改变准入理由、快照字段或重试边界时，须同步 LiteLLM 协议与结算调用方，保留用户限制、计价与原请求归属

## 风险与禁止事项

维护门禁在环境行锁内持久化，`phase != verified` 时拒绝新准入。安装门禁后先释放环境行锁再等在途租约排空；结算仍需该行锁，持锁等待会形成阻塞。排空前不得停止 CPA 或禁用其在途凭据，也不得通过永久降低并发掩盖升级问题

## 测试与通过标准

[网关行为](../../../account-pool/tests/account_pool/test_gateway_service.py)、[真实 PostgreSQL 排空](../../../account-pool/tests/account_pool/test_runtime_rollout_postgres.py)及 [LiteLLM 集成测试目录](../../../tests/test_litellm/proxy/management_endpoints/)。通过标准包括：旧快照/撤销 Key 被拒绝，新门禁拒绝新租约，原租约正常结算，恢复不越过原卡片和模型范围

## 故障排查

configuration 拒绝先查版本、待同步配置、Key 与维护门禁；concurrency 查有效租约；cooldown 查具体模型及运行时冷却。draining 停住时按请求 ID 查超时与结算，不先杀容器，也不直接删除租约行伪造“排空”

## 恢复与回滚

进程重启不自动解除持久门禁；只在固定目标的运行时核验成功后放行。切换失败保留新 Manager 以识别门禁，按[同版本续跑](../deployment.md)恢复，不能用旧 Manager 忽略门禁来恢复流量
