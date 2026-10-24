# Cockpit 整体后台替换：LiteLLM 项目对照与执行计划

> 本次在独立分支 `codex/cockpit-reuse-plan` 执行，起点为本地 HEAD `086c0f391ad6be683ab9ea45cb15cc4222a25ce9`，包含 4 个尚未出现在 `origin/CLIProxyAPI分支` 的既有提交；原 `CLIProxyAPI分支` 引用不移动。每阶段单独验证并提交；此前已创建远端隔离分支；本轮按最新要求只写代码和本地提交，不推送、不构建镜像、不部署

**目标：** 以 Cockpit 作为目标后台，评估并规划由 Cockpit 接管当前号池后台的整体迁移，逐项区分可直接替换、可复用、需适配、需重做和仍待确认的能力。当前额度耗尽换卡、重试和续聊行为是迁移验收基线，不是要求 LiteLLM 永久保留这些后台职责。

**目标架构：** 完成切换后由 Cockpit 后台负责账号池、账号选择、额度/错误处理、重试与故障切换、会话归属和续聊，以及其支持的上游适配；LiteLLM 号池不再作为第二个后台路由 owner。迁移期间保留旧链路仅用于对照和回滚，任何请求只允许一个 active retry/failover owner。已确认保留 LiteLLM 作为公共 API 入口、下游用户/Key 和用量统计层，现有号池页面经服务端管理适配器连接 Cockpit。

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

**前置条件：** 代码和离线契约测试按用户“先写代码”的要求推进；镜像、容器、真实上游验证仍是后续切流门槛，不能以单元测试代替。阶段 C 边界已确认，未映射的身份、授权范围和数据面行为必须先实现并验收。

- [ ] 在此独立分支按已确认边界实现 Cockpit 服务/引擎部署、认证绑定和必要的 LiteLLM/NewAPI 适配；Cockpit 成为账号选择、额度/错误处理、重试、故障切换与续聊的唯一后台 owner
- [ ] 为现有卡片 Key 或下游主体建立不可混淆的 Cockpit 身份和账号授权范围；以只读/校验迁移工具先检查映射，不导出明文 token 或覆盖原凭据
- [ ] 按阶段 B 选定的 Rust 或 Go 引擎接入供应商能力；固定源版本、构建产物摘要和上游接口契约
- [ ] 为切换提供显式路由开关和可回滚配置；不得在同一请求上同时启用 LiteLLM 与 Cockpit 重试/重放

**验收：** 新后台能在隔离环境独立处理管理和模型请求；现有账户密钥、权限与审计边界可验证；无未批准的双重重试。

**代码检查点（2026-10-23）：** Cockpit 仓库分支 `codex/cockpit-backend-replacement` 的本地提交 `0d74db2b` 增加了受独立管理员 Token 保护的 manifest/凭据条件写入 API、校验和显式重启入口。它仍以静态 manifest 为运行态输入，要求服务重启；LiteLLM 管理客户端与只读状态适配已在下方 D1 完成；账号/凭据导入、管理操作同步和模型请求接线尚未实现，因此不能据此切流或声称整体替换完成。本轮不构建镜像、不部署。

### D1 已完成：服务间管理客户端与安全状态接口

代码提交：`c90f67aaa6fe4486a5687963368ca9ea68124504`，分支 `codex/cockpit-reuse-plan`，实现基线 `533fbbc1f53e6d13c10d54b6688bb5053b05ed4a`。Cockpit 仍锁定本地 `0d74db2b1cf8fe42257ed934ad27e861b79efded`；既有 workflow 修改保留未动

- [x] `account-pool/account_pool/cockpit_admin.py` 提供 manifest 读取/条件写入、凭据 metadata 读取/条件创建/替换/删除、显式重启；失败作为脱敏结果返回，不自动重试、不跟随重定向、不自动重启，未知 manifest 字段 roundtrip 保留
- [x] Manager 新增 `ACCOUNT_POOL_COCKPIT_ADMIN_URL` 与 `ACCOUNT_POOL_COCKPIT_ADMIN_TOKEN`，必须同时配置；Token 至少 32 字符，URL 为不含凭据/path/query/fragment 的 HTTP(S) origin。默认关闭；启动失败或退出时关闭共用客户端
- [x] Manager `GET /api/cockpit/status` 与 LiteLLM admin-only `GET /account_pool/cockpit/status` 只提供 configured、账号/启用 Key/凭据计数、persisted manifest revision，明确 `runtime_activation=restart_required_or_unverified`，响应 no-store。浏览器没有 raw manifest、凭据或 restart 写入口
- [x] 现有配置/管理/代理测试 355 项通过；在此基础上客户端补充检查 40 项通过，二者有重叠不能相加。旧 failover、quota failover、gateway、conversation、native routing、accounting 六文件 431 项通过。新增客户端 strict Ruff 与类型检查无错误，改动文件 Ruff 和 diff check 通过
- [x] 同输入接口事务：BASELINE 404，MODIFIED 200 且只含安全摘要，ROLLBACK 404；回滚副本 hash 等于原文件。文档事务沿用既有四角色，原始命令/输出见证据

