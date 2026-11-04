# 额度重置、周期恢复与路由一致性修复方案

2026-11-03 执行状态：五项本地修复已实现，按项提交；本地验证、未覆盖项及生产验收边界见 D:/Work2api/litellm/docs/superpowers/plans/2026-11-03-quota-reset-recovery-execution.md。下文保留原批准方案，未打勾项目不能视为全部现场验收通过

> 执行方式：批准后在当前会话按任务串行执行，使用 executing-plans；每项先复现、再最小修复、验证后独立 commit。不创建分支或 worktree，不自动 push 或部署。

**目标：** 对同一卡片、同一模型，手动额度兑换或上游自然重置后，不再被已经失效的本地额度/聚合错误状态阻断；只有普通 LiteLLM 数据面完成真实响应才能记录“调用恢复”。

**架构：** 手动兑换与自然到期是不同入口，复用额度观测、按作用范围验证、条件恢复和路由检查。兑换身份与验证结果分离；不创建第二套认证流程，不把额度重置变成 OAuth 刷新，不关闭正常访问控制。

**技术栈：** Python/FastAPI/Pydantic/PostgreSQL、Go CLIProxyAPI 适配补丁、LiteLLM、React/TypeScript。

**依据：** 本次用户澄清“自动重置”是 OpenAI 给出的额度重置时间，不是自动使用重置券。源码基线 f9c0d9fa171a68b949d01cb83838dcdfa8272e76。诊断记录：
- D:/Work2api/litellm/.local/quota-reset-20261102/v193-REVIEW.md
- D:/Work2api/litellm/.local/quota-reset-20261102/v193-startup-auth-diagnosis.txt

## 范围与保证边界

- TARGET 固定为 D:/Work2api/litellm，主分支 CLIProxyAPI分支；保留现存无关改动。
- 本计划不启用自动兑换重置券；自然到期流程的 consume 调用次数必须始终为 0。
- 手动兑换仅使用供应商正常提供、当前账号适用的额度；不突破额度、风控或认证限制。
- 不承诺“除上游外永不失败”：代理网络、人工停用、密钥权限、预算、并发和正在更新配置也可能合法阻断。承诺是避免旧状态误拦截，并明确标出真实阻断层及原因。
- 不以“status=ready”“余额>0”“HTTP 200”“管理探针成功”单独作为全链路恢复证据。
- 一个模型通过不代表全部模型通过；模型级与共享额度窗口的恢复范围必须来自可核对的来源，不靠模型名猜测。
- 当前本地已复现缺陷，不等于已经确认生产现场根因。用户提供 2026-11-02 22:05（Asia/Shanghai）的成功请求 bac13211-1f70-4fbb-ab46-36f6843938dd；未读取到部署后的实时日志。

## 已确认的风险

1. CPA 的模型 A 401、模型 B 随后成功，可能仍留下账号顶层 error/unauthorized，同时 B 为 active、整卡 unavailable=false。Manager 启动读取顶层错误后变成 cooling_down，整个候选退出路由。
2. authentication_failed 未区分模型聚合错误、凭据失效和 auth_unavailable；现有模型隔离测试不含上述组合。
3. recovered_record 清全卡 rate_limit，但 CPA 只恢复选中模型；模型额度解析和替换未完整接通。
4. 已有运行时冷却和在途租约也会阻止恢复。不能只修卡片 status 或粗暴清空所有租约。
5. 当前自然刷新按 5–60 分钟间隔运行，不是按下一次 resets_at 精确唤醒；普通探活不证明模型推理可用。
6. 兑换 UI 可能把 uncertain/no_credit 当作默认恢复依据；能力探测的网络错误被归为需要升级；验证结果未落库。
7. 新兑换表的兼容规则缺失；Manager 发布不自动升级现有卡片 CPA。

## 两条入口与共同恢复流程

### 手动兑换

用户确认 → 校验单卡归属/凭据指纹/适用重置券 → 持久化 request_id 与 uncertain 检查点 → 单次发送 consume → 记录业务结果 → 重新查额度 → 共同恢复流程。

