# LiteLLM 与 Cockpit 对照审查及执行计划

> 本次在独立分支 `codex/cockpit-reuse-plan` 执行，起点为本地 HEAD `086c0f391ad6be683ab9ea45cb15cc4222a25ce9`，包含 4 个尚未出现在 `origin/CLIProxyAPI分支` 的既有提交；原 `CLIProxyAPI分支` 引用不移动。每阶段单独验证并提交；用户已明确要求推送此新分支到 GitHub；不部署，除非另行授权

**目标：** 以代码证据区分 Cockpit 能力中可复用、须适配、不可替换和仍待确认的部分，并在所有后续改动中保住当前号池的额度换卡与续聊行为

**架构：** LiteLLM 继续拥有卡片 Key 授权范围、账号选择、额度和错误分类、重试及故障切换、计费和续聊。CLIProxyAPI 继续作为单账号上游适配器。只把 Cockpit 中边界清楚的实现作为固定版本的参考；不引入第二套号池或网关

**技术栈：** Python / Pydantic，React / TypeScript，CLIProxyAPI / Go；已有单账号容器和号池网关

**审查基线：** LiteLLM `f1289af7b3059b27e7d76a4ce37262658465a3b1`；Cockpit `76fbea02ba8ab5a071205a0bfa59ef4db5cc64b4`；项目固定的 CLIProxyAPI fork `b3c954b63df09156ac2ac52ad038b3dc3a20748f`。比较不代表当前远端最新版本

**范围说明：** 源码对照和已归档的 129 项测试以固定 LiteLLM HEAD `f1289af7b3059b27e7d76a4ce37262658465a3b1` 为准。审查期间 `CLIProxyAPI分支` 已前进至 `a06aa136536fb1d2d55fbd8cfb1aa8fb9a1b2521`：提交 `24b49b9cb0` 固定并加入 managed identity sidecar 补丁，提交 `a06aa13653` 将 capability gate 接入 manager、API model 和 Dashboard。当前本地 HEAD 为 `086c0f391ad6be683ab9ea45cb15cc4222a25ce9`，只新增设置适配验证记录，没有后续运行时代码变化；见 `docs/superpowers/plans/2026-10-23-cockpit-settings-adaptation.validation.md`。第 0 阶段需按当前 HEAD 审阅并重跑相关门，不将旧 HEAD 测试结果当作当前结果。Cockpit 本地 checkout 也有未提交改动；本计划仅使用上述固定 Cockpit 快照，不把其本地凭据或配置当作证据

## 全局约束

- 在修改前记录当前 HEAD、未提交文件及现有失败；绝不覆盖他人的工作树修改
- 额度耗尽换卡必须留在卡片 Key 获授权的账号范围内；一般限流不得误判成额度耗尽
- LiteLLM 号池是账号路由和重试的唯一 owner；同步给 CLIProxyAPI 的请求重试保持关闭
- 只在当前尝试尚未输出首个有效内容且携带完整可安全重建历史时切换续聊账号；保留既有工具结果，但绝不重复执行工具或丢弃图像、会话参数和请求归属信息
- 不迁移数据库行、不重写既有卡片凭据、不在日志或文档中加入密钥、令牌、私有 URL 或个人配置
- 新代码遵守目标仓库的 CLAUDE.md、`account-pool/AGENTS.md`、`account-pool/MAINTENANCE.md` 和适用子目录说明
- Cockpit 源仓整体标注 CC BY-NC-SA 4.0；其中 vendored CLIProxyAPI 标注 MIT。复制 Cockpit 自有文件前，先记录来源、署名和适用许可；商业适用性须另行确认

## 审查结论