限制：以上为离线/mock 契约，不是运行中 Cockpit、PostgreSQL、容器、浏览器 UI 或真实供应商证明。独立审阅代理连续遇到加密上下文启动错误，未产生审阅结论；本轮只完成主执行者复查和自动检查。未执行镜像构建、部署、push 或凭据迁移，默认模型请求链路与页面未切换

### 管理员可配置策略执行约定（2026-10-23）

本节采用用户本轮确认的整合方向，扩充 D2 的实现和验收要求，不宣称下面的策略已经接入。只在既有隔离分支编写、测试和本地提交代码，不构建镜像、不推送、不部署

**唯一执行方：** 完成迁移后，Cockpit 负责路由、重试、额度换卡和续聊决策；LiteLLM 负责公共 API、下游用户认证/授权与标准用量统计。Manager 可以承担页面工作流和同步，不得作为第二套模型请求选卡/重试后台。不能将当前 D1 只读状态接口当作策略编辑已完成

**管理入口：** 优先沿用现有号池页面，通过 LiteLLM 管理鉴权和服务端适配器读写 Cockpit 策略；浏览器不持有 Cockpit 管理 Token，不直接编辑 raw manifest。如果以后同时开放 Cockpit 管理界面，两个管理界面必须读写同一份版本化策略，不能各自写一份配置再双向覆盖。持久化 revision 与运行中 revision 分开，冲突返回 409/412 并要求重新读取，不以最后写入者无条件覆盖

| 管理员配置目标 | 现有 LiteLLM 字段或行为 | Cockpit 接管时必须实现与验收 |
| --- | --- | --- |
| 卡片启用状态 | 卡片启用/停用、可用性 | 当前 accountSpec 没有同名启用字段，须补目标运行态语义；停用卡立即退出新请求候选，亲和性不能重新选中它；不擅自中止在途请求 |
| 候选账号范围 | RoutingPolicy.preferred_account_ids、卡片 Key scope | preferred 不等于授权范围；候选范围只能缩小，不能扩大主体授权范围。空候选、全部停用或不支持供应商时明确拒绝，不退回整个账号池；Cockpit apiKeySpec.accountIds 是授权映射输入，必须与认证主体绑定 |
| 优先级、权重、备用卡 | RoutingPolicy.priority/weight/is_backup | Cockpit customRoutingRules 有对应字段，但须核验排序方向、权重算法和备用卡触发条件；不把字段同名视为行为等价。按作用域配置不能被全局规则覆盖 |
| 路由策略 | RoutingPolicy.strategy、AccountPoolSettings.default_route | 与 Cockpit routingStrategy/customRoutingRules 逐枚举匹配；未支持的 plan/expiry/custom 或供应商组合明确禁用，禁止默默改成 auto |
| 是否故障换卡 | RoutingPolicy.fallback_enabled、额度换卡规则 | 区分额度耗尽与普通限流/可恢复故障；关闭对应换卡策略时返回明确错误，不偷偷切其他卡。移植前保留两类现有开关语义，不将它们合并成一个未经确认的新默认 |
| 重试次数和退避 | RoutingPolicy.max_attempts/retryable_statuses/backoff_ms，request_retry | 重试次数不包含首次请求；max_attempts 是包含首次的总尝试数，映射显式转换。限定次数、总时限和可恢复错误，0 次不做额外重试；重试与跨卡尝试共享 Cockpit 的单一预算，LiteLLM 不再叠加 |
| 冷却策略 | max_retry_interval、手工冷却、供应商 retry-after/额度重置 | 退避上限不等于账号冷却时长。分别建模手工停用、普通限流和额度耗尽；配置不能绕过上游强制等待或把尚未恢复的账号标为 ready |
| 会话亲和性 | RoutingPolicy.session_affinity/session_affinity_ttl | 明确 scope、TTL、账号失效与过期清理；亲和性不扩展授权。安全历史不完整或账号切换不满足恢复条件时拒绝重放，而不是为保持会话强行请求 |

字段依据：`account-pool/account_pool/policies.py` 的 RoutingPolicy、`account-pool/account_pool/settings.py` 的 request_retry/default_route/max_retry_interval，以及 Cockpit `sidecars/cockpit-cliproxy/manifest_policy.go` 的 apiKeySpec/accountSpec/customRoutingRule。这里只记录映射起点；每个选项还须定位实际执行函数和供应商覆盖范围

**能力与生效：** 为每个配置项返回供应商、支持状态、当前值、限制和不支持原因；不支持的选项禁止保存，服务端同样拒绝。页面区分已保存、待生效、已生效；现有 manifest 写入需要重启，必须展示待生效并提供受保护的显式应用操作，不能静默重启或虚报热更新。实现运行中 revision 的读取/确认前，不得显示已生效。修改受支持的策略不需要再改代码，但不等于任何修改都立即生效