- 重复点击、两个工作进程、进程重启只能继续原兑换身份。
- 超时或崩溃后，只有供应商明确保证幂等时才允许同身份补偿；否则停在待核对，不盲目重发消费请求。
- no_credit、uncertain、nothing_to_reset 不直接升级为“已恢复”。already_redeemed 也须核对原操作与当前观测。
- 已恢复额度但仍不可调用时，不得再消费一张券试图解除认证错误。

### 上游自然周期重置

已记录的有效 resets_at 到期，或新上游额度证据表明已恢复 → 主动刷新额度 → 共同恢复流程；不依赖页面打开或用户继续发失败请求。

- UTC 持久化，Asia/Shanghai 展示；不把 11/09 12:40 的本地字符串当 UTC。
- 复用现有刷新任务：按最近的相关耗尽窗口唤醒，保留原定时刷新兜底和事件合并。目标为健康、无排队条件下到期 60 秒内启动观测，不承诺供应商一定在该秒恢复。
- 上游有延迟/返回 429/网络失败：沿用有上限的退避及 Retry-After，页面显示“到期，等待上游确认”，不抖动成可用/失效，不热循环。
- 重启扫描已到期且待确认窗口并补查；不因重启丢失恢复机会，也不向上游补发兑换请求。

### 共同恢复流程

1. 获取单卡锁并读取最新记录、实际 CPA 能力、凭据归属和有效代理；复用当前代理，不为修复静默换线路。
2. 获取带时间戳的新额度证据，区分总窗口、模型窗口、缺失字段与明确清空；无法归属的窗口标记未知，不伪造模型额度。
3. 判定错误作用范围。独立凭据撤销、人工禁用、权限和配置错误优先；不能仅用 unavailable=false 或存在一个健康模型推翻确定的凭据失效。
4. 对已启用、受影响模型执行现有 no-refresh 验证；同凭据、同代理、无工具、无跨卡回退。验证使用最小输入并受并发/预算约束。自然恢复可在额度证据充足时等待首次普通调用确认，而不能提前声称“调用已验证”。
5. 只恢复此次证据覆盖的失效限制：CPA 模型状态、Manager 模型额度与冷却、可归属的运行时阻断。没有原因/范围/版本依据的旧冷却不能盲删；须到期或重新验证。仍有效的窗口、其他模型短期限流和人工限制保持不变。
6. CPA 与数据库不能原子提交：用现有代际/指纹和数据库 CAS 保护；CPA 成功而 Manager 冲突时记录部分完成并重读协调，不消费新券、不覆盖并发修改，不宣称成功。
7. 恢复后重新解析路由候选并失效相关旧快照；不得清除真实在途租约。逐项输出阻断层与过期时间，不能统称认证失败。
8. 通过普通 LiteLLM 接口完成响应，核对真实选卡、模型、代理、终止事件和统一记账。持久化结果：额度已恢复 / 状态已协调 / 调用已验证 / 部分完成或失败。

## 分提交实施与验证

每项固定顺序：添加能失败的回归用例 → 执行并保存 RED 输出 → 最小实现 → 定向测试及邻接回归 → 保存 GREEN 输出 → 独立 commit。以下是行为方案，不是已应用补丁。

### Commit 1：发布兼容与旧卡能力检查

文件：
- D:/Work2api/litellm/account-pool/account_pool/release_compatibility.py
- D:/Work2api/litellm/account-pool/tests/test_release_database.py
- D:/Work2api/litellm/account-pool/tests/test_release_database_integration.py
- D:/Work2api/litellm/account-pool/account_pool/channels/cliproxyapi/quota_reset.py
- D:/Work2api/litellm/account-pool/tests/account_pool/test_quota_reset_client.py

- [ ] 精确放行兑换表/索引的可兼容增加；同时验证旧版读取和回退，不关闭门禁。若后续新增持久化字段，随对应提交补兼容测试。
- [ ] 404/不支持版本归为能力缺失；管理端 401、超时、5xx 分开记录，后者不能提示“必须升级”。
- [ ] 验证 v1.9.2→候选升级、候选→旧版回退及不兼容负例；确认既有 CPA 未升级时明确提示、不消费券。