| 类别 | 能力 | 结论与后续处理 |
| --- | --- | --- |
| 可直接替换 | 整个账号池、API 服务、代理或 Dashboard | **无。** 两边的账户模型、运行进程和所有权边界不同，替换会改变路由、配置或生命周期语义 |
| 可复用但无需再搬 | 固定 CLIProxyAPI fork 中与 Cockpit 捆绑版逐文件完全相同的实现 | 在 1,413 个固定版本 CLIProxyAPI 文件中，1,005 个相同、306 个不同、298 个仅 Cockpit 有、102 个仅 fork 有。需要某项能力时先检查固定 fork；相同文件不得因 Cockpit 也包含它而重复移植 |
| 可窄范围适配 | Codex 安装级身份资料以及严格限定的安装标识改写 | Cockpit 固定版本有独立存储和 Go 请求改写模块，但不提供 LiteLLM 的卡片、策略或权限接口。可评估适配；不得把安装标识误当作会话迁移或跨卡解密 |
| 只借鉴、不复制 owner | 账号池 Key 范围、候选过滤、优先级、额度、重试、冷却、会话粘性和用量归属 | 复用安全不变量和测试案例，继续由 LiteLLM 网关执行；不能再叠加 Cockpit Selector 或第二个 retry loop |
| 必须保留 | LiteLLM 的额度分类、卡片换卡门槛及 Responses 续聊恢复 | 在替换或迁移得到独立批准、逐项兼容和回滚验证之前，保留现实现与现有测试 |
| 待确认 | 跨账号密文、实际供应商接受度、当前工作树适配状态、完整设置消费链 | 代码和文档不足以证明线上行为；按后续验证门逐项关闭，不得在设置页宣称已生效 |

### 代码依据

- 当前号池与 OpenAI-compatible 通道分离，后者单独校验目标 URL，不能假设每张卡都会进入 CLIProxyAPI：`account-pool/account_pool/channels/registry.py`、`account-pool/account_pool/channels/openai_compatible.py` 的 `_validate_destination`
- Manager 将 CLIProxyAPI 的 `request-retry` 设为 0，网关单独持有卡片重试策略：`account-pool/account_pool/channels/cliproxyapi/settings_sync.py` 的 `apply_global_settings`、`apply_policy`、`_gateway_retry_settings`；`account-pool/tests/account_pool/test_cliproxy_supplier_client.py` 的 `test_settings_and_policy_preserve_single_retry_owner`
- 换卡的错误分类区分 `quota_exhausted` 和可在同卡重试的 `rate_limit`：`litellm/proxy/management_endpoints/account_pool_failover.py` 的 `classify_failure`；补充读 `test_account_pool_failover.py`、`test_account_pool_gateway.py` 和 `test_account_pool_quota_failover.py`
- Responses 恢复会检查历史完整性、签名状态、compaction、图像、工具结果和流式输出边界：`litellm/proxy/management_endpoints/account_pool_conversation.py` 的 `Conversation._history_replayable`、`can_replay`、`rebuild`；`tests/test_litellm/proxy/management_endpoints/test_account_pool_conversation.py`
- Cockpit Codex 身份资料使用单独的 SQLite 持久化，并经 `ApplyManagedIdentity` 改写已知安装字段：Cockpit `src-tauri/src/modules/codex_api_identity.rs`、`sidecars/cockpit-cliproxy/third_party/CLIProxyAPI/internal/runtime/executor/helps/managed_identity.go`
- Cockpit 自带的旧指纹模式和客户端限制有明确“不改变请求／不限制客户端”的回归用例：Cockpit CLIProxyAPI `codex_fingerprint_test.go`、`codex_client_policy_test.go`。不能把这些弃用字段的行为当作可覆盖 LiteLLM 网关策略的证据
- 固定 CLIProxyAPI fork 已含 Claude 指纹解析和 mid-system-message 配置逻辑；对应文件与 Cockpit vendored 文件在该基线相同：`internal/runtime/executor/claude_fingerprint_policy.go`、`internal/runtime/executor/claude_signing.go`。因此先核对配置写入和消费，不搬重复执行代码
- Anthropic auth-file 管理文件整体并不相同；Kimi executor 也有差异，不能据少量同名字段推断整个供应商兼容：使用 `cliproxy_file_comparison.json` 和 `critical_snippets.json` 对照固定文件

## 仍待确认