**硬性边界：** 不得提供绕过安全边界的设置。主体无权使用的卡片永远不可进入候选；已输出有效内容、工具已经执行、上游执行状态不明或上下文不完整时，不得强制重放。即使直接调用管理 API 绕过前端，Cockpit 运行时也必须再次执行授权和可重放性检查

**按原 D2 顺序追加验收：**

- [x] D2.1 只读映射预检（不代表可迁移）：先写越权候选、空 scope、停用账号、供应商不匹配和策略枚举不支持的失败测试；输出缺失映射，不修改凭据或运行配置
- [ ] D2.2 管理同步：扩展 `account-pool/account_pool/cockpit_admin.py` 与独立策略编排模块，接入 `management_api.py`；在 LiteLLM `account_pool_management.py` 和现有 `AccountPoolManagementApi.ts`、策略表单中复用管理入口。先验证双管理员版本冲突、不支持选项拒绝、部分写入补偿、保存后待生效，再实现最小读写闭环
- [ ] D2.3 请求接管：按 scope 发布 Cockpit 路由，验证外层 retry/fallback 不会再次发请求，验证原有 LiteLLM 标准 token/spend 统计继续归属原用户/Key；客户端协议不新增内部管理 Token（本地代码与离线契约已完成，激活和运行验证未完成，见 D2.3 第一段结果）
- [ ] D2.4 行为验收：用可控时钟验证重试 0/1 次、总预算、冷却与亲和性 TTL；用有序候选验证优先级、备用卡和禁用；用 stream/tool/image/history 失败用例验证硬边界。模拟 429 额度耗尽与普通限流分别断言，不只检验 JSON 字段是否保存
- [ ] D2.5 UI 验收：在本地隔离测试中验证表单能力状态、显式保存、待生效提示、冲突刷新和错误回显；功能完成后再记录运行环境门槛。按用户要求，镜像和部署继续延期，代码测试可继续推进

### D2 后续代码顺序（未完成，不可切流）

1. 账号/主体映射 dry-run：以数据库卡片 UUID 和已授权范围建立目标 account IDs，旧卡 Key 只有 hash，不导出或猜测旧明文。报告模型/供应商/代理/额度字段差异，无确定映射时拒绝迁移；验收空 scope、跨卡 scope、缺失凭据和不支持供应商
2. 管理同步事务：保持现有页面契约，服务端经条件写入更新 Cockpit projection；处理 412、部分凭据写入、回滚与显式重启，写成功不等于已激活；先单实例单 writer，不宣称跨进程 CAS
3. 数据面接线：按已验证主体 scope 发布 Cockpit deployment，保留 LiteLLM 标准统计；对 Cockpit 路由取消外层重复 resolve/acquire、重试/换卡/续聊 owner，旧路径仅作为另一条显式回滚配置；不能只替换 upstream URL
4. 将旧行为矩阵变为 Cockpit 契约，覆盖额度换卡、续聊、stream/tool/image 重放边界和用量归属；验证各供应商加载路径。当前 manifest Codex token 注册要求 provider=codex，不能将九类供应商凭据一律按 Codex 导入

续作先读取此 D2 状态与已完成提交，不重新实施 D1、D2.1、全局策略保存/应用或前述单卡原生 Key 预检。本次已完成第五段回执服务的可信主体读取适配与显式、受保护的只读管理流程，离线验证覆盖鉴权、入参、主体撤销/范围变化及错误脱敏；不能用调用方 DTO 替代服务端证明，也不能让普通状态/预检调用自动读取凭据。下一阶段仍需单独规划 D2.3 的 scope 发布与唯一请求 owner 接线；真实数据库/凭据、provider 身份、生产部署和流量切换均未验证或授权，不可开始实际切流

### D2.1 本地代码结果（2026-10-23）

已新增 Manager `POST /api/cockpit/preflight` 与 LiteLLM `POST /account_pool/cockpit/preflight`。输入为 card_id、subject_kind、expected_key_id 与可选 candidate_ids；浏览器传 authorized_account_ids 等未定义字段返回 422。Manager 校验内部 Token，外层仅 proxy admin 可调用，成功与上游错误响应不缓存

旧卡 Key 依据服务端尚未撤销的绑定和有效 fallback 策略推导同供应商、同渠道范围，preferred_account_ids 不扩大权限；显式空候选与省略候选含义不同。原生 LiteLLM Key 尚无可验证的主体映射入口，返回 subject_binding_unverified 和空授权范围，不照搬旧卡 Key 的 fallback 权限

报告分开返回 authorized_account_ids、scope_valid、账号 blockers 和 pending_checks。scope_valid 仅说明候选在本次读取的静态授权范围内，不等于账号可用或能迁移。停用、待配置、手工冷却、缺凭据、跨供应商、重复目标、缺失目标均报告；ready_to_migrate 固定为 false。预检对非 READY 来源保守阻断，不改变现有网关的状态判定