### Commit 2：认证错误作用范围与重启误封

文件：
- D:/Work2api/litellm/account-pool/account_pool/channels/cliproxyapi/protocol.py
- D:/Work2api/litellm/account-pool/account_pool/channels/cliproxyapi/client.py
- D:/Work2api/litellm/account-pool/cliproxy-adapter/settings-adaptation.patch
- D:/Work2api/litellm/account-pool/cliproxy-adapter/sources.json
- D:/Work2api/litellm/account-pool/tests/account_pool/test_cliproxy_supplier_client.py
- D:/Work2api/litellm/account-pool/tests/test_account_pool.py

- [ ] 将本轮已运行的两段复现并入正式测试：A 401→B 成功→启动 refresh_auth_files，B 不应因 A 聚合错误被整卡禁用。
- [ ] 通过适配器暴露必要的非敏感错误范围/原因及版本，保留旧协议兼容；旧 CPA 不足以区分时标记待验证，不强行放行。
- [ ] auth_unavailable 不再直接等同于 Token 失效；真实 refresh_token_invalidated/reused、全卡凭据失效仍阻断。
- [ ] 验证普通刷新、定时刷新和 Manager 重启后状态一致；本次路径不得出现 Token 写入、消费重置券或代理变更。

### Commit 3：额度证据与按范围恢复

文件：
- D:/Work2api/litellm/account-pool/account_pool/providers/usage/codex.py
- D:/Work2api/litellm/account-pool/account_pool/channels/cliproxyapi/quota_reset.py
- D:/Work2api/litellm/account-pool/account_pool/quota_reset.py
- D:/Work2api/litellm/account-pool/account_pool/repository.py
- D:/Work2api/litellm/account-pool/account_pool/gateway_repository.py
- D:/Work2api/litellm/account-pool/account_pool/gateway_service.py
- D:/Work2api/litellm/account-pool/cliproxy-adapter/settings-adaptation.patch
- D:/Work2api/litellm/account-pool/tests/account_pool/test_quota.py
- D:/Work2api/litellm/account-pool/tests/account_pool/test_quota_reset.py
- D:/Work2api/litellm/account-pool/tests/account_pool/test_quota_reset_postgres.py
- D:/Work2api/litellm/account-pool/tests/account_pool/test_gateway_service.py

- [ ] 解析有明确语义的模型窗口；不以空数组保留已证伪的 0% 旧缓存，也不把缺少字段当成所有模型无限额。
- [ ] 去掉全卡无条件清 rate_limit；单模型验证只恢复对应范围，共享窗口依据 fresh 观测恢复对应范围。
- [ ] 测试 CPA 已恢复但 Manager CAS 冲突、在途租约存在、新冷却写入和凭据更换；不得覆盖新状态。
- [ ] 修复跨层旧快照不一致；每个删除操作必须有范围和版本条件。真实并发/预算/人工限制仍阻断并返回具体原因。

### Commit 4：自然到期调度接通共同恢复

文件：
- D:/Work2api/litellm/account-pool/account_pool/quota_scheduler.py
- D:/Work2api/litellm/account-pool/account_pool/quota_refresh_trigger.py
- D:/Work2api/litellm/account-pool/account_pool/service.py
- D:/Work2api/litellm/account-pool/account_pool/app.py
- D:/Work2api/litellm/account-pool/tests/test_account_pool.py
- D:/Work2api/litellm/account-pool/tests/account_pool/test_quota_refresh_trigger.py

- [ ] 使用可控时钟测试到点唤醒、已过期启动补查、UTC 时区和多个窗口，不能等到现实中的 11/09 才做基础验收。
- [ ] 以相关耗尽窗口的到期时间调整现有任务唤醒；合并同卡任务，保留有界并发和失败退避。
- [ ] 手动兑换和自然到期调用共同恢复逻辑；自然路径不需要成功兑换记录，但共享窗口清理必须有新的供应商恢复证据。
- [ ] 验证页面关闭仍恢复、重启不丢任务、失败不循环消耗；断言自然路径 consume=0、reset-triggered OAuth refresh=0。

