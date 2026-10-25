# LiteLLM + Cockpit 去 Manager 分阶段实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新环境使用客户端 -> LiteLLM -> Cockpit -> 供应商，管理与模型请求均不再依赖 Manager

**Architecture:** LiteLLM 保留公共 API、用户/Key 鉴权、模型权限、限额、原生用量计费与现有管理页面；Cockpit 负责上游账号、凭据、选卡、额度分类、冷却、重试/故障切换和会话续聊。必要管理适配放在 LiteLLM 进程内，不另建 Manager，不把旧 Manager 整体搬入 LiteLLM。旧数据导入是一次性受控工具，不参与日常请求

**Tech Stack:** Python/Pydantic/FastAPI、React/TypeScript/Next.js、固定版本 Cockpit Go sidecar、Docker Compose、GitHub Actions/GHCR

**Spec:** `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/docs/superpowers/plans/2026-10-23-cockpit-reuse-audit.md` 中的“2026-10-25 最新决策与续作入口”优先于旧 Manager 过渡设计

## Global Constraints

- 工作目录固定为 `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm`，分支 `codex/cockpit-reuse-plan`，当前业务代码基线 `16655b9fe438d19c1fec2edcea06b6145f4bea59`
- 用户已明确允许在现有 worktree 继续并本地提交；不切换到 `D:/Work2api/litellm` 修改，不新建功能分支或 worktree，不自动 push
- 先读 `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/AGENTS.md`、`C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/CLAUDE.md`、`C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/account-pool/MAINTENANCE.md`，修改相应目录时再读该目录规范
- 当前批准开发和本地验证；服务器操作、真实凭据导入、付费上游测试、镜像发布和生产切流必须按各门槛单独确认，不从“让 DS 写代码”推导部署权限
- 旧生产不动。新环境不运行 Manager 容器，不调用 Manager API，不依赖 Manager 数据库，也不挂 Docker socket/socket-proxy
- 保留原生鉴权与授权范围，不信任浏览器提交的权限结果、不复制权限 SQL、不用一枚共享管理 Token 代替各主体的数据面权限
- 单个请求只允许 Cockpit 重试/换卡；LiteLLM 不叠加重试、fallback 或已输出后的重放
- 默认不开启新路由。新环境缺少依赖时显式失败，不静默回退旧 Manager；旧部署兼容行为单独保留并测试
- 只做任务所需改动，不清理无关旧代码。测试通过后主代理本地 commit，未经明确命令不 push

## 协作方式与当前状态

用户最新安排是主代理负责规划、派工和检查，DS 负责代码实现；无需用户手动中转。前一条“只给其他 AI 计划”已被这一协作安排更新

已通过 DSH headless 实际调用 `opencode-go / deepseek-v4.1-flash`，请求 `reasoningEffort=max`，返回 completed、进程退出 0。一次性 overlay 不改变桌面默认配置。此次关闭了工具，仅让 DS 生成测试草案，没有业务代码改动。CLI 首次缺 adapter，随后凭据解析失败；修正为显式 provider 配置和只传入子进程的已有凭据后成功。凭据不写入计划、日志或仓库

DS 初稿把“Manager 失败后不回退”当成验收，并允许一次 Manager 调用，主代理已拒绝这一条件。正确条件是新环境 Manager 调用次数始终为 0，包括启动、健康检查、管理请求、模型请求与后台任务

后续每次派工包含：固定 HEAD、只允许修改的文件、输入/输出契约、失败测试、禁止事项、交付路径。先让 DS 在可丢弃候选副本写补丁与测试，由主代理检查后应用现有 worktree。不得让两方同时编辑同一文件；不得让 DS 自行推送、部署或无限扩大范围。DS 的解释不能替代主代理亲自观察的命令输出

本计划和下面的路径清单已提交即可独立交接，不依赖 Git 忽略的 checkpoint。换机器时先核对分支和 commit，再整体映射 C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm 路径，不能将旧测试数量当作新机器结果

## 证据边界