| 问题 | 当前证据能说明什么 | 关闭条件 |
| --- | --- | --- |
| `managed` 身份是否跨重启、刷新和多 worker 稳定 | 固定 Cockpit 代码有版本化身份资料和 revision；两边运行时、持久化 owner 不同 | 在 LiteLLM 实际 CLIProxyAPI 镜像上验证 create/read/update/restart/refresh/concurrency，并证明不同卡片隔离 |
| Claude / Kimi 等供应商设置是否在真实请求中生效 | UI -> API -> policy persistence -> CLIProxyAPI auth/config 路径已静态核对；Kimi 固定源中 `fingerprint_profile` 只作用于 Claude Messages，设备 ID 写入 `X-Msh-Device-Id` | 在有 Go toolchain 的环境运行固定 CLIProxyAPI tests；经用户确认后做隔离真实上游请求。现阶段不把静态路径当作线上验收 |
| Cockpit 是否能在真实上游接受跨账号 reasoning 密文 | Cockpit README 明确说跨账号密文接受度仍需真实请求验证；不能由本地测试推出 | 使用明确授权的测试账号和无敏感历史用例进行单独 E2E；没有结果前默认禁止把密文迁移写作已支持 |
| Docker、真实数据库、多副本和真实供应商端到端 | 本轮的 129 项 Manager 与 Go 单元测试都不是这些环境验证 | 在隔离环境完成带版本和可回滚数据快照的验收；无法运行时记为未验证，不降级安全规则 |
| Cockpit 自有 Rust/Tauri 文件在目标用途下能否复制 | 文件许可及与 LiteLLM 生命周期的兼容审查未完成 | 逐个目标文件审许可证、依赖、平台行为、凭据存储和维护 owner；在此之前只使用行为参考 |

## 执行计划

### 阶段 0：审阅已提交的身份适配

**目标：** 对照当前分支已经提交的身份适配检查行为、调用链和未验证部分，不重复搬代码或把提交误报成已完成验收

**审阅文件：**

- `account-pool/account_pool/channels/cliproxyapi/client.py`
- `account-pool/account_pool/channels/cliproxyapi/settings_sync.py`
- `account-pool/account_pool/policies.py`
- `litellm/proxy/management_endpoints/account_pool_management_models.py`
- `ui/litellm-dashboard/src/features/account-pool/components/providers/AccountPoolProviderPolicyFields.tsx`
- 上述文件对应的未提交测试、locale 和 API type 文件

- [x] 审阅 `24b49b9cb0` 与 `a06aa13653` 的完整差异，重点检查 `account-pool/cliproxy-adapter/` 补丁、source lock、`client.py` 能力检测、`settings_sync.py` 字段同步、Manager/API/Dashboard gate 及对应新测试
- [x] 在当前 LiteLLM 根目录运行 `account-pool/tests/account_pool/test_policies.py`、`test_cliproxy_supplier_client.py`、`test_channel_registry.py` 和 `test_management.py`；另运行阶段 3 的换卡与续聊回归，不复用旧 HEAD 的通过数
- [x] 确认底层能力缺失时策略保存/同步会明确失败或报告不支持；检查 managed identity 不扩展至 conversation/session 密文，`request-retry=0` 与单一重试 owner 保持不变
- [x] 将当前 HEAD、命令、完整结果和未通过范围追加到证据记录；若发现缺口，只补最小差异，不从 Cockpit 重新实现已有逻辑

**验收：** 有一份基于 `a06aa13653` 实际提交的变更与回归清单；旧提交的审查证据与新 HEAD 验收结果分开；不重复实现已提交的适配

**阶段 0 执行摘要（2026-10-23，HEAD `086c0f391ad6be683ab9ea45cb15cc4222a25ce9`）：** Manager 定向测试 `173 passed`，Dashboard 设置集成测试 `13 passed`。Phase 3 六文件回归第一次因缺少 `PYTHONPATH=account-pool` 出现 1 个导入失败、430 项通过；补上该路径后完整重跑为 `431 passed, 9 warnings`，退出码 0，没有断言失败。能力门控、默认关闭、`request_retry=0` 和单一重试 owner 均有当前测试覆盖；无额度路由或 conversation 运行时代码改动。LiteLLM 会保存所选策略，但底层不支持时将其标记 `failed` 并返回 502，明确告知尚未生效。固定 CLIProxyAPI 源码不在当前工作区，Go 补丁测试只引用历史结果，未冒充本次新运行。详细命令、结果和边界见同目录 `.evidence/VERIFICATION.txt`

### 阶段 1：建立设置能力矩阵，不搬整模块

**修改边界：** 若需补功能，仅改 `account-pool/account_pool/policies.py`、`settings.py`、`channels/cliproxyapi/settings_sync.py`、对应代理 management model、`AccountPoolProviderPolicyFields.tsx` 及必要测试。保持现有目录和兼容入口