以卡片 UUID 作为拟议目标 ID，只检查现有目标投影；不会创建、覆盖或改名账号。相同 UUID/authId 或凭据文件存在不构成所有权证据，仍返回 target_identity_unverified。仅定位 Codex token 导入路径，其他供应商及 api_key 路径暂不自动映射；所有策略枚举均保留原值并报告 strategy_mapping_unverified，不静默转 auto

仅执行 Cockpit manifest 与 auth-files 元数据 GET，不读取凭据正文，不发供应商请求，不写策略、发 Key、获取租约或重启。报告包含源账号/策略/设置版本和持久化 manifest revision，但不是原子快照或执行许可，后续写入及请求时必须重新校验版本、授权、可用性和重放边界

本轮新增预检与代理用例 29 项；连同现有 Manager 管理、网关与代理用例共 214 项通过。新预检源文件严格类型检查 0 错误；定向 Ruff 通过。独立审阅代理因上游上下文解密错误未运行，由主执行者完成定向自查，不能宣称独立审阅通过。完整原始命令、输出与三态回滚保存在既有四角色的 d2 附件

D2.2 策略保存/冲突/显式生效、D2.3 请求接管、D2.4 行为兼容、D2.5 页面编辑仍未完成。当前号池页面、LiteLLM 统计和额度换卡/续聊数据面没有切到 Cockpit，不应将本次管理 API 进展描述为整体后台替换完成

### D2.2 第一段本地代码结果（2026-10-23，完整管理迁移仍未完成）

新增 Manager 与 LiteLLM 的 GET/PUT `/api/cockpit/policy`、`/account_pool/cockpit/policy`，通过现有管理路由和管理员鉴权接入。号池“设置总览”增加 Cockpit 原生 Codex 池策略面板，复用 apiClient、生成类型、现有 UI 组件及按用户 Token 隔离的查询键

这次编辑的对象是 Cockpit manifest 中 routingStrategy 与 customRoutingRules，不是现有 LiteLLM 卡片策略。八个策略使用 Cockpit 原生枚举；自定义优先级高值优先，同优先级使用权重，优先使用与备用角色互斥，角色选择对各策略均有效。可写范围暂限 1 至 256 个 OAuth/default 账号，优先级 -10000 至 10000、权重 1 至 10000。缺省规则按上游的 0/1/false 展示，已有非法值不静默改成默认值

客户端不能提交启用状态、候选权限、重试次数、冷却或亲和性字段；这些尚未映射，服务端也拒绝。access_token、agent_identity、api_key 和未知账号类型暂不可编辑；这不是认定 Cockpit 不支持这些类型，只是本轮适配范围未覆盖。全局排序字段不可冒充逐卡或逐 Key 策略

GET 返回持久化 revision 和脱敏配置；PUT 先核对 revision，再用 If-Match 单次条件写入原 manifest，保留凭据、apiKeys/accountIds、模型等其他字段，以及被保留规则上的未知字段。旧版本和写入期间冲突返回 409。仅一次原子 manifest 更新，不跨写凭据或第二个策略库，因此本段不需要多文件补偿；凭据迁移事务的补偿仍待实现

保存后的状态始终为 restart_required_or_unverified。Manager、代理或浏览器收据不完整/网络结果不明时不得自动重试或宣称未写入；页面锁定编辑，要求显式成功重新读取。没有 restart 按钮、自动重启或已生效提示。后续显式应用仍须实现运行中 revision 与全局变更确认，不能凭持久化回执宣称生效

独立代码审阅发现并修复 PUT 异常收据分类、257+ 账号仍可编辑和浏览器空成功收据误报风险，失败测试和复验进入本轮证据。保留一个兼容限制：上游允许 weight<=0 按 1 执行，但当前视图对该类旧规则返回 502，不自动规范化；标准桌面生成规则会限制为正权重

本段仍不支持直接从 Cockpit 桌面设置与网页并发编辑的完整闭环：Go 管理 API 的单进程 CAS 已验证，但桌面生成器可能从独立配置重写 manifest，其统一版本化改造尚未完成。不支持多实例并发写入承诺。当前数据面、额度换卡、续聊与 LiteLLM 统计链路未切换

该段之后的运行版本及显式应用已在下节完成；不要重复实现。桌面共管与多进程写入尚未完成，不能与服务端单写者协议混淆

### D2.2 第二段本地代码结果：运行版本与显式应用

本段沿用 LiteLLM `codex/cockpit-reuse-plan` 与 Cockpit `codex/cockpit-backend-replacement` 两个隔离分支。没有推送、镜像构建、部署、真实供应商请求或凭据迁移；现有请求仍走原 LiteLLM/Manager 号池链路

