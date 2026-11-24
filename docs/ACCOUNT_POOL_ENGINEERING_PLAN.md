# 号池系统工程实施计划书与防回归治理规范

- **文档版本**：`v1.0`
- **主工作分支**：`CLIProxyAPI分支`（本地分批验证后提交，收到用户明确指令后才执行 `push`）
- **基线提交**：`1e13b2c7a7`
- **最后更新**：`2026-11-24`

## 变更同步原则（Plan-First & Sync-on-Change）

1. **提出更改要求时必须先更新本计划书**：每当新增需求、调整默认行为、变更架构约束或增删修复范围时，先递增文档版本号并在「变更记录」与对应章节记录改动原因、影响范围与验收门禁，再开始修改代码。
2. **每完成一个批次必须回填进度与实测结果**：记录提交哈希、运行的验证命令与精确结果（含跳过/失败说明），禁止未经验证声称完成。

---

## 一、防回归十条红线

1. **Docker 网络与 Compose 标签兼容**：`deploy/docker-compose.yml` 声明的共享网络在运行时兜底建网前必须先 `docker network inspect`；仅在缺失时创建，并继承 Manager 容器的 `com.docker.compose.project` 与 `com.docker.compose.network` 标签，避免热升级后 `docker compose up -d` 标签冲突。
2. **禁止在全局数据库顾问锁内做外部 IO / Docker 调用**：`worker_lock` 与全局事务锁内仅做短 DB 读写与状态校验；代理 IP 定位 HTTP、Docker 容器执行（如指纹生成）必须在锁外执行，提交事务内复核版本与绑定状态。
3. **跨子系统状态机闭环**：`onboarding` 驱动 `oauth_automation` 必须完整闭环前置就绪检查（镜像自检 + 总开关）、`pause -> cancel`、`retry -> 检查 delete_job 结果`、`verification_failed -> failed`（不得落入 `cooling_down` 锁死）。
4. **默认值升级保护存量用户配置**：修改字段默认值时，仅刷新未修改过的 `version = 0` 初始默认行，绝不覆写管理员已显式保存的 `version > 0` 配置。
5. **读路径性能与连接池容错**：单卡 `get_environment` / `refresh_environment` 的 `spend_usd` 与 `in_flight` 必须按单卡过滤，禁止扫全表；`account_pool_leases` 补齐 `expires_at` 索引；列表/单卡聚合捕获瞬时 DB 超时并降级返回默认值，避免拖垮管理接口。
6. **对话迁移错误透传边界**：`Conversation.rebuild()` 抛出 `ConversationUnavailableError`（503）前，必须先确认存在除失败卡外的其他可用备卡；无备卡（含卡绑虚拟 Key）时透传上游原始错误（如 429）。
7. **外部上游请求必须有界**：所有 `/models` 等外部拉取（含健康检查）必须施加总超时与响应体字节上限，防止 512MB Manager OOM 或长期占锁；目录刷新必须保留 `custom_models`。
8. **子进程与内存锁回收**：流式子进程（如 `docker image save`）在超时/取消时必须 `kill()` + `wait()`；卡片删除后在锁保护下安全回收内存锁。
9. **前端契约、i18n 与大文件红线**：后端新增字段同步 `schema.d.ts`；`zh-CN.json` 与 `en.json` 的 `accountPool` 键完全一致；大组件文件须低于 ESLint `max-lines: 800` 红线。
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
- **验收**：`test_onboarding_service.py`（`63 passed`）、`test_oauth_automation.py`（`67 passed, 25 skipped`）

### 批次 2：共享 runtime 网络治理、恢复优化与资源防泄漏（P1 #3, P2 #8, P3 #11, P3 #12）
- [x] `ComposeRuntime._ensure_runtime_network()` 先 `inspect` 复用，缺失建网时继承 Compose 项目标签
- [x] `ComposeRuntime.ensure_runtime_network_connected()` 消除共享网络缺失时的 `O(N)` 恢复放大
- [x] `ComposeRuntime.backup_runtime()` 超时/取消时终止并回收 `docker image save` 子进程
- [x] `ControlledOAuthBrowser` / `OAuthBrowserSessionService` 限制并发浏览器会话上限，防止子网池耗尽
- [x] `EnvironmentService` 删除卡片后安全回收 `_locks`
- **验收**：`test_compose_renderer.py`、`test_runtime_maintenance.py`、`test_oauth_browser.py`、`test_account_pool.py` 共 `283 passed, 1 skipped`（Postgres 跳过项为无测试库环境）

### 批次 3：存量默认开关升级兼容与 `/v1/responses` 错误透传（P1 #4, P2 #7）
- [x] `PostgresAccountPoolSettingsRepository.initialize()` 仅刷新未修改的 `version = 0` 存量初始行至最新默认开关
- [x] `Conversation.rebuild()` 先判断是否有除失败部署外的可用备卡，无备卡（含卡绑虚拟 Key）时透传上游原始错误而非 503
- **验收**：Manager `test_settings.py` + `test_gateway_service.py`（`94 passed`）；LiteLLM `test_account_pool_conversation.py` + `test_account_pool_quota_failover.py` + `test_account_pool_native_routing.py`（`256 passed`）

### 批次 4：实时并发与费用查询优化 + OpenAI 兼容卡发现加固（P1 #5, P2 #6）
- [ ] `LeaseRepository.in_flight_counts(account_id: UUID | None = None)` 支持单卡精准查询并新增 `account_pool_leases_expires_idx` 幂等索引
- [ ] `ErrorLogRepository.spend_by_environment(environment_id: UUID | None = None)` 支持单卡精准过滤
- [ ] `EnvironmentService._spend_by_environment()` / `_in_flight_by_environment()` 增加 DB/连接池超时降级与告警
- [ ] `OpenAICompatibleChannel.discover_model_catalog()` 保留 `custom_models`
- [ ] `OpenAICompatibleChannel._request_models()` 对所有模式施加总超时与字节上限保护
- **验收**：`test_account_pool.py`、`test_openai_compatible.py`、`test_account_pool_endpoints.py`

### 批次 5：前端契约同步、次级面板 i18n 补齐与 `page.tsx` 行数防线（P2 #10）
- [ ] `schema.d.ts` 的 `AccountPoolEnvironment` 补齐 `in_flight?: number`
- [ ] `page.tsx` 拆分辅助逻辑，控制行数 `< 760`
- [ ] 补齐 `ManagerLogsPanel.tsx`、`AccountPoolOnboarding*`、`AccountPoolCreateDialog.tsx`、`AccountPoolQuotaResetDialog.tsx`、`AccountPoolAuthorizationOverview.tsx` 的中英文 i18n
- **验收**：`zh-CN.json` / `en.json` 键 parity 0 缺失、`tsc --noEmit` 0 错误、全量 `eslint --quiet` 0 错误、全量 `vitest run src/features/account-pool` 通过

---

## 三、变更记录（Change Log）

| 版本 | 日期 | 变更说明 | 对应提交 |
| :--- | :--- | :--- | :--- |
| `v1.0` | `2026-11-24` | 建立号池系统全面加固 5 阶段计划书、十条防回归红线与需求变更同步规则 | 待提交 |