### Commit 5：结果持久化、真实提示与端到端验收

文件：
- D:/Work2api/litellm/account-pool/account_pool/quota_reset_models.py
- D:/Work2api/litellm/account-pool/account_pool/quota_reset_repository.py
- D:/Work2api/litellm/account-pool/account_pool/quota_reset_api.py
- D:/Work2api/litellm/litellm/proxy/management_endpoints/account_pool_endpoints.py
- D:/Work2api/litellm/ui/litellm-dashboard/src/features/account-pool/components/cards/AccountPoolQuotaResetDialog.tsx
- D:/Work2api/litellm/ui/litellm-dashboard/src/features/account-pool/api/AccountPoolQuotaResetApi.ts
- D:/Work2api/litellm/account-pool/tests/account_pool/test_quota_reset_api.py
- D:/Work2api/litellm/tests/test_litellm/proxy/management_endpoints/test_account_pool_gateway.py

- [ ] 兑换失败或结果不明不得默认勾选已恢复；额度恢复与调用验证分开显示。
- [ ] 验证结果、模型、时间、原因和关联请求 ID 落库；关闭页面、重启后仍能还原。自然恢复记录不能伪造一个兑换操作。
- [ ] 使用实际路由、选卡、CPA 执行器和本地模拟供应商做集成测试，而不仅 mock 所有服务方法；同步测试原生 Responses 和 Chat Completions 转换链路。
- [ ] 逐层注入错误验证归因：本地路由、CPA 调度、代理传输、上游401/429/5xx、流内错误。没有上游响应时不能报告“上游 Token 失效”。
- [ ] 核查 UI、API、Python 类型检查、Go 测试和生产构建。按项目现有测试映射扩展，不顺手重构其他模块。

## 必须通过的验收矩阵

| 场景 | 必须观测到的结果 |
|---|---|
| A 模型401、B模型随后成功，重启 Manager | B 不因 A 聚合错误被整卡误封；A 的有效限制保留 |
| 手动兑换成功，供应商恢复相关额度 | 仅扣适用次数；fresh 额度更新；相关模型普通请求完成 |
| 重复点击/多实例/发送后崩溃 | 不创建第二兑换身份；结果不明不继续消耗其他券 |
| 自然到期，页面关闭 | 到点触发观测、无需手点；consume=0 |
| 到点但上游未恢复/查询超时 | 待确认、按 Retry-After/退避重查；不虚报可用或认证失效 |
| 总额度恢复，但模型专属额度仍为0 | 仅可用模型恢复；不能声明全模型恢复 |
| A验证成功，B独立短期429未过期 | B保持原限制，不被全卡清理误删 |
| 凭据撤销、人工禁用、权限不足 | 不自动解除，具体原因正确，无自动换卡掩盖失败 |
| CPA成功、数据库CAS失败/有在途请求 | 记录部分完成并协调，不能吞错或虚报 restored |
| 管理验证成功，普通路由仍被旧状态拦截 | 整体验收失败；定位剩余阻断层，不再次兑换 |
| 普通HTTP200但SSE包含error或无完成事件 | 调用失败，不能仅按HTTP状态判成功 |
| Manager/CPA重启、页面再打开 | 已验证状态不被旧快照倒灌；日志和统计可追踪 |
| 旧卡无新接口、管理端401、代理超时 | 三种错误分别呈现，不统称升级要求/Token失效 |

## 验证命令与证据标准

从 D:/Work2api/litellm 执行，使用已有隔离 Python；环境变量：PYTHONPATH=account-pool;account-pool/tests/account_pool;enterprise，PYTHONUTF8=1，LITELLM_LOCAL_MODEL_COST_MAP=True。pytest 的 --basetemp 必须指向 .local/quota-reset-20261102 下一个新建且不存在的专用目录，避免系统 pytest-current 清理错误；不删除用户目录。