Cockpit 新增 GET `/admin/v1/policy-runtime` 和 POST `/admin/v1/policy-apply`。启动时从实际加载的同一份 manifest 字节记录 activeRevision；GET 单独返回已保存版本、运行版本、可应用状态和阻断原因。POST 必须携带目标已保存版本的 If-Match，只热更新 routingStrategy/customRoutingRules，并返回相同的已保存/运行版本。与启动快照相比，其他任意 manifest 字段有变化就拒绝单独应用，包括未知字段；不会连带迁移账号、Key、授权、模型或凭据

版本核对与应用使用同一个 Go 管理写入锁。每次 selector Pick 固定不可变策略快照，优先/备用筛选、排序与诊断使用一致版本；不重启、不终止流、不新增重试或重放。后续重试可读取新版本，不能声称整个逻辑请求始终固定一个版本。原有会话亲和性、动态 api-key-priorities 和额度状态仍独立影响选卡，此 revision 不代表这些动态状态已统一版本化

Manager 与 LiteLLM 暴露 GET `/api/cockpit/policy/runtime`、POST `/api/cockpit/policy/apply` 及对应 `/account_pool/cockpit/policy/...` 管理代理。POST 只接受 revision，沿用管理鉴权。两层核对返回版本是否与请求一致；冲突为 409，非策略变更/不支持为 422，不明确应用结果为 503 apply_outcome_unknown，不自动重试或回退。异常或矛盾收据不能变成已生效提示

号池现有面板增加“检查运行状态”和“应用已保存策略”。保存与编辑清除旧运行确认，未保存草稿不能应用。检查会重新读取策略与运行状态并核对版本；应用失败后必须重新检查，单独重读保存值不能解除应用锁。成功只确认 Codex 全局排序版本，不表示 Cockpit 已接管 LiteLLM 流量

**策略源边界：** 服务器方案以 Cockpit 服务 manifest 为唯一持久化策略源，现有号池页面通过管理 API 读写，不再新增策略库。使用单服务进程，manifest 必须与 auth 目录、额度/优先级状态文件分离，禁止其他进程或桌面生成器写同一文件。桌面应用启动自己的本地 sidecar，与服务器 `/data` 不是同一部署；跨机器桌面管理需要新增明确的远程地址、管理凭据和模式选择，本轮不擅自加入。保留可选桌面共管为未完成项，不以此阻塞已确认的服务器+现有页面方案，也不宣称跨进程 CAS 已实现

本轮 Manager/代理/原网关定向回归 269 项通过；前端相关 66 项通过；Go 包 172 个顶层测试和 69 个子测试通过。新运行模块及管理客户端严格 Python 类型检查、聚焦前端源码与测试 tsc 通过。Go race 检查实际因缺少 gcc 失败，未安装编译器；前端 ESLint 无错误，保留一个复杂度警告。上轮全量前端 tsc 失败仍未解决，本轮未运行全量 tsc、生产构建或浏览器目视测试

独立审阅未发现本段阻塞问题，补充覆盖了未知/矛盾收据、严格布尔值、已保存与运行版本各自不匹配、管理接口禁用/未授权及 Go 管理 PUT 与 apply 并发。后续仍需主体/凭据映射、按 scope 发布、唯一请求 owner 接线、计费与续聊行为矩阵；这些完成前不得标记整体后台替换完成

### D2.2 第三段本地代码结果：原生 Key 单卡预检与凭据冲突检查

本段基于 LiteLLM `fe31c59cc1`，继续使用 `codex/cockpit-reuse-plan`。Cockpit 保持 `ec9989c0` 和原有未提交 workflow 文件不动；原 `CLIProxyAPI分支` 不改、不提交。没有推送、镜像构建、部署、生产数据库访问、真实供应商请求或真实凭据迁移

新增 `POST /account_pool/cockpit/native-preflight`，仅管理员可调用，公开输入只有 64 位小写 Key hash 和可选 candidate_ids，不接受明文 Key、card_id、metadata 或 ACL。成功、入参校验和 Manager 错误响应不缓存；校验失败不回显误填的秘密。`inspect_native_subject` 只读取 writer 数据库中的 live Key 和撤销 tombstone，不读缓存、不注册或生成 Key。live 查询在同一 SQL 快照排除 tombstone，避免先读撤销表后删除竞争复活 Key

首期只核验无复杂权限、无关联主体的有效原生单卡 Key。缺失、撤销、禁用、过期、非法记录、旧卡 Key、无绑定或数据库失败均不返回卡片范围。带用户/团队/项目/组织等关联，或模型、路由、配置、访问组等限制时返回 `model_scope_unverified`；许多实际 Key 会属于这一未支持范围，不能把本段说成全量原生 Key 授权已完成

