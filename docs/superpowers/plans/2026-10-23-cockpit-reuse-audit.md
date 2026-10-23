# Cockpit 整体后台替换：LiteLLM 项目对照与执行计划

> 本次在独立分支 `codex/cockpit-reuse-plan` 执行，起点为本地 HEAD `086c0f391ad6be683ab9ea45cb15cc4222a25ce9`，包含 4 个尚未出现在 `origin/CLIProxyAPI分支` 的既有提交；原 `CLIProxyAPI分支` 引用不移动。每阶段单独验证并提交；用户已明确要求推送此新分支到 GitHub；不部署，除非另行授权

**目标：** 以 Cockpit 作为目标后台，评估并规划由 Cockpit 接管当前号池后台的整体迁移，逐项区分可直接替换、可复用、需适配、需重做和仍待确认的能力。当前额度耗尽换卡、重试和续聊行为是迁移验收基线，不是要求 LiteLLM 永久保留这些后台职责。

**目标架构：** 完成切换后由 Cockpit 后台负责账号池、账号选择、额度/错误处理、重试与故障切换、会话归属和续聊，以及其支持的上游适配；LiteLLM 号池不再作为第二个后台路由 owner。迁移期间保留旧链路仅用于对照和回滚，任何请求只允许一个 active retry/failover owner。是否保留 LiteLLM 或 NewAPI 作为外部 API 入口、下游用户/Key 和计费层，是实施前的边界决策，不在本计划中擅自假定。

**技术栈：** Python / Pydantic，React / TypeScript，CLIProxyAPI / Go；已有单账号容器和号池网关

**审查基线：** LiteLLM `f1289af7b3059b27e7d76a4ce37262658465a3b1`；Cockpit `76fbea02ba8ab5a071205a0bfa59ef4db5cc64b4`；项目固定的 CLIProxyAPI fork `b3c954b63df09156ac2ac52ad038b3dc3a20748f`。比较不代表当前远端最新版本

**范围说明：** 源码对照和已归档的 129 项测试以固定 LiteLLM HEAD `f1289af7b3059b27e7d76a4ce37262658465a3b1` 为准。审查期间 `CLIProxyAPI分支` 已前进至 `a06aa136536fb1d2d55fbd8cfb1aa8fb9a1b2521`：提交 `24b49b9cb0` 固定并加入 managed identity sidecar 补丁，提交 `a06aa13653` 将 capability gate 接入 manager、API model 和 Dashboard。当前本地 HEAD 为 `086c0f391ad6be683ab9ea45cb15cc4222a25ce9`，只新增设置适配验证记录，没有后续运行时代码变化；见 `docs/superpowers/plans/2026-10-23-cockpit-settings-adaptation.validation.md`。第 0 阶段需按当前 HEAD 审阅并重跑相关门，不将旧 HEAD 测试结果当作当前结果。Cockpit 本地 checkout 也有未提交改动；本计划仅使用上述固定 Cockpit 快照，不把其本地凭据或配置当作证据

## 迁移约束

- 在修改前记录当前 HEAD、未提交文件及现有失败；绝不覆盖他人的工作树修改
- 把现有授权范围、额度耗尽换卡/普通限流分类、重试预算、会话亲和性及用量归属逐项记录为兼容基线；最终由 Cockpit 承担这些后台职责，允许的行为差异必须先明确列出并获用户确认
- 单次请求在任何迁移阶段只能有一个路由和重试 owner；切换前不得让 LiteLLM 与 Cockpit 同时重放模型请求
- 续聊迁移必须覆盖同卡和跨卡两种路径、可见历史/工具/图像/流式输出边界；失败状态不明、已输出有效内容或历史不完整时不得无条件重放
- 不迁移数据库行、不重写既有卡片凭据、不在日志或文档中加入密钥、令牌、私有 URL 或个人配置
- 新代码遵守目标仓库的 CLAUDE.md、`account-pool/AGENTS.md`、`account-pool/MAINTENANCE.md` 和适用子目录说明
- Cockpit 源仓整体标注 CC BY-NC-SA 4.0；其中 vendored CLIProxyAPI 标注 MIT。复制 Cockpit 自有文件前，先记录来源、署名和适用许可；商业适用性须另行确认

## 审查结论