| 项目 | 已知结果 | 尚不能证明 |
| --- | --- | --- |
| 本轮过渡回执离线基线 | 16 passed，1 warning，exit 0 | 无 Manager 运行、真实数据库和真实上游 |
| 上一提交前端检查 | 66 项定向测试、4 项 Vitest 类型测试通过；Webpack 导出 54 页成功 | 全前端测试或真实网页操作通过 |
| 完整 TypeScript | next typegen 后 1083 条测试文件诊断，源码诊断 0，exit 2 | 完整 tsc 通过 |
| make check | exit 127，make 缺失 | 项目全门通过；本机有 Git Bash，不能再写“缺 bash” |
| 历史 D2.4 | Manager 1152 passed/5 skipped；LiteLLM 1043 passed/1 skipped；定向 38 passed | Cockpit 真实额度分类、换卡、冷却、会话亲和与计费落库 |
| 服务器/镜像 | 仅有此前只读观察，目标机器 ARM64 | Linux ARM64 镜像构建、容器验收、测试域名已开通 |

## 代码定位清单

| 文件 | 已观察依赖与处理边界 |
| --- | --- |
| `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/litellm/proxy/management_endpoints/account_pool_cockpit_route.py` | `CockpitRouteResolver._default_lookup` 使用 Manager URL/Token；`ManagerRouteLookup.read` 调 `/internal/cockpit/route/`；`cockpit_route_for_request` 仍依赖 `PoolIdentity.card_id` |
| `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/litellm/proxy/route_llm_request.py` | `_route_request_single_attempt` 接入 Cockpit 路由；防止外层 wrapper 再次重试 |
| `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/litellm/proxy/management_endpoints/account_pool_integration.py` | 审计 `PoolIdentity` 生成、card scope 与鉴权前置依赖，不能只替换最后一次 lookup |
| `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/litellm/proxy/management_endpoints/account_pool_cockpit_subject.py` | 复用原生 Key 主库权威查询；当前只支持可静态证明的单卡、明确模型上界，不随意放宽 |
| `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/litellm/proxy/management_endpoints/account_pool_cockpit_receipts.py` | 当前代理到 Manager，去服务化后使用进程内受鉴权调用；内网主体 HTTP callback 不再是新路径前置 |
| `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/litellm/proxy/management_endpoints/account_pool_management.py` | 管理代理和资源接线；保留管理员权限、脱敏及 no-store |
| `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/account-pool/account_pool/cockpit_scope.py` | `CockpitScopeService.publish/read/revoke/activate/deactivate/route_state`，提取必要编排时保持 CAS、撤权和运行 revision 检查 |
| `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/account-pool/account_pool/cockpit_credential_receipts.py` | 旧凭据对应关系的签名回执服务，不应成为每次新账号请求的 Manager 依赖 |
| `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/account-pool/account_pool/cockpit_admin.py` | 可复用管理客户端契约；与 manifest/failure/policy/preflight/runtime 配套核对依赖、包内容和生命周期 |
| `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/account-pool/account_pool/app.py` | 仍初始化旧 DB、定时器、凭据/额度刷新和 Docker 生命周期；禁止整个移植到 LiteLLM |
| `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/ui/litellm-dashboard/src/features/account-pool/components/dashboard/AccountPoolCockpitPolicyPanel.tsx` | 目前只有全局策略流程，不等于完整号池管理 UI |
| `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/ui/litellm-dashboard/src/features/account-pool/api/AccountPoolManagementApi.ts` | 复用客户端与生成 schema，不能手改 schema 来假装接通 |
| `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/.github/workflows/publish-deployment-images.yml` | 正式发布流水线，不能原样用于测试镜像 |
| `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/deploy/docker-compose.yml` | 现有固定容器/卷/网络名称及 socket proxy 不能照搬到测试环境 |

## 阶段 0：冻结依赖与协议矩阵，由主代理先完成派工依据