LiteLLM 只把服务端查得的单卡 UUID 和候选提交给内部 token 保护的 `/api/cockpit/native-preflight`，不转发 Key hash。Manager 不查询旧卡 Key，也不继承其 fallback 权限；候选只能缩小范围，不能扩大权限。公开代理再次拒绝返回错卡、错主体种类或扩张范围的回执。`scope_valid` 仅表示本次静态账号上界内的候选关系，`ready_to_migrate` 始终为 false，保留 `native_runtime_authorization_unverified`、模型、凭据、策略和运行资格检查；报告不是原子授权快照或请求执行许可

凭据预检补充 source fingerprint 交集冲突、target authId 重用、大小写和 Linux basename 别名冲突。只允许 CLIProxyAPI 的 Codex OAuth/default 候选；API key、access-token、agent identity 和未知模式阻断，缺省 authKind 也检查 accessTokenOnly/upstreamApiKey，矛盾字段保守拒绝。源所有权 HMAC 与目标文件 SHA256 不是同类证据，UUID、文件名或账号声明一致仍不能解除 `target_identity_unverified`。其他供应商及 OpenAI-compatible 独立通道继续未映射

未扩展号池页面，本段先提供受保护的离线 API。生成类型仅增加一个公开路径、一个操作、三个 schema，并更新既有两个预检 schema；通过 OpenAPI 与固定 openapi-typescript 7.13.0 生成后按 TypeScript AST 合并，没有手写整份 schema

本轮最终定向回归 366 项通过，原 failover/quota/gateway/conversation/native-routing/accounting 六文件 431 项通过，均为离线或模拟集成测试；分别有 93 和 9 条既有依赖警告。新 reader 源码及测试、Manager preflight 严格类型检查通过，改动 Python 文件定向 Ruff 与生成声明 tsc 通过。未运行全仓检查、前端生产构建、浏览器目视、真实数据库或供应商验收；上段 Go race 缺 gcc 和全量前端既有类型问题仍未解决

独立审阅的两项 P2 已修复并定向复审关闭：非法请求回显秘密、空 authKind 隐含非 OAuth 认证。三态源码副本验证：BASELINE 原生接口 404、冲突夹具仅报告身份未验证；MODIFIED 接口 200、单卡上界且仍不可迁移，非法输入 422 脱敏并报告三类凭据阻断；ROLLBACK 恢复 404 和原阻断行为，三个进程退出均为 0，回滚源码包 hash 等于基线。现有四角色继续保留，完整原始输出、失败尝试及补丁见 scope 附件

### D2.2 第四段本地代码结果：关联主体与显式模型范围（2026-10-24）

本段基于 `d06bd940ec`，沿用现有整合工作区，没有新建分支或 worktree；原 `CLIProxyAPI分支` 不改、不合并。只实现只读静态预检，没有推送、镜像构建、部署、真实数据库/供应商访问或凭据迁移。前文“所有关联主体一律未验证”是第三段的历史结果，当前支持范围以本节为准

`WriterNativeSubjectRepository.find_live` 在同一条参数化主库查询中取得 Key 与用户、团队、项目、团队成员的必要事实，并保留同快照的撤销 tombstone 排除。关联只投影 ID、模型列表、停用状态及保守权限标记，不读取用户密码、邮箱或返回主体原始配置。缺失、格式非法、归属矛盾或停用的关联不返回卡片范围；用户和团队同时关联时，要求匹配且没有预算关联的当前成员记录

`_PermissionScope.model_scope` 按现有 `common_checks` 的模型规则处理：个人 Key 受用户 models 限制，团队 Key 不继承用户个人模型列表；Key、团队和项目的非空精确模型列表取交集。空列表仅在所有适用层均无模型限制时表示 unrestricted，空交集仍返回 `model_scope_unverified`，绝不转换成无限制

`NativeSubjectResult` 新增显式 `model_scope`：verified 必须携带 `{kind: restricted, models: [...]}` 或 `{kind: unrestricted, models: []}`，未验证结果不得携带模型范围或卡片。这里只证明精确模型名称限制，不证明供应商模型 ID、全局 alias 或 access-group 的实际解析。现有 native-preflight 公共响应保留完整 subject，Manager 仍只检查单卡候选关系；`ready_to_migrate=false` 和 `native_runtime_authorization_unverified` 不变，结果不能用作运行时许可或扩大旧卡 fallback 权限

首期仍阻断 sentinel、通配符、别名、access groups、自定义策略、组织/对象权限、预算关联和未验证的限额配置等动态情况。支持有界关联不等于全部实际 Key 可迁移；这些不支持的权限必须继续保守拒绝。没有修改原网关选卡、额度换卡、重试、续聊、计费或 Cockpit 服务端

本轮新运行：检查点基线 366 passed；修改后同范围回归 466 passed，原换卡/续聊/计费六文件 431 passed，分别有 93/9 条既有警告。定向 TDD 为 14 failed（预期 RED）后 177 passed（与完整回归重叠，不累加）；新 reader 源码及测试严格类型、改动 Python Ruff、生成声明 tsc 均通过。独立审阅未发现 P1/P2，离线 SQL/schema 引用检查通过，但未实际执行 PostgreSQL。源码同输入 BASELINE/ROLLBACK 均为 model_scope_unverified 且无卡片，MODIFIED 为 verified 且仅 scope-model；恢复源码包哈希一致，补丁重建逐字节一致