| 类别 | 能力 | 结论与后续处理 |
| --- | --- | --- |
| 可直接替换 | 现有号池后台整体 | **目标是整体切换到 Cockpit；目前没有证据证明其任何完整模块可作为 drop-in 直接替换。** 需先验证服务形态、API/数据契约、部署和运行行为，不能把“不能原样复制代码”误读成“不以 Cockpit 替换后台” |
| 可复用但无需再搬 | 固定 CLIProxyAPI fork 中与 Cockpit 捆绑版逐文件完全相同的实现 | 在 1,413 个固定版本 CLIProxyAPI 文件中，1,005 个相同、306 个不同、298 个仅 Cockpit 有、102 个仅 fork 有。需要某项能力时先检查固定 fork；相同文件不得因 Cockpit 也包含它而重复移植 |
| 需适配或重做 | 账号/卡片模型、Key 授权范围、供应商凭据、候选过滤、优先级、额度、重试、冷却、会话归属、用量/计费、管理 API 和 UI | 这些是 Cockpit 接管后台时的主要迁移项；逐项映射 Cockpit 的实际 owner 与项目现有外部契约，不以身份设置适配代替后台替换 |
| 行为兼容验收 | 额度耗尽换卡、普通限流处理、同卡/跨卡续聊、流式重试及工具/图像历史 | 现有 LiteLLM 测试与行为作为迁移基线；切换后由 Cockpit 侧的对应测试证明。不得因原逻辑目前位于 LiteLLM 就预设永久保留其 owner |
| 暂不能判断 | Cockpit 无头 Linux 服务化、多副本/持久化、外层 LiteLLM/NewAPI 边界、跨账号密文续聊、真实上游接受度及商业许可 | 源码和单元测试不足以证明可部署或行为等价；需按后续阶段验证并记录通过、差异或阻断项 |

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
| “整体后台”的精确边界 | 用户已明确目标为整体换成 Cockpit 后台；尚未确认 LiteLLM/NewAPI 的公共 API 入口、下游用户/Key 和计费层是否继续保留 | 实施前确认是替换现有 account-pool Manager+路由数据面，还是进一步替换 LiteLLM 公共 API/网关；对应更新 API、认证和计费验收范围 |
| Cockpit 是否能以生产服务形态运行 | 固定 Cockpit 同时有 Rust 原生引擎和 Go CLIProxyAPI 引擎，但此前源码审阅不等于证明可作为无头 Linux 服务、多副本服务或兼容现有容器编排 | 在隔离环境构建/启动选定引擎，验证健康检查、持久化、重启、配置、日志、升级和备份恢复；不能运行则记为架构阻断 |
| 账号池 Key、NewAPI 卡片范围与 Cockpit 授权模型 | 当前 LiteLLM 卡片 Key 与路由授权可限制候选账号；未证明 Cockpit 有相同的主体隔离与权限语义 | 建立逐接口/逐角色映射并验证越权账号不可被选择；若外层继续负责 Key，验证其到 Cockpit 请求身份的可靠绑定 |
| Cockpit 的额度/限流分类、重试和候选策略 | 文档描述池内选择与错误恢复，但尚未与 LiteLLM 的 quota_exhausted、429、retry budget 和 account exclusion 逐条件对照 | 用现有回归输入在 Cockpit 实现上运行契约测试，逐项记录一致/差异；未批准的差异阻止切换 |
| 同卡和跨卡续聊，尤其 reasoning 密文 | 项目当前保存完整可见历史并有严格重放边界；Cockpit 有本地历史/检查点尝试恢复，但跨账号密文是否被供应商接受仍待真实请求 | 先完成无真实上游的状态机/序列化测试；跨卡密文只在明确批准的隔离账号和低风险请求验证，未通过时保持该路径关闭 |
| Cockpit 支持的供应商、UI/API 和项目能力覆盖 | Cockpit Rust/Go 引擎及当前项目各自适配范围不相同；已有 provider-settings gate 仅是窄范围功能 | 为目标供应商和管理工作流建立功能矩阵，以真实配置消费者和测试为依据；不可用能力列入差异清单 |
| Cockpit 源码许可对目标部署/分发的影响 | Cockpit 自有源整体为 CC BY-NC-SA 4.0，vendored CLIProxyAPI 为 MIT | 复制或分发前由用户确认许可适用性；不能因 Go 子仓 MIT 推断 Cockpit 自有模块同许可 |