- [ ] 记录两个仓库实际目录、HEAD、未提交修改、构建入口和许可证。Cockpit 历史记录为 `ec9989c0`，本轮未复核其真实路径/版本，不能自行编造路径或假定已有管理 API
- [ ] 用源码调用链列出启动、鉴权、路由、管理、计费、后台对账每一处 Manager/旧 DB/Docker 依赖，标注新 owner 和一次性迁移例外
- [ ] 定义两种明确部署状态及默认值：旧部署保留原行为；新部署绝不回 Manager。禁止凭 URL 是否可达来自动切换
- [ ] 固定接口支持矩阵：Responses 普通/流式、Chat Completions、Anthropic Messages、工具/图像、同卡/跨卡续聊、previous_response_id、签名/密文历史。当前窄路径只支持 Responses、单卡、显式白名单，其他列为未支持或未验证，不可默认为兼容
- [ ] 主代理审阅矩阵后再派阶段 1。缺少 Cockpit 服务能力、许可不允许目标分发、关键协议必须降级时向用户报告，不能掩盖为内部实现细节

验收：每项新运行态依赖可定位到固定源码和测试；无 Manager 的成功条件清楚；尚不确定的 API 和字段阻断对应任务，不阻断独立本地基线检查

## 阶段 1：把最小管理适配从 Manager 运行时移出

先做保持行为的提取提交，再做接线提交。依赖阶段 0；不要同时改路由默认值、计费或数据结构

- [ ] DS 先为 `CockpitScopeService` 的四状态、manifest revision 冲突、撤销、过期/被禁用主体和目标凭据不一致写测试，复制现有输入和期望值，不通过改期望掩盖语义变化
- [ ] 仅移动所需客户端/值模型/编排至 LiteLLM 可打包的号池适配模块。保留原管理路径及错误码，旧 Manager 调用方仍能通过兼容入口使用，禁止 runtime import 旧 app/config/DB/Docker
- [ ] 将主体读取依赖改为进程内调用现有原生查询。原 `SubjectReader.read(subject_hash) -> TrustedCredentialScope | None` 与 `ReceiptRevalidator.revalidate(subject_hash, encoded)` 的行为保持明确，类型归属在提取提交中一并调整
- [ ] 同一 owner 管理 HTTP client、锁、缓存和关闭过程，不为不同入口重复建一套状态。持久化 revision 与运行 revision 分开；保存成功不冒充激活成功
- [ ] 主代理重跑旧 Manager 配套测试与新的 LiteLLM 测试，检查 wheel/镜像中实际包含新模块，再本地提交

失败测试必须覆盖：普通用户被拒、浏览器伪造主体被拒、秘密不入响应/日志、CAS 冲突不覆盖、服务不可用不返回假成功、移除旧 Manager 包后 LiteLLM 最小适配仍可导入和构造

回退：回退本阶段代码及接线，不更改生产数据或源凭据；不在提取过程中删除旧服务

## 阶段 2：原生鉴权后直连 Cockpit，切断所有运行时 Manager 调用

- [ ] DS 在现有 `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/tests/test_litellm/proxy/management_endpoints/test_account_pool_cockpit_route.py` 和 `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/tests/test_litellm/proxy/test_route_llm_request.py` 增加失败测试。依赖通过现有 `RouteLookup` 注入，不猴子补丁生产类，不把测试桩假装成真实 Cockpit
- [ ] 新模式 Manager transport、旧 DB repository 和旧 fallback 均注入 tripwire，调用即失败。active 且已授权时仅发送一次 Cockpit 请求；absent/staged/blocked/超时/非法响应时明确拒绝，Manager 与旧 fallback 调用次数为 0
- [ ] 将 `_default_lookup` 接到阶段 1 的直接服务，清除新模式对 Manager URL/Token 的需求。匹配 subject hash、账号集合、模型范围与 runtime revision，不能只检查 HTTP 200
- [ ] 排查 `PoolIdentity.card_id` 前置生成和原生 Key 鉴权、模型发现、卡片投影、reconciler、usage/background tasks 中的传递依赖；旧 Key 的兼容映射属于阶段 5，未迁移的 Key 不放宽授权
- [ ] 原生预算、限流、模型权限继续生效。禁用 Key、撤销范围、模型别名/通配范围、多 worker 缓存分别验证，明确撤权生效上界；现有 5 秒缓存不是即时跨 worker 撤权
- [ ] 主代理审查单一 retry owner：`num_retries=0`、`max_retries=0`、禁 fallback，确认外层包装、错误恢复和计费路径也没有重放；旧模式同输入继续保持历史行为