生成声明通过实际 FastAPI OpenAPI 与固定 `openapi-typescript 7.13.0` 生成，再按 TypeScript AST 合并，只新增 NativeModelScope 及 NativeSubjectResult 的字段；不是手写声明。回归、同输入三态、补丁重建和审阅结果见原四角色及 `complex-*` 补充证据，均不代表真实 PostgreSQL、完整 UI/生产构建或真实上游验收

### D2.2 第五段本地代码结果：内部凭据字节对应回执（2026-10-24）

本段基于 `61c3cb40ca`，新增内部 `CockpitCredentialReceiptService.issue/revalidate` 与独立回执密钥派生用途 `SecretPurpose.COCKPIT_CREDENTIAL_RECEIPT`。本轮在 `955541c801` 上完成受信主体读取适配和显式只读回执 API；只注入内存数据、虚构凭据与 HTTP MockTransport，未访问真实数据库、真实凭据或供应商。没有 UI 注册、凭据迁移、模型权限发布或后台接管；原预检的 `target_identity_unverified` 与 `ready_to_migrate=false` 不变

签发只接收原生主体 hash，重验另外接收已有签名回执；均不接受调用方提供的凭据正文、来源摘要或 proof=true。注入的可信主体读取器必须每次验证当前单卡及明确模型范围，构造 `TrustedCredentialScope` DTO 本身不是证明。服务通过既有源读取/下载、CredentialOwnership、CockpitAdminClient 端口读数据，下载前后均核对独占归属，并使用既有 credential_identity 重算完整指纹集合，拒绝与卡片记录不一致的来源

首期仅处理严格 Codex OAuth access_token + refresh_token 文件和唯一目标账号/安全 basename。源身份 HMAC 只用于归属证明；确认来源后，才把源文件原始字节的 SHA256/长度与 Cockpit 当前文件 metadata 对比。不同独立卡容器使用相同源文件名不算冲突，来源指纹重叠和目标共享目录中的重名/别名冲突必须拒绝。此结果只证明已拥有的源文件与目标文件字节对应，不证明上游账号身份、令牌有效或目标服务已加载

回执固定版本/用途和 300 秒有效期，以单独派生用途的 HMAC 绑定主体、卡片、模型列表、源版本/状态/指纹集合/内容摘要，以及目标账号/文件/manifest revision。签发与重验都做两轮当前读取，拒绝观察到的中途变化；重验另拒绝篡改、外部密钥签名、错误主体、过期、未来时间和陈旧状态。两次读取不是跨服务原子事务，回执不是一次性迁移锁或运行时授权。原管理、网关、OAuth 和授权状态的密钥派生未变，跨版本 StateCipher 互解检查通过

独立审阅发现并修复一项 P2：Go encoding/json 对结构字段大小写不敏感，Python 投影会忽略非规范大小写。回执边界现在拒绝 accounts 及 id/authId/authKind/accessTokenOnly/upstreamApiKey 的大小写影子或别名字段，避免对错误目标文件/认证模式签名；无关字段及嵌套元数据仍可保留。修复只在回执入口，不修改既有预检响应行为，复审已关闭该问题

验证分阶段记录于原四角色与 receipt-* 补充证据：本段起点基线回归 466 passed；大小写修复前管理/API 回归 294 passed；修复后回执定向 100 passed，Manager 六文件回归 225 passed（包含该 100 项）；本轮原数据面六文件回归 431 passed。上述执行退出码均为 0，范围有重叠，不相加或混称一次执行。最终回执源码/测试严格类型检查 0 errors，定向 Ruff 通过；已有依赖 warnings 保留，不代表完整仓库、真实服务或生产构建验收。源码同输入 BASELINE/ROLLBACK 为服务不存在，MODIFIED 能签发并重验只读对应回执，篡改/陈旧/大小写歧义均拒绝，迁移标记始终 false。恢复源码包哈希及补丁重建逐字节核对，不触碰原主分支

经用户确认，Manager 通过固定 HTTP(S) 内部地址和现有 Manager bearer secret 请求 LiteLLM 的 `/account_pool/internal/cockpit/native-subject`；LiteLLM 使用已有主库主体解析读取当前单卡和静态模型范围，不复制权限 SQL，也不接受浏览器提供的权限结果。`ACCOUNT_POOL_COCKPIT_SUBJECT_URL` 未配置时回执服务关闭，配置还要求已有 Cockpit 管理设置。subject HTTP client 禁止重定向、使用固定路径和有限超时，并在 Manager lifespan 退出或 startup 失败时关闭