## Cockpit 整体替换执行计划

### 替换阶段 A：固定当前行为基线

**目标：** 把当前 LiteLLM 号池的实现和测试作为替换前基线，不把既有通过数误当成 Cockpit 已通过。

- [x] 固定当前分支、Cockpit 与 CLIProxyAPI fork 的审查版本和文件清单
- [x] 已有换卡、账号授权范围、重试、会话与 Responses 续聊的定向回归通过；完整记录见下方历史验证结果及 `.evidence/VERIFICATION.txt`
- [x] 已审阅当前分支的 provider capability gate 与 managed identity 适配；确认它们没有实现后台切换
- [ ] 新增迁移实施前，按最终确定的外层 API 边界重跑旧系统验收集，并导出关键场景的输入/期望输出矩阵

**验收：** 有固定版本、命令、输出和逐场景行为清单；旧系统结果只作为 BASELINE。

### 替换阶段 B：验证 Cockpit 服务化和引擎选择

**目标：** 判断固定 Cockpit 是否能作为目标后台运行，不因桌面应用或单元测试代码存在就假定生产服务能力。

- [x] 审阅固定 Cockpit Rust CLI/core、Tauri 桌面入口与 Go sidecar 的启动入口、HTTP API、账号存储、认证及管理界面依赖；确认 Rust CLI 不是 HTTP 服务，Go sidecar 才是可独立启动的数据面
- [ ] 验证选定 Go sidecar 在目标 Linux/Docker 形态无桌面运行；已加入容器入口、Compose 示例和 `/healthz`，但本机 Docker Linux daemon 不可用，镜像构建及容器内验证仍未完成
- [ ] 验证单实例重启、持久化、备份/恢复和并发访问；多副本、锁和账号一致性作为独立门槛
- [x] 核对 Cockpit 自有代码与 vendored CLIProxyAPI 的许可证及发布义务；Cockpit 自有代码为 CC BY-NC-SA 4.0，vendored CLIProxyAPI 为 MIT，分发前仍需确认适用性

**阶段 B 当前结果（2026-10-23）：** Go sidecar `go test -mod=readonly ./...` 通过，Linux/amd64 无 CGO 交叉构建通过；隔离进程启动后，匿名 `/healthz` 返回 200、有效 API Key 可读模型和额度端点、缺少 API Key 的模型请求返回 401。Compose 文件可解析。Docker daemon 未启动，实际镜像构建未验证；Rust 定向测试因缺少 MSVC `link.exe` 在依赖编译时停止。新增的服务镜像仍读取静态 manifest，尚无账号管理 REST API，因此这不是整体后台替换完成，也未满足阶段 B 全部验收条件。完整命令与输出见 `2026-10-23-cockpit-reuse-audit.evidence/VERIFICATION.txt`。

**验收：** 选定一个可构建、可运行并能持久保存账号状态的后台形态；失败即记录为整体替换阻断项，不以现有 CLIProxyAPI 容器代替证明。

### 替换阶段 C：确认边界并完成能力映射

**目标：** 明确 Cockpit 接管的后台职责与保留的外层系统，逐项映射数据模型和行为。

- [x] 已确认外层边界：LiteLLM 保留公共 API、下游用户/Key、用量统计和现有号池页面；Cockpit 接管账号池运行态、账号选择、上游请求、重试/故障切换和续聊。迁移期间不允许 LiteLLM 与 Cockpit 同时作为请求重试 owner
- [ ] 对照账号/卡片/供应商凭据、卡片 Key 授权范围、模型过滤、候选账号、优先级、额度、错误分类、冷却、重试、会话粘性、用量归属、管理 API 和 Dashboard
- [ ] 每项标为 `直接替换`、`可复用`、`需适配`、`需重做`、`不支持` 或 `未验证`；列出 Cockpit 目标 owner 和对应文件/API/数据表
- [ ] 将 LiteLLM 当前 quota failover 与 conversation tests 改造成 Cockpit 可执行的契约场景，不把 LiteLLM 代码本身当作最终实现