测试断言模板只表示行为，不冒充已存在测试 fixture：

```python
assert result_is_expected_for_route_state
assert cockpit_request_count == (1 if authorized_and_active else 0)
assert manager_request_count == 0
assert legacy_repository_access_count == 0
assert legacy_fallback_count == 0
```

验收：隔离 LiteLLM + Cockpit 进程即使完全未启动 Manager，也可完成已声明支持的管理和模型请求；不可用场景失败关闭。静态 grep 无引用只是辅助，必须有运行调用证据。禁止把“尝试 Manager 1 次失败”记为通过

## 阶段 3：Cockpit 侧行为兼容与 LiteLLM 原生计费

- [ ] DS 按固定 Cockpit 源中的真实函数/测试位置实现额度耗尽换卡、普通 429 分类、冷却、账号候选过滤及亲和性，先用现有 LiteLLM failover/conversation 场景做红灯，再实现最小差异
- [ ] 普通请求、流式、工具、图像、同卡续聊、跨卡续聊分别记录实际请求次数与归属。已输出有效内容、可能已执行工具或历史不完整时不得盲目重放
- [ ] `previous_response_id`、签名/密文与压缩上下文的恢复不能靠删除字段冒充成功；不支持的路径明确拒绝并保持关闭。需要降低用户现有能力时先确认
- [ ] 在隔离 PostgreSQL 验证成功、失败、取消、流中断、重试成功的 request_id、用户/Key/团队归属、tokens、费用和 spend 持久化，避免一次外部请求重复计费；内层已收费的多次尝试按真实 usage 对账
- [ ] 主代理复跑 mock 状态机，再按批准的测试账号/模型和费用上限运行真实供应商；记录跳过项，未提供真实凭据不能宣布真实验收完成

验收：协议矩阵逐项有通过/不支持/未验证证据。模型名称从固定仓库模型表或提供方实际模型列表核实，不使用本计划臆定的型号

## 阶段 4：让现有页面完成真实管理流程

- [ ] DS 复用现有 dashboard 组件、`apiClient`、查询键和权限，不新建另一套管理应用
- [ ] 补全账号/凭据状态、主体预检、回执核对、scope 发布、运行 revision 确认、激活、停用/撤销流程，只有 staged 不显示“已经生效”
- [ ] 在现有策略面板/接口映射的测试文件增加表单输入到请求 payload、403、409/412、服务不可用、撤销后缓存失效的用例；浏览器不拿 Cockpit admin token 或 raw manifest
- [ ] 后端 API 变动后在 `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/ui/litellm-dashboard` 执行 `npm run gen:api`，提交生成结果；只跑关联 Vitest 文件，不跑未限制范围的全量 suite
- [ ] 分开记录定向 UI、ESLint、完整 tsc、Vitest 类型项目、Next Webpack 构建；构建用一次性源副本，保护已有 .next/out，不能删除测试或放宽 TS 配置伪造全绿
- [ ] 主代理在真实隔离服务浏览器依次创建/授权测试账号、确认模型发现、预检、发布、激活、请求、查看用量、停用，并记录网络请求没有 Manager 地址

验收：有可点击步骤和可观察结果。缺失的账号管理能力、代理/策略功能不能只隐藏旧按钮就算实现，须在阶段 0 能力矩阵中逐项批准差异

## 阶段 5：一次性迁移与隔离部署材料

依赖前四阶段。此阶段首先只写工具、配置和文档，不自动执行服务器操作

