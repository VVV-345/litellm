# 号池系统工程实施计划书与防回归治理规范

- **文档版本**：`v1.2`
- **主工作分支**：`CLIProxyAPI分支`（本地分批验证后提交，收到用户明确指令后才执行 `push`）
- **基线提交**：`1e13b2c7a7`
- **最后更新**：`2026-11-24`

## 变更同步原则（Plan-First & Sync-on-Change）

1. **提出更改要求时必须先更新本计划书**：每当新增需求、调整默认行为、变更架构约束或增删修复范围时，先递增文档版本号并在「变更记录」与对应章节记录改动原因、影响范围与验收门禁，再开始修改代码。
2. **每完成一个批次必须回填进度与实测结果**：记录提交哈希、运行的验证命令与精确结果（含跳过/失败说明），禁止未经验证声称完成。

---

## 一、防回归十条红线

1. **Docker 网络与 Compose 标签兼容**：`deploy/docker-compose.yml` 声明的共享网络在运行时兜底建网前必须先 `docker network inspect`；仅在缺失时创建，并继承 Manager 容器的 `com.docker.compose.project` 与 `com.docker.compose.network` 标签，避免热升级后 `docker compose up -d` 标签冲突。
2. **禁止在全局数据库顾问锁内做外部 IO / Docker 调用**：`worker_lock` 与全局事务锁内仅做短 DB 读写与状态校验；代理 IP 定位 HTTP、Docker 容器执行（如指纹生成）以及 `onboarding` 对 `submit()` / `retry` 的调用均必须在锁外执行，提交事务内复核版本、绑定状态与人工暂停标记。
3. **跨子系统状态机闭环**：`onboarding` 驱动 `oauth_automation` 必须完整闭环前置就绪检查（入队前与已排队观察期均核验开关 + 镜像自检）、`pause -> cancel`、`retry -> 检查 delete_job 结果`、保留已保存凭据原卡时的 `verification_failed` 仅重试验收、`verification_failed -> failed`（不得落入 `cooling_down` 锁死）以及前端透传后端引导文案。
4. **默认值升级保护存量用户配置**：修改字段默认值时，仅刷新未修改过的 `version = 0` 初始默认行，绝不覆写管理员已显式保存的 `version > 0` 配置。
5. **读路径性能与连接池容错**：单卡 `get_environment` / `refresh_environment` 的 `spend_usd` 与 `in_flight` 必须按单卡过滤并匹配真实索引（`account_pool_leases_expires_idx` 与费用表达式部分索引 `account_pool_log_spend_env_idx`），禁止只过滤结果而不走索引；列表/单卡聚合捕获瞬时 DB 超时并降级返回默认值，避免拖垮管理接口。
6. **对话迁移错误透传边界**：`Conversation.preempt()` 与 `Conversation.rebuild()` 抛出 `ConversationUnavailableError`（503）前，必须先确认存在除失败卡外的其他可用备卡；无备卡（含卡绑虚拟 Key）时透传上游原始错误（如 429）。
7. **外部上游请求必须有界**：所有 `/models` 等外部拉取（含健康检查及前置 DNS 解析）必须施加总超时与响应体字节上限，防止 512MB Manager OOM 或长期占锁；目录刷新遵循 `docs/handbook/provider-standard.md` 契约（普通刷新合并 `custom_models` 与上游发现，显式目录发现返回上游目录且不改动卡内 `custom_models`）。
8. **子进程、网络上限与内存锁回收**：流式子进程（如 `docker image save`）在超时/取消时必须关闭管道并限时 `kill()` + `wait()`；所有读/写入口在 `NOT_FOUND` 或异常退出后不残留空锁；未清理的 OAuth 浏览器会话计入并发上限；检测到无标签旧版共享网络时记录明确告警。
9. **前端契约、i18n 与大文件红线**：后端新增字段同步 `schema.d.ts`；`zh-CN.json` 与 `en.json` 的 `accountPool` 键完全一致且零重复键（避免同名嵌套对象覆盖既有字符串键）；大组件文件须低于 ESLint `max-lines: 800` 红线。
10. **分支与提交纪律**：直接修改并提交到 `CLIProxyAPI分支`，不自动 `push`，不触碰无关未跟踪文件及既定升级兼容白名单策略。

---

## 二、全量加固分批清单

### 批次 1：批量 2FA 上号 ↔ 浏览器 OAuth 自动化闭环与锁竞争消除（P1 #1, P1 #2, P2 #9）
- [x] `AutomationRegistry.readiness_error()` 校验镜像自检与总开关
- [x] `AutomationRegistry.submit()` 将 `_match_profile()` 移出全局 DB 事务锁
- [x] `OnboardingService._enqueue_automation()` 增加就绪预检与 `delete_job` 结果检查
- [x] `OnboardingService.action(..., "pause")` 联动取消活跃自动化任务
- [x] `OnboardingService._observe()` 正确识别 `verification_failed` / `oauth_verification_pending` 为 `failed`
- [x] `OnboardingService.authorization()` 对自动化排队任务返回明确指引
- [x] `OnboardingService.import_login_file()` 与批量 2FA 行为自洽（自动化就绪即入队，未就绪标记手工模式不突变）
- **验收**：`test_onboarding_service.py`（`63 passed`）、`test_oauth_automation.py`（`67 passed, 25 skipped`） · 提交 `1a2dda8daf`