- [x] 将专属策略字段登记为供应商、默认值、保存位置、执行 owner、能力状态和测试；无专属字段的供应商返回并显示 `unsupported`
- [x] 覆盖 8 个 CLIProxyAPI supplier 定义与 OpenAI-compatible 通道，共 9 个供应商类别；没有把 provider-specific policy 扩展到通用兼容通道
- [x] 对 Claude 检查 `fingerprint_profile`、`cloak_mode`、`rebuild_mid_system_message` 的 policy/auth-file 映射及固定源消费路径；没有复制 Claude 执行文件
- [x] 对 Kimi 核对 `fingerprint_profile` 与固定源 `resolveKimiDeviceID` 的来源、回退和 `X-Msh-Device-Id` 使用；设备 ID 属于现有认证状态，不以 Cockpit 身份字段替换
- [x] 对 Codex 核对 fingerprint 默认值、`identity_confuse`、CLI/app-server 准入、Compact、WebSocket 和当前 owner；弃用 metadata 不覆盖网关策略
- [x] 对 Antigravity、xAI、Gemini、Gemini Interactions、Vertex 核对配置与执行点；无专属策略字段的供应商标记 `unsupported`
- [x] 保留 OpenAI-compatible `_validate_destination` 目标校验和直连路由，不切入 CLIProxyAPI 容器

**针对性验证：**

```powershell
.venv/Scripts/python.exe -m pytest account-pool/tests/account_pool/test_policies.py account-pool/tests/account_pool/test_cliproxy_supplier_client.py account-pool/tests/account_pool/test_channel_registry.py account-pool/tests/account_pool/test_openai_compatible.py -q
```

Dashboard 有修改时，在 `ui/litellm-dashboard`：

```powershell
npx vitest run --project unit --project component --project integration src/features/account-pool src/components/Settings/RuntimeSettings/RuntimePolicyDialog.integration.test.tsx --maxWorkers 2
npm run test:types
npm run gen:api
```

运行 API type 生成后审阅 `src/lib/http/schema.d.ts`，只提交本次生成的真实差异

**验收：** 每个字段都有一个明确运行时消费者或明确的不支持状态；默认行为不变；不存在 manager 与 sidecar 两边各自重试

**阶段 1 执行结果（2026-10-23）：** 逐字段详表见 `account-pool/cliproxy-adapter/providers.md`。专属 `provider_settings` 对 Codex、Claude、Kimi、xAI、Antigravity 为 `gateway`，对 OpenAI-compatible、Gemini、Gemini Interactions、Vertex 为 `unsupported`；“unsupported”不撤销各通道的认证/凭据能力，也不影响通用网关策略。修正了 Manager 与代理响应模型的 capability 映射，并让 Dashboard 的 `unsupported` 状态优先于固定说明显示。默认值和 quota/failover/conversation 逻辑未改。

本阶段原始验证：阶段 1 Python 回归 `124 passed`；policy/API 定向测试 `5 passed`；Dashboard `RuntimePolicyDialog` 集成 `14 passed`；Dashboard type tests `4 passed, no type errors`。本次独立工作树续做时重跑四个指定后端测试文件，`144 passed`，`3.17s`。本机没有 Go toolchain，固定 CLIProxyAPI 的 Go tests 未重跑；真实 provider 请求、Docker、PostgreSQL 和跨账号密文接受度仍未验证。没有响应 schema 形状变化，因此未运行 `gen:api`。

### 阶段 2：单独完成 Codex managed installation identity

**目标：** 如阶段 0 证明仍有缺口，只适配安装级标识，不搬入 Cockpit 号池或本地历史库

**实现范围：** `account-pool/cliproxy-adapter/` 的固定来源与小补丁、CLIProxyAPI capability/client/settings sync、manager `CodexPolicy` 与 API model、已有 Dashboard policy 控件及映射测试。实际文件以阶段 0 的调用链核实为准