回执签发与重验只通过 Manager `/api/cockpit/credential-receipts` 和 `/revalidate` 显式执行；LiteLLM 代理入口先校验 proxy admin，再转发至 Manager。两侧响应均禁缓存，输入和服务失败使用固定错误，不回显 receipt/凭据正文。普通状态、预检和用户模型请求不触发凭据读取；只读回执仍不证明上游身份、令牌有效或 Cockpit 已加载目标账号

本轮新增三文件定向回归 16 passed，Manager 受影响 8 文件回归 233 passed，LiteLLM 受影响 3 文件回归 250 passed，均为离线/MockTransport 测试并有一条 Starlette/httpx 弃用警告。新增源文件与测试严格类型检查 0 errors；Manager app/API 与原生跨协议类型检查仍含历史诊断，严格 Ruff 聚焦集合仍报告既有复杂度/异常处理/日期规则，不作为通过声明。更完整的命令、失败尝试与三态计划回滚结果见原四角色及 receipt-adapter 补充记录

### D2.3 第一段本地代码结果：授权范围发布与唯一请求 owner 接线（2026-10-25）

本段基于 `1a6902d443`，沿用现有 `codex/cockpit-reuse-plan` 工作区；Cockpit 保持 `ec9989c0` 和既有未提交 workflow 不动。只写代码、离线测试和本地提交，没有推送、镜像构建、部署、真实数据库/供应商访问、真实凭据迁移或切流。按用户确认的四项执行：A+B 两段都做；发布记录 `enabled=false` 且预生成派生通行证；只接原生虚拟 Key 的 Responses 请求；数据面地址由 Manager 内部路由状态下发

Manager 新增 `account-pool/account_pool/cockpit_scope.py`，并通过 `create_router` 暴露管理路由。发布输入只有 subject hash、D2.2 第五段的签名回执和期望 manifest revision；账号、模型和回执状态只能来自服务端当前读取。发布前重新校验回执，要求目标 account 为 Codex OAuth/default、authId 与回执一致，回执中的每个模型都能在 manifest modelIds 和排除规则下映射；任一不满足整体拒绝，不静默裁剪。投影写入保留 apiKey 条目，id 为 `litellm-subject-<subject hash>`，`accountIds` 只含回执绑定的单卡，`allowedModels` 只含受限模型，`enabled=false`，key 使用新 `SecretPurpose.COCKPIT_SUBJECT_ROUTING` 派生，不在响应或日志出现

写入沿用单次 manifest CAS，保留未知字段；revision 冲突、同名但不同结构、通行证冲突和写入结果不明分别返回固定 409/409/503，不自动重试。撤销同样走 CAS 删除，幂等。新增 `GET/POST/DELETE /api/cockpit/scope` 与内部 `GET /internal/cockpit/route/{subject_hash}`；LiteLLM 增加仅 proxy admin 的 `/account_pool/cockpit/scope` 代理。所有响应禁缓存。视图返回 `state=staged`、account_ids、models、manifest revision、`runtime_activation=restart_required_or_unverified` 和 `ready_to_migrate=false`，不把发布当运行授权

LiteLLM 新增 `account_pool_cockpit_route.py`，按 subject hash 读取 Manager 内部路由状态。开关 `ACCOUNT_POOL_COCKPIT_ROUTING_ENABLED` 默认关闭；关闭时不查询、不改变现有链路。开启后 absent/staged 继续走原链路；active 时校验模型、协议和可重放边界，在 `_route_request_single_attempt` 绕过 Router 与 Manager resolve/acquire，单次直接调用 `litellm.aresponses`，设置 `num_retries=0`、`max_retries=0`、清空 fallbacks、禁用缓存，并保留原请求的 `litellm_metadata` 归属字段，只追加 `account_pool_route_owner=cockpit`。blocked、路由读取失败、模型越界、非 Responses 或带 `previous_response_id`/签名历史的请求明确失败，不回退旧链路

首期只在测试中注入 active 路由；没有实现把发布记录改为 `enabled=true`、重启 Cockpit、确认运行 revision 或切流。生产上发布记录保持 disabled，Router 和 Manager 仍是实际请求 owner；因此“代码具备单 owner 路径”不等于“运行中已接管”。30 秒路由缓存和未实现激活意味着发布/撤销传播存在窗口，切换顺序仍属后续门槛

本轮新增测试：Manager 范围发布与路由状态 16 项、LiteLLM 发布代理 4 项、LiteLLM 唯一 owner 13 项，共 33 项，均为离线或 MockTransport 测试。更广的 Manager/LiteLLM 定向回归、严格类型、Ruff 和新增行诊断见 `d23-20261025/` 证据。新增源文件严格类型 0 errors；改动文件仅保留既有诊断，新增行严格 Ruff/基于 pyright 诊断均为 0。`npm run gen:api` 尝试重新生成时产生既有路由顺序导致的大范围重排，未提交该噪声；本轮没有 UI 使用新类型。`ready_to_migrate` 保持 false，真实统计持久化、运行中 Cockpit、凭据和供应商身份仍未验证

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