### 批次 2：共享 runtime 网络治理、恢复优化与资源防泄漏（P1 #3, P2 #8, P3 #11, P3 #12）
- [x] `ComposeRuntime._ensure_runtime_network()` 先 `inspect` 复用，缺失建网时继承 Compose 项目标签
- [x] `ComposeRuntime.ensure_runtime_network_connected()` 消除共享网络缺失时的 `O(N)` 恢复放大
- [x] `ComposeRuntime.backup_runtime()` 超时/取消时终止并回收 `docker image save` 子进程
- [x] `ControlledOAuthBrowser` / `OAuthBrowserSessionService` 限制并发浏览器会话上限，防止子网池耗尽
- [x] `EnvironmentService` 删除卡片后安全回收 `_locks`
- **验收**：`test_compose_renderer.py`、`test_runtime_maintenance.py`、`test_oauth_browser.py`、`test_account_pool.py` 共 `283 passed, 1 skipped`（Postgres 跳过项为无测试库环境） · 提交 `13a3dec3d2`

### 批次 3：存量默认开关升级兼容与 `/v1/responses` 错误透传（P1 #4, P2 #7）
- [x] `PostgresAccountPoolSettingsRepository.initialize()` 仅刷新未修改的 `version = 0` 存量初始行至最新默认开关
- [x] `Conversation.rebuild()` 先判断是否有除失败部署外的可用备卡，无备卡（含卡绑虚拟 Key）时透传上游原始错误而非 503
- **验收**：Manager `test_settings.py` + `test_gateway_service.py`（`94 passed`）；LiteLLM `test_account_pool_conversation.py` + `test_account_pool_quota_failover.py` + `test_account_pool_native_routing.py`（`256 passed`） · 提交 `29955707a9`

### 批次 4：实时并发与费用查询优化 + OpenAI 兼容卡发现加固（P1 #5, P2 #6）
- [x] `LeaseRepository.in_flight_counts(account_id: UUID | None = None)` 支持单卡精准查询并新增 `account_pool_leases_expires_idx` 幂等索引
- [x] `ErrorLogRepository.spend_by_environment(environment_id: UUID | None = None)` 支持单卡精准过滤
- [x] `EnvironmentService._spend_by_environment()` / `_in_flight_by_environment()` 增加 DB/连接池超时降级与告警
- [x] `OpenAICompatibleChannel._request_models()` 对所有模式施加 20s 总超时与 2 MiB 响应字节上限保护，并核实遵循 `provider-standard.md` 的 `custom_models` 与目录发现契约
- **验收**：Manager `test_openai_compatible.py` + `test_account_pool.py`（`350 passed`）；LiteLLM `test_account_pool_endpoints.py -k "in_flight or discover"`（`1 passed`） · 提交 `e853735f84`

### 批次 5：前端契约同步、次级面板 i18n 补齐与 `page.tsx` 行数防线（P2 #10）
- [x] `schema.d.ts` 的 `AccountPoolEnvironment` 补齐 `in_flight?: number`
- [x] `page.tsx` 抽出 `AccountPoolPageDialogs.tsx`，行数从 `809` 行降至 `701` 行（`< 760` 行防线，远离 `max-lines: 800`）并补齐首页剩余标签 i18n
- [x] 补齐 `ManagerLogsPanel.tsx`、`AccountPoolOnboardingPanel.tsx`、`AccountPoolOnboardingTasks.tsx`、`AccountPoolOnboardingCleanup.tsx`、`AccountPoolOnboardingTarget.tsx`、`AccountPoolCreateDialog.tsx`、`AccountPoolAuthorizationOverview.tsx` 的中英文 i18n（核实 `AccountPoolQuotaResetDialog.tsx` 已全量接入 `useTranslation`）
- **验收**：`zh-CN.json` / `en.json` 重复键 `0`、键数 `1694 / 1694`（0 缺失）；`page.tsx` `701` 行；`tsc --noEmit` 0 错误；全量 `eslint --quiet` 0 错误；全量 `vitest run src/features/account-pool src/app/(dashboard)/account-pool/page.integration.test.tsx` `53 passed (440 passed, Type Errors: no errors)` · 提交 `4a558f66d3`