- [ ] 固定 Cockpit commit、CLIProxyAPI fork commit、相关文件 SHA-256 与许可来源；以 `account-pool/cliproxy-adapter/sources.json` 为版本锁，不使用浮动分支
- [ ] 先写会失败的 managed 能力缺失测试、字段精确映射测试和 refresh/restart 稳定性测试；底层不支持时在写配置前拒绝，并报告 `unsupported/partial`
- [ ] 复用已有的版本化卡片持久标识作为单卡 scope；持久化 profile 必须有 owner、版本和 revision，令牌刷新不能意外重置身份；不同卡片及不同主体不能共享安装 ID
- [ ] 改写只限明确允许的 installation 字段及 header；不改 session、conversation、thread、turn、prompt cache、工具调用、图像、response ID 或 encrypted content
- [ ] `managed` 只对 Codex 明确启用；既有 `off/device/session/full` 和 `identity_confuse` 的默认与作用范围不变，不把两种机制叠加成隐式替换
- [ ] 验证普通 HTTP、SSE、WebSocket、图片和 Compact 共同请求路径在同一卡片下得到稳定身份，响应/客户端可见状态和安全日志无 token 泄漏
- [ ] 保持 `request-retry=0` 与 `max-retry-credentials=1` 的 sidecar 约束；任何身份查询失败都不尝试另一个上游账号或重发已执行请求

**回归命令：** 先运行阶段 1 的 Manager/前端命令，再运行本计划固定 fork 的 `go test` 中 `^TestManagedIdentity`、HTTP/SSE/WebSocket 请求边界测试；Go 必须使用仓库固定工具链和完整 embed 输入。最后运行阶段 3 的全部换卡与续聊测试

**验收：** 缺能力会失败而非假成功；identity 跨刷新/重启保持卡片归属；session/reasoning 密文逐字节不因此设置变化；没有新增重试 owner

### 阶段 3：证明额度换卡和续聊没有退化

**禁止范围：** 不改 `account_pool_failover.py` 的分类优先级、`Conversation` 的恢复门槛、重试预算默认值或账户排除规则，只为采用 Cockpit 命名而改协议字段

- [x] 额度耗尽、无可用卡和普通瞬时 429 的既有换卡分类与同卡重试
- [x] 下一张卡仍限定在该卡片 Key 已授权范围内
- [x] 完整 Responses 历史、图像、可见工具调用/结果、instructions 和请求参数的恢复路径
- [x] 不完整历史、`previous_response_id` 和失败后状态不明请求的不可重放限制
- [x] stream 首个有效输出后的禁止换卡/重放边界
- [x] compaction 只从完整父历史恢复，失败不覆盖已存响应或会话归属
- [x] 卡片 Key 切换门及工具、图像、WebSocket、native Messages 的重放限制

**文件与回归：**

- `tests/test_litellm/proxy/management_endpoints/test_account_pool_failover.py`
- `tests/test_litellm/proxy/management_endpoints/test_account_pool_quota_failover.py`
- `tests/test_litellm/proxy/management_endpoints/test_account_pool_gateway.py`
- `tests/test_litellm/proxy/management_endpoints/test_account_pool_conversation.py`
- `tests/test_litellm/proxy/management_endpoints/test_account_pool_native_routing.py`
- `tests/test_litellm/proxy/management_endpoints/test_account_pool_accounting.py`
- `account-pool/tests/account_pool/test_cliproxy_supplier_client.py` 的单一重试 owner 测试

```powershell
$gatewayTests = @(
  'tests/test_litellm/proxy/management_endpoints/test_account_pool_failover.py',
  'tests/test_litellm/proxy/management_endpoints/test_account_pool_quota_failover.py',
  'tests/test_litellm/proxy/management_endpoints/test_account_pool_gateway.py',
  'tests/test_litellm/proxy/management_endpoints/test_account_pool_conversation.py',
  'tests/test_litellm/proxy/management_endpoints/test_account_pool_native_routing.py',
  'tests/test_litellm/proxy/management_endpoints/test_account_pool_accounting.py'
)
.venv/Scripts/python.exe -u -m pytest @gatewayTests -q
```

**验收：** 上述回归通过；若身份适配触及任何失败边界，先修复或回退该适配，不调整额度分类、重试或续聊语义来迎合 Cockpit

**阶段 3 执行结果（2026-10-23）：** 在当前 `codex/cockpit-reuse-plan` checkout、设置 `PYTHONPATH=account-pool` 后，阶段 3 六个指定文件及 `test_settings_and_policy_preserve_single_retry_owner` 一并运行，`439 passed, 8 warnings`，退出码 0；没有修改 `account_pool_failover.py`、conversation 恢复门槛、重试预算或账户排除规则。

