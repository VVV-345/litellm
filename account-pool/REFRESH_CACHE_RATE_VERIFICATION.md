# 认证刷新、额度入口与缓存率修复记录

核查与修复日期：2026-09-16

## 完成状态

- [x] 认证文件页提供立即刷新、5/15/30/60 分钟周期和刷新状态，默认 15 分钟；后端保存配置并运行独立调度
- [x] 仪表盘提供刷新额度入口、上次完成时间和下次计划时间，复用额度页的刷新接口
- [x] 成功请求日志逐条显示缓存率，前后端契约和日志存储包含该字段
- [x] 修正 OpenAI 缓存率分母，输入总数已包含缓存命中，不再重复相加；标准响应没有缓存创建字段时也能显示命中率
- [x] 同一环境、同一凭据的供应商管理调用共用异步锁，Codex 额度端点顺序执行；其他凭据和环境仍可并行
- [x] 验证锁在取消和失败后释放，失败或重试请求不记录成功缓存率
- [x] 保留现有提交历史、请求日志和原始计划，新增中文提交并按用户最新要求推送

## 并发问题的处理边界

核查时，Codex 的 `accounts/check` 先执行，随后 `wham/usage` 和 `wham/rate-limit-reset-credits` 通过 `asyncio.gather` 并发执行。原有环境操作锁只保护外层刷新，不能阻止一次刷新内部的并发调用

本次改为按顺序调用 Codex 端点，同时在供应商管理请求入口按环境 UUID 和 auth_index 加锁。锁使用弱引用保存，仍在等待或执行的请求持有强引用，空闲锁不长期积累。HTTP 错误和任务取消都会退出锁的上下文

当前固定的 CLIProxyAPI 版本 `bebf587f5a940af676f6e503065140d4991ae1f3` 已在 `sdk/cliproxy/auth/conductor_refresh.go` 的 `refreshAuthForRequest` 中按凭据加锁，并在获得锁后读取当前凭据。本次补齐 Manager 侧保护，没有修改 CLIProxyAPI 仓库，也没有取消上游不可用状态或绕过自动冷却

已经被上游判定失效的 refresh token 无法通过互斥锁恢复，仍需重新授权。这里记录的是代码修复和本地验证，不代表生产容器已经部署或特定账号已经恢复

## 缓存率规则

号池数据面支持 OpenAI-compatible 接口，输入 token 总数包含缓存命中。成功请求的缓存率为 `cache_read_input_tokens / input_tokens`，与是否返回 `cache_creation_input_tokens` 无关。输入 100、命中 80 的结果为 80%，汇总缓存率也不再重复计入缓存 token

标准 OpenAI Responses 和 Chat Completions 响应分别从 `input_tokens_details.cached_tokens`、`prompt_tokens_details.cached_tokens` 读取命中量。已返回该字段但未返回缓存创建量时，缓存创建量按 0 展示。没有用量或命中信息时保留未知，不把未知当作未命中

逐请求字段仅对成功且非重试的请求计算。输入为零、命中量超过输入总数时保留未知，防止不一致的上游用量导致请求收尾失败。历史日志不批量改写

## 验证结果

先补充回归断言，确认旧代码出现同一凭据并发调用、缓存率为空和缓存率为 44.4% 的失败，再修复实现

| 验证范围 | 命令 | 结果 |
| --- | --- | --- |
| 号池后端完整测试 | 在 `account-pool` 执行 `.venv/Scripts/python.exe -m pytest tests -q` | 374 项通过 |
| 补齐取消、失败和跨环境测试后的供应商客户端 | 在 `account-pool` 执行 `.venv/Scripts/python.exe -m pytest tests/account_pool/test_cliproxy_supplier_client.py -q` | 42 项通过 |
| LiteLLM 网关与管理接口 | `.venv/Scripts/python.exe -m pytest tests/test_litellm/proxy/management_endpoints/test_account_pool_gateway.py tests/test_litellm/proxy/management_endpoints/test_account_pool_endpoints.py -q` | 92 项通过 |
| 号池前端全部测试 | 在 `ui/litellm-dashboard` 执行 `node node_modules/vitest/vitest.mjs run 'src/app/(dashboard)/account-pool/'` | 24 个文件、101 项通过 |
| Python 格式与基础规则 | 对本次修改的 Python 文件运行 Ruff 格式检查，对源文件和测试分别运行相应规则 | 通过 |
| 用量解析类型检查 | `.venv/Scripts/basedpyright.exe litellm/proxy/management_endpoints/account_pool_stream.py --pythonpath .venv/Scripts/python.exe` | 0 错误、0 警告 |
| 前端变更格式与规则 | 对日志面板测试运行 Prettier 和 ESLint | 通过 |

类型纪律检查仍有用量解析文件原有的 3 处 `LIT001` 参数类型告警，本次未新增，也未修改预算以掩盖它们。测试包含现有依赖弃用告警，未出现测试失败。本次未进行线上重新认证、真实计费请求或容器部署

## 历史提交

此前本地已有 9 个提交：`09dc5886b1`、`56f3bff3a7`、`bb2e31c070`、`749219200e`、`6bb0fae9c7`、`683558fddf`、`835108d1fa`、`a116e1163e`、`56a05fd42d`

其中 `09dc5886b1` 原始标题为英文，中文含义为“记录逐请求缓存率”。保留它及后续提交的原始记录，不重写已有历史；本次新增提交使用中文，并将上述尚未推送的提交一起推送

原始执行计划保存在 `docs/superpowers/plans/2026-09-16-account-pool-refresh-cache-rate.md`。其中旧缓存率公式和“不推送”要求已经由本次修正及用户最新的 commit、push 指令取代