### 批次 6：复核功能缺陷收口（#2 验收重试、#1 队列停用与前端指引、#7 单卡续聊 `preempt()`、#9 上号 `worker_lock` 缩锁）
- [x] `OnboardingService` 支持注入 `retry_automation_job`；保留已保存凭据原卡的 `verification_failed` 任务点击重试触发仅重试验收，不再原地 `failed -> queued -> failed`
- [x] `OnboardingService._observe()` 与 `_enqueue_automation()` 对已排队任务复查 `readiness_error()`，停用自动化时及时转入 `failed`；`AccountPoolOnboardingTasks.tsx` 透传后端授权指引文案
- [x] `Conversation.preempt()` 提前检查 `card_id` 与剩余候选卡数量，普通 Key 单卡冷却且历史未缓存时透传上游路由结果而不提前抛 503
- [x] `OnboardingService.import_login_file()` 与 `run_once()` 释放 `worker_lock` 后执行慢 `submit()` / `retry`，回写前复核人工暂停并联动取消
- **验收**：Manager `test_onboarding_service.py` + `test_oauth_automation.py`（`133 passed, 25 skipped`）；LiteLLM `test_account_pool_conversation.py`（`163 passed`）；UI `AccountPoolOnboardingPanel.integration.test.tsx`（`10 passed`）+ `eslint --quiet`（`0` 错误）

### 批次 7：索引、DNS 总超时、网络与资源回收加固（#5、#6、#3/#8、#11/#12）
- 本轮以 `a2ecd561ae` 及已有的 5 个后端工作区改动为起点；先验证第 7 批，再验证第 8 批，不改动线上网络或部署。
- [x] `management_repository.py` 新增部分表达式索引 `account_pool_log_spend_env_idx` 并与 `spend_by_environment()` 查询谓词对齐
- [x] `OpenAICompatibleChannel._discover_models()` 与 `discover_models_from_source()` 将 `_validate_destination()` 前置 DNS 解析纳入 20 秒总超时
- [x] `ComposeRuntime._ensure_runtime_network()` 检测既有无标签共享网络并告警；`OAuthBrowserSessionRepository.count_active()` 将未清理 `cleanup_pending` 会话计入并发上限
- [x] `ComposeRuntime.backup_runtime()` 超时/取消时关闭管道并限时等待子进程；`EnvironmentService._locks` 使用弱引用注册表，仅由使用者和等待者持有锁，在 `NOT_FOUND` 与删卡/异常退出后零残留
- **本地验收**：Manager venv 下 `pytest -q -rs` 运行上述 5 个模块与 `test_cliproxy_runtime_image.py`，401 通过、1 跳过（未配置独立 PostgreSQL 测试库）。取消备份用真实本地子进程和管道；未启动 Docker，宿主 Docker 服务不可用。隔离的 PGlite 实例插入 12,000 条费用记录后，执行实际单卡 SQL 的 `EXPLAIN` 命中 `account_pool_log_spend_env_idx`；同一数据库验证过期未清理会话仍占一个名额。

### 批次 8：补齐剩余前端面板中英文 i18n（#10）
- [ ] `AccountPoolAutoUpdatePanel.tsx` 接入 `useTranslation()` 并按 `i18n.language` 格式化时间
- [ ] `AccountPoolAutomationPanel.tsx` 接入 `useTranslation()` 并控制文件行数 `< 780`（远离 `max-lines: 800` 红线）
- [ ] `zh-CN.json` 与 `en.json` 成对新增 `accountPool.autoUpdate` 与 `accountPool.automation`
- **验收**：`check_locales.js` 0 重复键 0 缺失、`AccountPoolAutomationPanel.tsx < 780` 行、`tsc --noEmit` 0 错误、全量 `eslint --quiet` 0 错误、全量 `vitest` 通过

---

## 三、变更记录（Change Log）

| 版本 | 日期 | 变更说明 | 对应提交 |
| :--- | :--- | :--- | :--- |
| `v1.0` | `2026-11-24` | 建立号池系统全面加固 5 阶段计划书、十条防回归红线与需求变更同步规则，并完成批次 1 | `1a2dda8daf` |
| `v1.1` | `2026-11-24` | 完成批次 2–5 全部加固与回归验证；按 `docs/handbook/provider-standard.md` 明确兼容卡 `custom_models` 与显式目录发现边界；红线 #9 新增 JSON 重复键门禁 | `13a3dec3d2`、`29955707a9`、`e853735f84`、`4a558f66d3` |
| `v1.2` | `2026-11-24` | 依据 `4a558f66d3` 复核结论新增批次 6–8（#2 保留原卡重试验收、#1 队列停用观察与前端透传、#7 `preempt()` 备卡前移、#9 上号 `worker_lock` 缩锁、#5 部分索引、#6 DNS 总超时、#3/#8 未清理会话计数与无标签告警、#11/#12 限时子进程回收与引用计数锁、#10 剩余两面板 i18n），并强化红线 #2/#3/#5/#6/#7/#8 | 批次 6–8 进行中 |