**验收：** 外层入口与责任边界获用户确认；没有未映射的关键能力；不确定项有阻断条件和负责人。

**边界记录（2026-10-23）：** LiteLLM 的客户端入口、Key 鉴权和用量记录保留；现有号池页面保留为管理入口并通过服务端适配器读写 Cockpit。Cockpit 成为模型请求的数据面和唯一换卡/重试 owner。管理适配期间可保留 Manager 作为页面工作流或数据同步桥，但模型请求不能继续调用 Manager 的 `resolve/acquire/finish` 选择账号。供应商凭据与策略从现有数据库导入 Cockpit 的具体映射仍需实现和契约测试。

### 替换阶段 D：实现 Cockpit 后台接管

**前置条件：** 阶段 B 可部署性通过，阶段 C 的边界与能力差异得到确认。此阶段是后续单独实施任务，不代表本计划已改业务代码。

- [ ] 在此独立分支按已确认边界实现 Cockpit 服务/引擎部署、认证绑定和必要的 LiteLLM/NewAPI 适配；Cockpit 成为账号选择、额度/错误处理、重试、故障切换与续聊的唯一后台 owner
- [ ] 为现有卡片 Key 或下游主体建立不可混淆的 Cockpit 身份和账号授权范围；以只读/校验迁移工具先检查映射，不导出明文 token 或覆盖原凭据
- [ ] 按阶段 B 选定的 Rust 或 Go 引擎接入供应商能力；固定源版本、构建产物摘要和上游接口契约
- [ ] 为切换提供显式路由开关和可回滚配置；不得在同一请求上同时启用 LiteLLM 与 Cockpit 重试/重放

**验收：** 新后台能在隔离环境独立处理管理和模型请求；现有账户密钥、权限与审计边界可验证；无未批准的双重重试。

**代码检查点（2026-10-23）：** Cockpit 仓库分支 `codex/cockpit-backend-replacement` 的本地提交 `0d74db2b` 增加了受独立管理员 Token 保护的 manifest/凭据条件写入 API、校验和显式重启入口。它仍以静态 manifest 为运行态输入，要求服务重启；LiteLLM 管理适配、账号/凭据导入和模型请求接线尚未实现，因此不能据此切流或声称整体替换完成。本轮不构建镜像、不部署。

### 替换阶段 E：行为兼容与差异验收

- [ ] 额度耗尽时仅在请求主体/卡片获授权范围内换卡；普通限流按明确策略处理，不误判为额度耗尽
- [ ] 验证同卡会话归属与续聊、跨卡恢复门槛、可见历史重建、图片、工具调用/结果、instructions、stream 和 compaction
- [ ] 验证首个有效输出后的错误、工具已执行、请求状态不明和历史不完整等情况不会错误重放；不得声称跨账号 reasoning 密文已受上游接受，除非有隔离真实请求证据
- [ ] 验证重试预算、冷却、并发、用量/计费、错误日志和管理 UI；逐个供应商运行实际支持矩阵
- [ ] 标记所有与 LiteLLM 基线不同的行为，逐项记录是否接受；关键差异未获确认则不切流

**验收：** Cockpit 后台的契约测试通过；关键行为等价或差异已获用户明确接受；不存在重复请求、越权换卡或静默丢失续聊上下文。

### 替换阶段 F：隔离切换、回滚与生产决策

- [ ] 先在隔离环境执行账号映射 dry-run、单卡请求、额度失败、同卡续聊、跨卡受控用例和配置回滚演练
- [ ] 只有得到单独的部署授权后才触达生产；真实供应商请求的额度成本、数据范围和账号授权另行确认
- [ ] 采用小范围切流；监测成功率、错误分类、重试次数、用量、会话恢复和 Cockpit 持久化状态
- [ ] 回滚时将入口切回原后台并验证账号路由/会话行为；保留两边审计记录，不自动删除账号凭据或历史

**验收：** 有固定镜像/提交、数据备份、切流指标、回滚命令及回滚后验证；没有未经确认的生产部署。

## 既有窄范围适配记录（仅作历史证据）

> 以下阶段记录的是此前身份与供应商设置适配，以及原 LiteLLM 行为的回归结果；它们不是 Cockpit 整体后台替换的实施顺序，也不能证明 Cockpit 已接管请求路由。

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