```powershell
& .local/quota-reset-20261102/venv/Scripts/python.exe -m pytest account-pool/tests/account_pool/test_cliproxy_supplier_client.py account-pool/tests/account_pool/test_quota_reset.py account-pool/tests/account_pool/test_quota_reset_client.py account-pool/tests/account_pool/test_quota_reset_api.py account-pool/tests/account_pool/test_quota_refresh_trigger.py account-pool/tests/account_pool/test_gateway_service.py -q --tb=short -p no:cacheprovider
& .local/quota-reset-20261102/venv/Scripts/python.exe -m pytest account-pool/tests/test_account_pool.py account-pool/tests/test_release_database.py tests/test_litellm/proxy/management_endpoints/test_account_pool_gateway.py -q --tb=short -p no:cacheprovider
```

Go 在按 sources.json 固定版本、重新应用正式补丁的临时副本中执行；不能以历史临时副本测试通过代替新补丁可重建验证。数据库 CAS/并发用真实临时 PostgreSQL 验证；不可用时明确记为未验收，不以纯 mock 替代。

每次记录 commit、命令、输入、stdout、stderr、退出码、测试范围和未执行项。没有新测试输出，不复用历史通过数作为当前验收。

## 生产灰度与停止条件（需另行授权）

1. 只读确认实际主机/镜像digest/Manager、release-worker、第一张卡CPA版本；核对该卡7895有效代理与部署前证据。读取脱敏状态和Token哈希，不下载或展示真实凭据正文。
2. 按 UPDATE_RUNBOOK 做备份、兼容校验和受控升级；发布器、业务容器、既有卡片CPA分别确认，不以GitHub构建成功代替部署成功。
3. 只针对 1972dfac-cd36-5f82-8d9a-31aa7de3c03f。先做不消费次数的现有额度恢复验证；不能为了验收再浪费一张券。第二张卡不测试、不修改。
4. GPT-5.6-SOL 同一卡、同代理、同密钥范围分别经 /v1/chat/completions 和 /v1/responses 发最小、无工具请求，禁止跨卡回退掩盖失败。验证内容、结束事件、实际账号ID、请求ID和LiteLLM记账。
5. 受控重启和再次刷新后重复普通调用；业务请求与测试探针分别计数，不重复记账。任何代理、认证、部分提交或正常路由失败立即停止扩大灰度。
6. 自然周期先用模拟时钟完成全链路验收；真实上游到期再做一次现场复验。在真实周期未发生前，只报告模拟通过，不宣称生产自然到期已验收。
7. 回退代码/配置不等于退回已经消费的重置次数；保留兑换台账，不恢复过时Token覆盖可能已轮换的新凭据。外部消费不可回滚必须明确告知。

## 方案阶段状态（历史，不代表当前执行结果）

本轮只形成方案，不实现、不 push、不部署、不消费额度。针对既有幂等、启动刷新、独立阻断与无刷新消费保护的定向检查：15 passed, 217 deselected，exit 0。首次检查结束时系统临时目录清理 PermissionError 导致 exit 1；已用新的 workspace --basetemp 重跑并保留错误来源。证据：D:/Work2api/litellm/.local/quota-reset-20261102/reset-plan-validation.txt。

历史四角色工件继续保留，后续修改扩展其证据范围而不是把历史结果当成新候选验收：
- D:/Work2api/litellm/.local/quota-reset-20261102/transaction/MODIFIED_FILE.tar.gz
- D:/Work2api/litellm/.local/quota-reset-20261102/transaction/DIFF_FILE.patch
- D:/Work2api/litellm/.local/quota-reset-20261102/transaction/VERIFICATION.txt
- D:/Work2api/litellm/.local/quota-reset-20261102/transaction/ROLLBACK.sh

历史选券行为：BASELINE no-expiry/exit1；MODIFIED soon/exit0；ROLLBACK no-expiry/exit1，恢复哈希与原始一致。不是当前候选修复或生产恢复结果。
