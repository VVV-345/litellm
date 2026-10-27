# sub2api 故障处理移植

2026-10-15，方案 A 保留 LiteLLM、Manager 和 CLIProxyAPI，只移植分类与有限恢复逻辑。没有引入 sub2api 的 Go 服务、数据库、用户体系或计费系统，也没有替换线上容器

## 来源和复用入口

上游固定在 [Wei-Shaw/sub2api 698d142468ab15374bc22bd5247759e5b7352006](https://github.com/Wei-Shaw/sub2api/tree/698d142468ab15374bc22bd5247759e5b7352006)，不跟随浮动 main

| 上游源文件 | 采用的机制 | SHA-256 |
| --- | --- | --- |
| backend/internal/service/openai_account_runtime_block_fastpath.go | 额度与瞬时 429 分流，2 分钟重试窗口，8 秒指数退避上限，请求级错误不污染账号状态 | 878feff6ee184b632763d4bc82599c093bd49147f3dc405cbc7aa29b855d0090 |
| backend/internal/service/openai_gateway_upstream_errors.go | 请求拒绝与账号可用性错误分离 | 4e3884e76971b55e6738716eeee2221fe007a1436dbd7af946ded2540d93516d |
| backend/internal/handler/failover_loop.go | 同账号重试和换号分离，有限次数后停止 | bc9733c415df26c41f618e1afe21653724253e9596ab75f037aea42b27f2c82c |

`litellm/proxy/management_endpoints/account_pool_failover.py` 是可复用的纯分类入口，HTTP 和 SSE 共用 `classify_failure`，Router 和会话续接共用类型化失败标记。Manager 只接收内部 `FinishRequest.failure_reason`，不反向依赖 LiteLLM 包，不创建第二套调度器

请求级容量等待对照 [c591412c42ab195dacdede9eb19f9cbfee221144](https://github.com/Wei-Shaw/sub2api/commit/c591412c42ab195dacdede9eb19f9cbfee221144) 的 `openAIRequestScopedRetryDelay`。采用的字段按序是 Retry-After、Retry-After-Ms、`error.headers.retry-after`、`response.error.headers.retry-after`，以及这两处的 `retry-after-ms`。第一正值向上取整，封顶为已有 `MAX_RETRY_DELAY_SECONDS`（8），写入已有 `FinishRequest.retry_after_seconds`

## 本项目的适配规则

| 情况 | 行为 |
| --- | --- |
| 明确额度耗尽、Codex 窗口用量达到 100%、429 返回重置时间 | 跳过同卡重试，按照已有额度切换开关选下一张授权卡片，保留上游 reset 冷却 |
| 普通 429 | 按原有 `max_attempts` 先重试同卡，等待 Retry-After，不再一律先写 60 秒冷却 |
| 容量降载（`upstream_unavailable`、同卡、冷却为 0） | 同卡重试，等待请求级 Retry-After 或 Retry-After-Ms，向上取整后封顶 8 秒 |
| 连接失败或已确认的临时上游错误 | 在安全边界内重试，同卡预算耗尽后按 `fallback_enabled` 换号 |
| model_not_found、CLIProxyAPI auth_unavailable/auth_not_found/model_cooldown | 该卡当前不可用，避免消耗无意义的同卡重试，允许已有范围内换号 |
| 上下文超限、内容或 cyber policy 拒绝 | 不换号，不把拒绝计为账号健康故障 |
| Manager 预检没有候选 | 保留原 HTTP 状态和消息，新增具体 code，区分额度、订阅、模型、策略和未知可用性 |
| 额度未知或快照过期且保留额度策略要求新数据 | 不标记为已确认额度耗尽 |
| 没有备用卡、绑定单卡、关闭相应切换开关 | 返回当前错误，不扩大密钥授权范围 |

保留项目已有单卡 1 至 5 次、内部转发链最多 10 次的限制，跨卡仍受 Router `max_fallbacks` 限制。2 分钟是允许继续重试的窗口，带签名的内部票据传递同一个截止时间，换卡不能重置它；到期不再发起尝试。已经开始正常输出的流仍遵守原请求总超时和 read timeout，不因为重试窗口到期而截断。Provider Retry-After 超过剩余重试窗口时直接停止，不截短等待后强行请求

指数退避上限为 8 秒，起始退避仍使用本项目原有配置，没有把上游 500 毫秒默认值强加到用户配置。持久化瞬时限流冷却在预算耗尽后取已有观察值、Retry-After 和 5 秒保守回退中的最大值；没有新分类字段的旧 Manager 请求保留旧行为。已知额度重置和管理员手动停用不被清除

CLIProxyAPI 自身的冷却继续有效；若其拒绝同卡重试并返回 `model_cooldown`，网关按不可用账号处理，不强行清除冷却。原生成配置仍保持 `request-retry: 0`、`max-retry-credentials: 1`、`max-retry-interval: 0`、`streaming.bootstrap-retries: 0`，防止两层重试叠加

## 续接与安全边界

流式请求仅在未输出正文、工具调用、推理等有效事件前重试。已经输出的流、读取超时、写入超时和不明确是否执行的请求不自动重放。签名修复完成后不会再借可用性错误触发跨卡迁移

Responses 续接复用已有加密、密钥隔离、有大小和过期限制的可见历史缓存。额度耗尽预检返回类型化 503 时，即使 Router 已写入 failed_deployment_id，也能按开关重建完整可见历史、移除旧 previous_response_id，迁移一次。不复制上游隐藏推理状态，不重执行已有工具结果。缓存缺失、历史不完整、服务端工具、自动截断或绑定单卡时安全失败

未经分类的 503 不被猜测成额度耗尽；已经分类的瞬时错误也不会被日志正文里的 quota 字样覆盖。上游不能透传伪造 `x-account-pool-failure` 或 `x-account-pool-rebuild`。临时故障不能借仅供额度切换的 preview-model 开关改变模型

## 许可与验证边界

上游为 LGPL-3.0-or-later。移植分类文件保留该许可，旁置原 LGPL、完整 GPL v3 和 NOTICE；项目 LICENSE 明确列出例外，包元数据和打包清单包含相应文本。其余文件保留原有许可。分发时须同时提供移植文件及修改源码，不应将该文件重新标成 MIT

验证包括纯分类、Manager 冷却/租约、Router 到内部网关的本机真实 HTTP、模拟上游、SSE、安全续接、授权范围、计费回归，以及补丁重建和字节回滚。模拟上游通过不等于线上恢复；Go 后端、生产凭据、实际供应商请求和生产部署需单独验证。本改动没有这些运行结果，也不声称吞吐性能优于原系统

发布需要配套的新 Manager 和 LiteLLM 镜像：旧 Manager 对新增内部 finish 字段采用 extra=forbid，不能只更新网关后长期混跑。线上更新仍按既有 UPDATE_RUNBOOK 的成对镜像、备份、验证和回滚流程进行