- [ ] DS 编写默认 dry-run 的一次性迁移工具，报告账号/Key/scope 映射、冲突和凭据兼容性；明文秘密只在受控通道传递，不入 diff/log。用合成 fixture 验证重复运行幂等、失败恢复与旧源不变
- [ ] 旧用户 Key 保留或换发必须明确选择并记录授权上界、归属和停止旧 Key 的时间；不能将 Manager DB 保留为新系统永久查询表
- [ ] 新建独立测试 Compose 与测试镜像 workflow，具体文件命名在阶段 0 固定清单后由主代理分配。新 Compose 的网络、卷、DB、端口、秘密和资源额度独立，无固定生产名称和 Docker socket
- [ ] GitHub Actions 负责构建，GHCR 仅存储。LiteLLM 与 Cockpit 各有独立测试镜像包，固定源码 SHA、平台和 digest，必须提供 linux/arm64。不得发布正式 latest/version 别名、递增正式版本、创建正式 release tag、清理原项目镜像或自动部署
- [ ] 复核目标宿主机当时的资源、已有 Compose 身份和监听端口，再请用户确认隔离部署参数。之前看到的 ARM64/4 CPU/约 15 GiB RAM 只是历史读数，不是容量承诺
- [ ] 测试访问优先已有 HTTPS 入口加独立测试域名，或 VPN/SSH 隧道；不一定需要新增公网 IP。此前候选测试域名没有创建，数据库和 Cockpit 管理端点不对公网暴露
- [ ] 部署前读 `C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm/deploy/UPDATE_RUNBOOK.md`，原项目不停止、不换镜像、不共用数据库/数据卷/主密钥。同机隔离不等于独立主机安全边界，尤其旧 Socket Proxy 可访问同一 Docker daemon

验收：本地 Compose config 与镜像构建通过后，获得单独批准才 push/发布/部署。记录可访问的实际 HTTPS 地址、鉴权方法、镜像 digest、健康状态、重启持久化和备份恢复。不把本地文档 ROLLBACK.sh 当服务器回滚证明

## 阶段 6：主代理独立验收，决定能否替代生产

- [ ] DS 交回两个仓库的 base/head、文件 diff、测试命令及完整 stdout/stderr/退出码、跳过/失败原因、接口/数据迁移清单和测试镜像 digest
- [ ] 主代理按阶段 1-4 的安全、功能和 UI 条件复跑，不直接采信 DS 的“全部通过”总结
- [ ] 隔离环境运行一轮可重复的错误注入、撤权、多 worker、重启/持久化、费用对账和恢复演练。Manager 不存在且请求链路仍可用是硬门槛
- [ ] 区分“可隔离试用”和“可替代生产”。有未验证关键协议/账号授权/计费/恢复路径时只能留在隔离环境，不能给“可以上线”结论
- [ ] 切换前另定维护窗口、写入冻结/最终增量、旧新 Key 归属、入口切换步骤、回退触发条件与数据回滚边界。保留旧服务不代表新环境写入的数据可自动回滚
- [ ] 用户明确批准后才执行生产替换；验收失败继续旧生产，不能一边故障一边扩大迁移范围

## 可复跑的本地基线命令

以下命令在本计划 C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm 执行；输出目录每次唯一，避免复用 pytest basetemp 覆盖已有运行。此处是操作说明，只有证据文件记载的 run 才算已执行

```powershell
$Root = 'C:/Users/42117/.codex/worktrees/cockpit-reuse-audit/litellm'
$Python = 'D:/Work2api/litellm/.venv/Scripts/python.exe'
Set-Location -LiteralPath $Root
$env:PYTHONPATH = "$Root;$Root/account-pool;$Root/account-pool/tests"
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:LITELLM_LOCAL_MODEL_COST_MAP = 'True'
& $Python -m pytest "$Root/account-pool/tests/account_pool/test_cockpit_scope.py" "$Root/tests/test_litellm/proxy/management_endpoints/test_account_pool_cockpit_route.py" "$Root/tests/test_litellm/proxy/management_endpoints/test_account_pool_cockpit_receipts.py" "$Root/tests/test_litellm/proxy/test_route_llm_request.py" -q
git diff --check
```

`make check` 在具备 GNU Make 的受控环境运行，遵守仓库队列锁，不调整槽位来绕开检查。记录实际命令与 commit，不将历史通过数相加。代码阶段修复完成后，由主代理提交已核验的精确文件列表

## 下一次主代理给 DS 的派工单

先完成阶段 0 的真实 Cockpit 定位与依赖矩阵，然后派阶段 1 最小可提取单元。派工中列完整文件路径、固定 HEAD、待保留签名、失败输入与期望状态；第一次只交补丁和测试，不允许改变业务默认值。DS 交回后主代理复核，再进入下一项，不把本次工具连通性测试等同于去 Manager 改造已开始或完成