### 阶段 4：部署前验证与分阶段提交

- [ ] 从 `deploy/UPDATE_RUNBOOK.md` 开始部署准备；本阶段不实际部署，除非用户另行明确授权
- [ ] 在隔离环境验证真实 Manager、LiteLLM、PostgreSQL 和 CLIProxyAPI 容器的 schema、restart、single-card request、额度拒绝和续聊；记录镜像 SHA/digest、配置版本、测试账号范围及是否真实触达上游
- [ ] 先完成不消耗额度的路由和状态检查；任何真实供应商请求另行确认成本、测试数据和权限，跨账号密文 E2E 未证明前禁用该场景
- [ ] 每次只提交一个可回退职责；保留前后镜像和配置。功能开关默认关闭；回退适配后保留可审计数据，不删除已有凭据或历史
- [ ] 按 `account-pool/MAINTENANCE.md` 做 Manager、网关和 Dashboard 定向检查；记录 skipped tests、外部环境限制与已存在失败，不能引用本计划日期之前的测试通过数作为新结果
- [ ] 逐份检查待提交 diff、格式和 `git diff --check`，避免把 `.local/`、本地配置、身份数据和生成产物带入提交
- [ ] 验证完成后依项目规则 commit；只在收到用户明确 push 命令后 push

**验收：** 有测试范围、具体提交、固定镜像身份、回滚步骤和未验证事项；没有未授权部署或推送

## 当前可引用的验证证据

以下只是固定快照上的定向结果，不代表当前未提交工作树、完整后端或线上部署：

- LiteLLM 固定 HEAD `f1289af7b3`：`test_policies.py`、`test_cliproxy_supplier_client.py`、`test_channel_registry.py`、`test_quota.py` 共 **129 passed**；本轮用修正后的 pytest 插件入口复跑，退出码 0，`2.17s`
- Cockpit 固定 commit `76fbea02...` 的 vendored CLIProxyAPI：Go `TestManagedIdentity` 定向 suite 使用固定完整 toolchain 和 embed 文件运行成功，退出码 0；覆盖验证/旧模式、精确路径与不透明历史、重复字段与 multipart、刷新主体一致性和选中 bearer 校验
- 上述 Manager 测试不运行 Docker、生产数据库或真实上游；Go 单元测试也不证明 LiteLLM 与该身份适配已集成
- 审计期间有一次无效 pytest 命令因 `pytest_asyncio` 插件未加载退出码 4；修正插件后才是上述通过结果。一次初始 Go 尝试因系统 Go toolchain/embed 不完整失败；随后使用固定工具链和完整源码输入的运行才通过

当前分支续做结果与上述历史快照分开记录，具体命令、失败尝试、literal outputs、退出码和角色哈希追加在 `.evidence/VERIFICATION.txt`。

## 证据附件

证据目录：`docs/superpowers/plans/2026-10-23-cockpit-reuse-audit.evidence/`

- `source_inventory.json`：固定仓库 HEAD、文件清单与审计范围
- `litellm_snapshot.json`、`cockpit_snapshot.json`、`cliproxy_snapshot.json`：各固定源码快照文件 SHA-256
- `cliproxy_file_comparison.json`：Cockpit vendored CLIProxyAPI 与固定 fork 的逐文件同异统计
- `evidence_index.json`：LiteLLM 与 Cockpit 关键代码锚点
- `critical_snippets.json`、`cross_reference_matches.json`：包含 Cockpit 源码摘录，仅保留本地，不推送到 GitHub；待许可适用性确认后再决定是否发布。远端分支通过固定提交和文件锚点复核源码，不依赖这些摘录
- `manager_check_resume.json`、`cockpit_go_check_retry.json`、`cockpit_go_check_verbose.json`：可复核测试命令、输出和退出码
- `working_tree_guard.json`：生成计划前已知的本地 tracked diff 文件 SHA-256；不包含 untracked secrets
- `../cliproxy-adapter/providers.md`：9 类供应商专属策略的默认值、保存与执行 owner、unsupported 状态及未实测边界

审计依据是源码和列明的测试，不是生产服务实测。发现固定快照之外的上游新版本时，先重新取证和对照，再决定是否更新结论
