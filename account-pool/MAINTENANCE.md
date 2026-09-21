# 号池与 LiteLLM 解耦复用操作手册

本文记录号池整理解耦的操作方法、复用入口和校验经验，供后续维护及 Agent 执行同类任务时使用。适用范围包括独立号池服务、号池前端，以及 LiteLLM 密钥、日志、路由和运行设置页面对这些模块的引用

2026-09-20 用户收紧后续整理范围：只整理号池模块；LiteLLM 原生模块不继续解耦。号池接入代码按依赖进行验证，不能以复用或统一风格为由扩大到原生控制台和网关实现

开发约束见 [根目录规范](../CLAUDE.md)、[Manager 规范](AGENTS.md)和[前端规范](../ui/litellm-dashboard/CLAUDE.md)。本文保存操作细节及带日期的检查记录；规范文件只保留需要持续遵守的规则

## 职责与目标

整理的目标是让职责、依赖和复用入口清晰，同时保持现有运行行为。文件行数用于发现候选模块，拆分位置由事务、并发锁、外部契约和实际调用关系决定

| 层次 | 所属职责 | 整理时保持的边界 |
| --- | --- | --- |
| LiteLLM 网关与原生模块 | 虚拟密钥鉴权、模型路由、计价、用量及日志集成 | 复用原有机制，保留卡片范围和请求归属 |
| Manager | 环境生命周期、认证材料、供应商及额度、代理、上号和版本操作 | 保留锁、事务、幂等和失败补偿的完整流程 |
| CLIProxyAPI 通道及供应商适配 | 上游协议、网络调用、供应商响应解析 | 协议差异留在适配层，解析函数不反向依赖服务编排 |
| Dashboard | 页面路由、展示、表单及查询状态 | 页面编排与可复用功能分开，沿用现有控件和请求客户端 |

结构整理单独提交。默认值调整、功能修复、数据库迁移和部署另行说明及验证，便于判断行为变化的来源。仅移动镜像版本不能等同于恢复数据库、认证文件或日志数据

## 前端目录

`ui/litellm-dashboard/src/app/(dashboard)/account-pool/page.tsx` 保留原页面路由和编排，其余模块迁移至 `ui/litellm-dashboard/src/features/account-pool/`

| 目录 | 职责 |
| --- | --- |
| `api/` | 通过已有 `apiClient` 调用管理接口，复用 OpenAPI 生成类型 |
| `hooks/` | 查询键、环境查询、卡片操作、代理查询及共享下拉查询定义 |
| `utils/` | 格式化、权限、筛选、校验及展示数据计算 |
| `components/cards/` | 卡片、创建表单、批量操作及排序 |
| `components/credentials/` | 授权与认证文件 |
| `components/onboarding/` | 文件导入与 OAuth 上号任务 |
| `components/providers/` | 供应商目录和策略字段 |
| `components/dashboard/` | 仪表盘、额度与全局设置总览 |
| `components/releases/` | 版本、回退检查及确认 |
| `components/proxy/` | 代理管理 |
| `components/plugins/` | 卡片插件 |
| `components/upstream/` | 上游同步 |
| `components/shared/` | 号池内有多个调用方的展示控件，继续复用原生 UI 基础控件 |

测试跟随对应模块。密钥、日志、路由和运行设置页直接从 `@/features/account-pool/` 引用功能，不再依赖账号池页面目录。通用控件继续复用 `components/ui`，没有新增一套下拉、按钮或弹窗实现

所有账号池查询键由 `hooks/accountPoolQueryKeys.ts` 定义。代理和策略选项通过 `hooks/accountPoolOptions.ts` 复用；页面仍负责权限、加载时机、刷新间隔和缓存时长。版本命令列表刷新使用 `releaseCommandsRoot(token)`，具体版本查询使用 `releaseCommands(token, pairId)`，避免把带 `undefined` 的具体键误用为前缀

认证文件的查询、上传、替换、编辑和删除由 `hooks/useAccountPoolCredentials.ts` 编排，页面保留展示与文件输入引用。该 Hook 共用凭据与卡片缓存失效函数，但分别保留各操作的 `onSuccess` 和 `onSettled` 时机。认证文件与额度页面使用 `components/shared/AccountPoolRefreshIntervalSelect.tsx`，调用方仍提供默认值、保存请求和文案

控制台请求实现保留在原生 `components/networking.tsx` 中。此前拆出的 `src/lib/http/api-modules/` 已按用户要求通过 Git revert 撤销，后续不得再依赖该目录。号池继续使用已有 HTTP 客户端和自身 `features/account-pool/api/`，不需要改变原生请求模块的内部组织

提交 `2bccb7863c` 删除了经引用核对无调用方的 `AccountPoolScopePanel`、10 个前端 API 包装，并移除未使用的类型或缩小类型导出范围。服务端对应接口保留；现有页面使用的卡片插件、凭据删除、运行配置保存和镜像回退接口均保留

## 后端目录

| 路径 | 职责 |
| --- | --- |
| `account_pool/application/authorization.py` | 授权有效期和回调 URL 的纯计算 |
| `account_pool/application/environment_state.py` | 配置补偿和冷却状态判断 |
| `account_pool/application/profile_updates.py` | 命名配置与卡片原配置的差异计算 |
| `account_pool/application/plugin_validation.py` | 插件来源和版本校验 |
| `account_pool/application/environments/contracts.py` | 生命周期常量和显式回调协议 |
| `account_pool/application/environments/provisioning.py` | 创建环境及初始凭据验证 |
| `account_pool/application/environments/authorization.py` | OAuth、回调与授权验证恢复 |
| `account_pool/application/environments/auth_files.py` | 认证文件上传、替换、删除与状态修改 |
| `account_pool/application/environments/configuration.py` | 配置更新、版本对账及冷却检查 |
| `account_pool/application/environments/settings_sync.py` | 全局设置和策略同步及失败补偿 |
| `account_pool/application/environments/deletion.py` | 删除步骤及清理进度持久化 |
| `account_pool/application/environments/plugins.py` | 插件操作与校验编排 |
| `account_pool/channels/cliproxyapi/protocol.py` | 管理协议响应模型 |
| `account_pool/channels/cliproxyapi/transport.py` | 共用 HTTP 客户端的传输和鉴权 |
| `account_pool/channels/cliproxyapi/provider_quota.py` | 供应商额度探测、失败回退及串行凭据调用 |
| `account_pool/channels/cliproxyapi/provider_requests.py` | 供应商额度请求参数和错误信息提取 |
| `account_pool/channels/cliproxyapi/quota_state.py` | 主动额度、被动额度与缓存合并 |
| `account_pool/providers/usage/codex.py` | Codex 额度和订阅解析 |
| `account_pool/providers/usage/claude.py` | Claude 额度和套餐解析 |
| `account_pool/providers/usage/xai.py` | xAI 账单与额度解析 |
| `account_pool/providers/usage/common.py` | 复用 JSON、数值、时间和额度窗口解析 |
| `account_pool/providers/usage/contracts.py` | 额度观测、刷新结果和供应商错误类型 |
| `account_pool/providers/usage/antigravity.py` | Antigravity 项目、套餐、额度及汇总解析 |
| `account_pool/providers/usage/signals.py` | 各供应商响应头中的额度窗口解析 |
| `account_pool/application/quota_state.py` | 额度窗口有效性与冷却状态计算 |
| `account_pool/shared/` | 错误脱敏、结果类型与密钥基础设施 |

`EnvironmentService` 负责组装操作对象、共用环境锁及刷新入口；事务、授权和配置恢复以完整操作搬入 `application/environments/`。组合对象只接收所需仓库、通道与类型化回调，不使用 mixin、动态代理或独立锁。`HttpCLIProxyClient` 仍持有连接和凭据锁的生命周期，传输及供应商额度对象使用同一实例，保留原调用顺序。解析模块不导入客户端或服务编排

`account_pool.provider_quota` 继续作为原有公开导入入口。`AuthorizationStart`、`_AuthFile` 及现有测试使用的服务导入名称保持可用。旧的 `result`、`secrets`、`error_safety` 和 `cliproxy` 兼容入口继续保留，不能仅凭当前内部引用数删除

`account_pool.quota` 也保留为兼容入口，继续导出原来的领域类型、解析器和状态计算函数。内部调用直接依赖上述对应模块；供应商解析器通过 `providers.usage.contracts` 获取额度契约，避免为了结果类型反向导入加载所有解析器的兼容入口。Antigravity 的 JSON 校验复用 `common.parse_model`，旧 `_parse_model` 调用仍有效

## 操作流程

### 1. 记录起点

先检查工作区和分支，再执行快进拉取，记录起始提交、用户已有改动、涉及模块和已有检查失败。用户的未提交改动需要保留；构建产物和类型缓存也不能因为看似可再生成就直接覆盖或提交

```powershell
git status --short
git pull --ff-only
git rev-parse HEAD
```

明确本轮是审查、纯结构整理还是功能修复。输出目标目录、调用方和保持不变的契约，再执行对应范围内的工作。审查发现的其他问题进入待处理记录

### 2. 查清依赖与复用入口

先搜索定义、所有导入和调用方，再检查路由注册、配置字符串、动态导入、兼容导出、构建脚本和打包配置。测试、原生 LiteLLM 页面及外部调用者都可能是模块的使用者

```powershell
rg -n 'account_pool\.provider_quota|from .*provider_quota' account-pool litellm tests
rg -n 'accountPoolPolicyOptions|accountPoolProxyProfileOptions' ui/litellm-dashboard/src
rg -n 'releaseCommandsRoot|releaseCommands\(' ui/litellm-dashboard/src
```

| 需要实现的内容 | 优先复用位置 | 调用方需要保留的责任 |
| --- | --- | --- |
| 管理接口请求 | `apiClient`、`src/lib/http/`、功能目录的 `api/` | 鉴权、错误展示、原有幂等键与请求契约 |
| 策略和代理下拉列表 | `hooks/accountPoolOptions.ts` | 权限、加载时机、刷新间隔和缓存时长 |
| 查询及刷新 | `hooks/accountPoolQueryKeys.ts` | 用户隔离、具体查询键和失效范围 |
| 按钮、下拉、弹窗、表格和分页 | `components/ui/`、`components/shared/` | 业务字段、可访问性和保存反馈 |
| 供应商额度解析 | `providers/usage/` | 各供应商单位、套餐、重置窗口及未知值含义 |
| 鉴权、路由、价格和日志 | LiteLLM 已有模块及号池集成入口 | 卡片限制、模型归属、计价来源、脱敏和重试边界 |

共用逻辑需具有相同语义。额度未知不能统一当成零，供应商特有套餐不能仅因字段相似就套用另一个供应商的解析。只有多个真实调用方，或抽取明显降低现有复杂度时，才新增共享层

### 3. 按职责移动

每批选择一个职责，移动实现及其测试，同步更新全部引用，再验证该批。使用明确的业务目录；避免把多种职责重新堆进一个 `utils`，也避免按每个函数创建文件

前端路由入口保留页面编排，功能进入 `features/account-pool`。原生密钥、日志、设置页面直接引用功能目录。后端纯计算、协议模型和供应商解析可以提取，事务、锁、授权补偿及网络调用顺序继续由原编排者维护

保持公开导入路径的兼容层，并验证旧路径和新实现指向同一对象。函数体等价比较或 AST 比较可以辅助核对机械搬迁，但还需要检查模块初始化、相对导入、打包内容和真实调用方，避免只比较函数体就宣布兼容

### 4. 复用查询并核对交互

策略列表直接调用 `accountPoolPolicyOptions(accessToken)`，代理列表直接调用 `accountPoolProxyProfileOptions(accessToken)`。页面通过现有查询机制提供 `enabled`、缓存和刷新条件，保持原有权限及进入页面时的请求行为

版本操作完成后，刷新全部版本命令列表使用 `releaseCommandsRoot(accessToken)`。`releaseCommands(accessToken, pairId)` 表示具体版本查询；省略 `pairId` 产生的尾部 `undefined` 不等于所有版本的前缀

查询缓存保存数据，动态导入控制代码加载，保留挂载状态保存组件状态。这三种机制分别验证。检查首次进入、切走再返回、保存后返回、切换用户和退出登录，保证显示新数据且不串用身份

Hooks 只能在组件或自定义 Hook 顶层执行。列工厂需要翻译函数时，由组件取得 `t` 后传入，或把工厂转换为顶层调用的自定义 Hook。仅把函数改成 `use` 开头、仍放在 `useMemo` 回调中，不会修复调用顺序问题。涉及此模式时增加稳定回调下的二次渲染检查，并检查排序或语言切换

### 5. 删除已证实的冗余

Knip、引用数量和重复片段扫描提供待核实线索。删除前逐项确认配置入口、动态加载、兼容路径和对外契约，优先缩小多余导出或删除确定无调用方的包装

本次 Knip 曾把 `tests/setup.unit.ts` 和 `tests/setupTests.ts` 报为未使用；它们由 Vitest 配置引用，必须保留。前端包装无调用方也不能作为删除服务端接口的依据。扫描器的自动修复只在已确认范围内使用，随后复查差异

### 6. 验证、提交及交接

先运行当前职责及其调用方的检查，再按实际影响扩大范围。发现失败时，对照起始提交、运行环境和既有日志区分新回归、原有问题、测试过时和环境限制。不要通过跳过用例、放宽类型或修改业务默认值来掩盖失败

检查结束后执行 `git diff --check`，审阅待提交文件，排除密钥、认证文件、个人配置和生成缓存。使用中文 Conventional Commit 描述具体职责调整，按任务要求推送。提交、镜像构建、部署和线上验证分别记录结果

## 验证命令与诊断

以下命令按修改范围选用。纯文档调整只检查内容、路径、格式及差异；无需重复运行业务测试

### Manager 与网关

后端完整回归在仓库根目录运行，确保同时能导入 Manager 和 LiteLLM。只有号池独立虚拟环境时，跨服务模型契约测试会缺少 LiteLLM 导入路径

```powershell
.venv/Scripts/python.exe -m pytest account-pool/tests -q
```

修改涉及 LiteLLM 集成、共享契约或请求处理时，补跑网关测试。下面使用 PowerShell 数组明确选择号池测试文件，避免启动全仓库测试

```powershell
$gatewayTests = @(rg --files tests/test_litellm/proxy/management_endpoints -g 'test_account_pool_*.py')
.venv/Scripts/python.exe -u -m pytest @gatewayTests -n 2 --dist=loadscope --max-worker-restart=0 --timeout=120 -q
```

并行参数需要当前环境安装 `pytest-xdist`，超时参数需要 `pytest-timeout`。并行执行前确认所选用例没有互相依赖的外部状态；不适合并行的用例单独串行运行

静态检查按用途运行；未定义名称检查通过只说明该类错误未被检出，数据库迁移扫描通过也不能代替真实数据库升级或回退检查

```powershell
.venv/Scripts/python.exe -m ruff check litellm account-pool/account_pool enterprise/litellm_enterprise --select F821,F822,F823
.venv/Scripts/python.exe tests/code_coverage_tests/check_migrations_no_data_rewrites.py
```

测试长时间无输出时，先检查进程、日志及当前用例，用非缓冲输出、详细报告、持续时间统计或 `faulthandler_timeout` 定位。已确认仍有进展的任务继续运行；确需停止时只终止本次命令及其子进程，避免留下后台测试

本次有两类耗时：测试初始化会通过远程网络读取模型价格表；旧卡片 Key 的 429 同卡重试会实际等待至少 60 秒。诊断价格表网络依赖时，可仅在测试进程设置 `LITELLM_LOCAL_MODEL_COST_MAP=True` 使用本地表，并在报告说明，远程价格更新能力需另行验证。重试测试应使用可控时钟或等待依赖；未改测试前，时限需要覆盖已知冷却时间。测试进程因过短时限退出不能直接认定请求逻辑崩溃，也不能算测试通过

### Dashboard

前端相关测试在 `ui/litellm-dashboard` 下运行。Vitest 路径过滤使用不带路由组括号的片段，确保首页测试被实际选中

```powershell
npx vitest run --project unit --project component --project integration src/features/account-pool src/components/Settings/RuntimeSettings account-pool/page.integration.test.tsx --maxWorkers 2
npm run test:types
npx eslint src/features/account-pool 'src/app/(dashboard)/account-pool' src/components/Settings/RuntimeSettings
```

涉及原生密钥、日志、路由页面的导入时，补跑对应调用方用例，并核对实际收集到的文件及数量。避免只凭命令退出码判断页面测试被覆盖，也不要使用无路径限制的 `npx vitest run`

扩大检查时分别记录以下命令的结果，Knip 和 ESLint 的已有告警也要保留，不在审查中自动删除或修改基线

```powershell
npx tsc --noEmit --incremental false
npx eslint . --quiet
npx knip --reporter json
npm run build
```

| 检查 | 能说明什么 | 需要单独说明的限制 |
| --- | --- | --- |
| 全量 `tsc` | 当前 TypeScript 配置纳入的文件是否类型正确 | 测试和本地预览文件可能同时被纳入 |
| 生产源码类型检查 | 指定生产文件范围的类型结果 | 记录排除项，不能代替全量检查 |
| Vitest 类型项目 | 被实际收集的类型用例结果 | 当前 `ignoreSourceErrors: true` 会忽略其他源码错误 |
| Next.js 构建 | 对应构建方式能否编译并生成页面 | 16.2.11 会过滤测试文件类型错误，构建不证明浏览器交互正常 |
| 组件及集成测试 | 断言覆盖的行为与模块连接 | Mock 上游、缺少数据库的运行不证明真实服务可用 |

生产构建可能写入 `.next`、`out` 和 TypeScript 缓存。已有未提交产物时，使用隔离工作区或临时源码副本，并使用与项目一致的依赖。若因依赖链接或平台限制改用 `npm run build -- --webpack`，记录这一参数，不把该结果写成默认 Turbopack 或全新镜像构建已通过

只在实际修复受抑制的 lint 问题时更新相应基线，并检查差异。纯移动或审查过程中不要顺带执行全库自动修复

## 本次结果与待处理项

以下记录对应 2026-09-20、提交 `2bccb7863c`，用于回溯，后续提交需要按影响重新验证。该次整理没有修改数据库结构、HTTP 路径、请求和响应字段、Docker 配置、计费规则、网关路由、重试顺序或签名处理，也没有部署生产服务器

| 检查范围 | 当时的结果 |
| --- | --- |
| Manager | 498 项通过 |
| 号池及运行设置前端 | 130 项通过 |
| 密钥创建、日志调用方 | 分别 74 项、21 项通过 |
| LiteLLM 号池网关 | 限时批次中 272 项通过、1 项 PostgreSQL 检查跳过；超时及未运行的 4 项单独复跑通过，合计 276 项通过 |
| 类型与构建 | 生产源码类型检查通过；Vitest 类型用例 4 项通过；隔离副本 webpack 构建生成 52 个页面 |
| 静态及打包 | Python 未定义名称检查通过；162 份迁移未检出违规数据重写；Manager wheel 构建通过；208 个搬迁定义做过 AST 等价核对 |
| 冗余与 lint | 号池范围 Knip 无未使用文件或多余导出，相关范围 ESLint 无错误；全库 ESLint 有 15 个错误，涉及 7 个文件，另有过期抑制项 |

全量 TypeScript 检查有 1401 条错误，其中 1398 条来自 66 个测试文件，另 3 条来自本地 `out/rollback-qa/main.tsx`。相邻路由设置的 4 项测试与中文文案或会话亲和性默认值不一致；扩展页面检查另有 12 项旧文案断言失败。这些结果与生产构建通过分别记录

| 待处理项 | 已确认的表现 | 后续验证方向 |
| --- | --- | --- |
| 表格 Hook 调用位置 | `CredentialsTableColumns.tsx`、`PassThroughEndpointsTableColumns.tsx`、`HealthChecksTableColumns.tsx` 在缓存回调中调用 `useTranslation`，稳定回调下二次渲染均复现 Hook 数量错误 | 修复顶层调用及依赖传递，验证二次渲染、排序和语言切换 |
| 测试与质量检查 | 全库类型及 lint 检查未通过，部分断言仍使用旧文案或默认值 | 逐项判断配置问题、过时断言和真实回归，保留断言强度 |
| 旧卡片 Key 重试耗时 | 模拟 429 的用例实际等待 60.06 秒；初始化另耗时 26.42 秒 | 分开测量初始化、上游请求、冷却和重试；虚拟 Key 的 429 交回 LiteLLM 处理，不能混用结论 |
| 进一步解耦 | `EnvironmentService` 约 2300 行，前端 `networking.tsx` 约 8100 行 | 按实际职责和调用方逐步拆分，保留事务、并发和故障恢复边界 |

这份文档的补充没有修复上述待处理项。审查时本地 Docker 引擎未启动，真实数据库、容器启动及线上 Key 调用未验证。号池 Compose 配置静态解析通过，不能据此推断镜像启动、真实调用或数据回退兼容已验证

## 交接记录

每轮交接记录起始与完成提交、移动路径、复用入口、删除依据、兼容方式、检查命令与实际结果、未完成项，以及是否已推送、构建或部署。临时校验脚本和 `.git/` 内日志只用于当前工作区取证；后续维护不能依赖这些未提交文件，文档中应留下可重新执行的命令

后续修改目录或公开入口时，同时更新本文目录表。问题修复后在新的日期及提交下补充验证结果，保留历史记录，避免把一次局部整理写成全项目清理完毕

## 2026-09-20 续轮记录

本节为历史记录：其中原生控制台的 `6270748365` 和 `6b0f4ca8a9` 已在后续回退中撤销；下列测试结果仅描述当时版本。号池后端拆分保留，当前回退状态见文末

本轮从 `f35f3f83b2` 的未提交修复继续，快进拉取确认上游没有新提交。Windows Git 的默认 TLS 后端首次握手失败，使用单次命令 `git -c http.sslBackend=openssl pull --ff-only` 成功，没有修改全局配置

代码提交为 `6270748365`（表格 Hook、类型 fixture、共享护栏类型及测试语言），`6b0f4ca8a9`（网络请求模块拆分），`ca38cc5613`（环境服务与 CLIProxyAPI 客户端拆分）。以下验证针对这三次提交组成的工作树，不将上轮的通过次数计入本轮

### 变更与兼容

三个表格把翻译 Hook 移到组件顶层，列工厂接收翻译函数，缓存依赖包括语言变化。回归覆盖加载状态变化后的第二次渲染、排序和语言切换。测试公共授权数据改为类型化 fixture；预算编辑器仅补全既有新旧字段的输入类型，运行时字段处理没有改变

全量 TypeScript 继续检查测试源码，通过 `tests/globals.d.ts` 引入 Vitest 全局类型，仅排除生成目录 `out`。组件测试默认使用英文，号池目录使用中文；中文运行日志测试显式设定中文。语言切换用例继续操作真实 i18n 实例，不靠屏蔽翻译绕过断言

`networking.tsx` 从 8135 行缩为 414 行，`service.py` 从 2327 行缩为 795 行，CLIProxyAPI `client.py` 从 1323 行缩为 776 行。兼容入口保留原函数、类型和调用参数，新增网络测试验证兼容入口与直接导入共用客户端，并在切换 Worker 后使用新的 URL 和鉴权头。152 项原有 raw-fetch 抑制随实现进入允许传输的 `lib/http/` 后移除；其余需要保留的规则按实际文件搬迁，没有提高原有违规总额

后端用不可变 dataclass 组合操作，依赖由原服务注入；原环境锁、凭据所有权、仓库、网络连接和凭据请求锁均共用。操作内部的条件持久化、锁作用域、补偿和调用顺序没有拆散。前端 395 个顶层定义及后端 135 个方法做过 AST 核对，排除导出修饰和内部方法重命名后实现保持一致。AST 核对只是迁移证据，仍结合以下回归和构建结果判断

### 本轮验证

| 检查 | 命令或范围 | 结果 |
| --- | --- | --- |
| 前端源码与测试类型 | `npx tsc --noEmit --incremental false` | 通过 |
| Vitest 类型项目 | `npm run test:types` | 4 项通过，另一个收集文件含 0 项类型用例 |
| 本轮修改及直接调用方 | 显式列出测试路径，`npx vitest run ... --maxWorkers=2` | 47 文件、1135 项通过 |
| 号池、密钥创建和日志 | 号池 feature、页面及密钥和日志测试共 33 文件 | 232 项先通过，运行日志修正测试语言后该文件 4 项通过，最终覆盖 234 个不同用例 |
| 运行设置 | `RuntimeSettingsSection`、`RuntimeSettingsProfileSection`、`RuntimePolicyDialog` | 3 文件、11 项通过 |
| ESLint | `npx eslint . --prune-suppressions`，随后检查本轮文件 | 全量 0 错误、2640 警告；变更范围 0 错误 |
| Manager | `.venv/Scripts/python.exe -m pytest account-pool/tests -q` | 最后一次 498 项通过 |
| 原生号池网关 | 本文网关命令，2 个 worker、120 秒用例超时 | 276 项通过，1 项需要 PostgreSQL 的检查跳过 |
| Next.js | 隔离源码副本中 `npm run build -- --webpack` | 52 页面构建通过；字体 TLS 重试导致编译耗时 17.6 分钟 |
| Python 未定义名称 | 本文 `ruff --select F821,F822,F823` 命令 | 通过 |
| 数据迁移扫描 | `check_migrations_no_data_rewrites.py` | 162 份迁移通过，本轮未修改迁移 |
| Manager 打包 | `uv build --wheel --out-dir .git/continuation-wheel ./account-pool` | wheel 成功，101 个条目，包含新环境操作和传输模块 |

Manager 和网关测试进程设置 `LITELLM_LOCAL_MODEL_COST_MAP=True`，使用仓库价格表以排除测试初始化时的远程读取。所有原始结果记录在工作区 `.git/continuation-*.log`，后续执行以本文命令重新验证，不依赖这些本地日志仍然存在

### 残留与边界

Python 严格类型检查并未全绿：相同配置和导入路径下，搬迁前两个原文件有 63 条诊断，搬迁后入口及新模块有 70 条。按规则和消息比较没有新的诊断内容，7 条增量是旧私有名称在多个模块被引用后重复报告。现有问题涉及私有名称跨模块使用、旧兼容重载的类型收窄、JSON 输入类型及部分通道协议定义；后续需要独立完善契约和回归，不能把本次未定义名称检查通过写成严格类型检查通过

2640 个前端 lint 警告也没有在本轮全量清理。仍未进行真实供应商 Key、生产数据库升级或容器启动验证；本轮没有修改生产部署、路由、计费和重试默认值，也没有部署服务器。用户原有 `tsconfig.tsbuildinfo` 改动完整保留且未提交

### 维护经验

搬迁可变模块状态时必须保留 ES module 实时绑定，不能把 `proxyBaseUrl` 复制为初始化快照，也不能在每个业务文件构造新客户端。用跨模块请求测试验证切换地址和自定义鉴权头，比只断言导出存在更有效

类拆分使用显式协议和依赖注入，让调用方持有锁与连接的生命周期。将内部方法改成可跨对象调用的名称前先查同名方法；本轮 `_create_direct_credential_environment` 去掉下划线曾与公开方法冲突，回归检出后改为 `provision_direct_credential_environment`，再次通过完整 Manager 回归。机械搬迁校验必须检查名称唯一性，不能仅把定义放进字典而静默覆盖

共享测试 fixture 引用真实角色工具时，调用方应部分 mock 模块并保留其余导出。固定测试语言与生产默认语言是不同职责，不应为旧英文断言改变中文产品默认值。新密钥默认隐藏的测试先点击显示按钮，再检查内容，保留实际用户操作流程

## 2026-09-20 号池范围续轮

起始提交为 `6d97d0b071`，快进拉取无新提交。本节随本轮实现一起提交；范围仅为 `account-pool/` 和前端 `features/account-pool/`，没有修改 LiteLLM 原生模块。用户已有 `tsconfig.tsbuildinfo` 改动保留且不提交

额度模块原先同时包含供应商协议模型、响应头解析、Antigravity 响应解析和路由状态计算。本轮将 37 个定义按目录表分离，机械搬迁时逐个核对 AST，再让 Antigravity 的四处 JSON 校验复用已有 `common.parse_model`。窗口过期、未知额度、手动冷却、套餐和模型汇总优先级保持原实现。内部服务使用直接导入，旧 `quota` 路径保留公开类型和解析器

认证文件页面将操作编排移到专用 Hook，凭据与环境刷新复用同一个函数。上传无论成功或失败均刷新缓存，编辑、删除和启停继续仅在成功后刷新。保留同卡替换、不允许向已有凭据卡片再次上传、失败后留在弹窗重试、取消时清空文件、按凭据索引与卡片版本删除等行为。刷新档位控件复用原生 Select，两个页面继续使用各自默认值和接口，没有统一其查询或轮询策略

### 验证记录

| 检查 | 命令或范围 | 本轮结果 |
| --- | --- | --- |
| 修改前基线 | `pytest account-pool/tests -q`，相关两个前端测试文件 | Manager 498 项、前端 8 项通过 |
| Manager | `pytest account-pool/tests -q`，设置 `LITELLM_LOCAL_MODEL_COST_MAP=True` | 504 项通过 |
| 额度解析 | `pytest account-pool/tests/account_pool/test_quota.py -q` | 26 项通过，含旧导出对象一致性和异常 JSON 回退 |
| 号池网关 | 本文网关命令，显式 `test_account_pool_*.py`，2 workers | 276 项通过，1 项需要 PostgreSQL 的检查跳过 |
| 前端相关行为 | `npx vitest run src/features/account-pool/components/credentials/AccountPoolCredentialsPanel.integration.test.tsx src/features/account-pool/components/dashboard/AccountPoolQuotaPanel.test.tsx --maxWorkers=2` | 11 项通过，新增编辑字段校验、删除索引与版本、切换 Token 查询隔离 |
| 前端类型 | `npx tsc --noEmit --incremental false` | 通过，不写用户类型缓存 |
| Python 严格类型 | `basedpyright` 检查 `quota.py` 与四个新模块 | 0 错误、0 警告 |
| Python 静态检查 | 本轮 Python 文件的 Ruff `F821,F822,F823,I` | 通过 |
| 前端 lint | 本轮五个 TS/TSX 文件的 ESLint | 0 错误、10 条既有或随实现移动的警告 |
| 未使用代码扫描 | `npx knip --reporter json`，筛选号池路径 | 号池未使用文件和问题条目均为 0；全库其他条目不在本轮处理范围 |
| Manager 打包 | `uv build --wheel --out-dir .git/pool-scope-wheel ./account-pool` | 构建成功，105 个条目，四个新模块均在包内 |

本轮未执行 Dashboard 生产构建、Docker 启动、真实供应商调用或服务器部署。严格类型检查仅覆盖表中指定模块；将既有 `common.py` 也作为独立检查目标时仍有 8 条私有辅助函数“未使用”诊断，其调用方实际在其他供应商模块中，因此没有按报告删除。不能将局部类型通过写成整个 Manager 严格类型通过

### 复用边界

检查兼容导出时也要检查原文件导入后间接暴露的类型。本轮最初只保留本地定义，Manager 测试发现 `ProviderEndpointFailure` 的旧导入路径丢失；补回领域类型导出后，完整回归通过。新增契约层可以避免各解析器为了获取结果类型反向加载整个兼容入口

管理 API 中相似的错误处理未合并：`api._unwrap` 按错误类型返回 404、409、422 或 502，`management_api.unwrap` 则固定返回 409。OAuth 上号接口另有明确的 404/409 契约，不能仅因代码相似就改变错误映射。路由注册、事务和补偿集中在同一编排模块的部分也不按文件行数强行拆散

Windows 上机械迁移应保留原文件换行，避免只改一个导入却出现全文件差异。临时脚本和详细日志位于本地 `.git/pool-scope-*`，不作为仓库运行依赖；后续按上述命令重新验证

## 2026-09-20 撤销原生控制台整理

按用户要求，从 `99de1839c7` 执行 `git revert --no-commit 6b0f4ca8a9` 和 `git revert --no-commit 6270748365`，以新提交保留撤销记录，不改写已推送历史。两次撤销均无冲突；涉及的 98 个文件与 `f35f3f83b2` 对比一致

撤销范围包括公共请求层的 35 个拆分模块、原生表格 Hook 修复、护栏共享类型、原生预算输入类型、Playground/MCP 表达式清理，以及同批次测试、类型配置和 lint 基线调整。`networking.tsx` 恢复原实现；原生请求路径和更早的号池功能集成没有回退

保留 `e561c19fc7`、`2bccb7863c`、`ca38cc5613`、`99de1839c7` 中的号池解耦，包含 `features/account-pool/`、环境服务组合、供应商额度模块、认证文件 Hook 和刷新控件。原生页面连接号池新目录所需的导入调整及共享号池查询继续保留。对比确认号池前后端实现相对回退起点没有变化

### 回退验证与已知限制

显式选择 25 个号池 feature 测试文件，以及号池页面、原生请求层、密钥创建、日志和运行设置的 9 个调用方文件，执行 `npx vitest run ... --maxWorkers=2`，34 文件、304 项通过。其中恢复后的 `networking.test.ts` 有 41 项通过

生产源码类型检查通过：使用临时 TypeScript 配置继承控制台配置、关闭 incremental，仅纳入 `next-env.d.ts` 和 `src/**/*.ts(x)`，排除 `*.test.*`、`*.test-d.*`。该检查未写入用户已有 `tsconfig.tsbuildinfo`，也未改变仓库的类型检查范围

隔离副本 `.git/native-revert-build/dashboard` 中执行 `npm run build -- --webpack` 成功，生成 52 个页面；编译阶段耗时 13.4 分钟。副本使用原控制台依赖和配置，未覆盖工作区 `.next`、`out` 或类型缓存。构建结果不代替上面的全量类型检查，也不代表已验证所有浏览器交互

全量 `npx tsc --noEmit --incremental false` 未通过，恢复了整理前的 1401 条诊断：1398 条来自测试文件，3 条来自本地 `out/rollback-qa/main.tsx`。原生三个表格的 Hook 问题也随修复提交的撤销恢复，不能将本次 304 项通过描述成这些问题已经解决。此次回退不重新加入用户要求撤销的原生修复

本轮没有修改 Python 实现、数据库、认证文件、运行日志或服务器部署，没有重新运行此前的 Manager 和网关回归，也没有进行真实供应商调用。历史检查结果仍留在上文，只对各自当时版本有效

## 2026-09-21 验收与单独修复

从 `5b6645fd82` 开始验收。该提交已单独修复三个原生表格的 Hook 调用顺序，保留原文件结构；修复前三个稳定回调二次渲染用例均复现错误，修复后对应 33 项测试通过。上节记录的表格缺陷不再是当前待修项

本轮只将 `check_openapi_schema.tsx` 的帮助文字容器改为 `span`，避免在 `FieldDescription` 的 `p` 内嵌套 `div`。在现有创建密钥集成测试中增加无非法嵌套错误的断言，修复前失败，修复后通过；未改变字段值、提交契约或公共表单组件

本轮重新执行 Manager 全部测试，504 项通过；号池网关测试 276 项通过，1 项需要真实 PostgreSQL 的检查跳过。显式选择号池 feature、页面、请求层、密钥、日志、运行设置和三个表格的前端测试，实际收集 36 个文件、302 项通过。生产源码 TypeScript 检查通过，排除测试文件且不写增量缓存；修改范围 ESLint 无错误，保留 `defaultValues` 依赖的已有警告

这些结果仅证明上述自动化检查。线上实际流程、真实供应商请求、镜像发布和部署必须另行记录结果，不能用本节测试数量代替。全库测试类型和后端严格类型欠账不在本轮扩展修复范围

### 2026-09-21 服务器验收补充

代码版本 `3f86b651da4b5b31c4b4006cb5a29d424b934624` 的两份业务镜像由 GitHub Actions 运行 `35524042634` 构建成功，随后部署至服务器。部署前版本为 `dbbe36cc3a5990fc95ae613880d3030a5f2c4727`。本轮没有更改 LiteLLM 后端源码，三个表格修复与密钥帮助文字修复都保持原文件结构

实际发布镜像核对得到 233 项结构条目一致，包含 36 条 DDL。仓库与配置定义、加密类、Prisma、日志实现和迁移启动证据一致。环境操作 88 个方法及客户端 46 个方法的签名和实现经规范化内部名称后逐项一致；另一个提取为函数的配置计算核对函数体一致。构造器组合、依赖注入和调用转发结合 Manager 回归验证，不能将 AST 对比扩大为所有运行行为的形式化证明

旧 release-worker 的结构指纹含源码文件名，其凭据证据还直接比较整个服务文件；文件搬迁会造成兼容性误报。本次未关闭或修改此校验。仅针对上述确定的两个版本，核对实际镜像后复用 `ReleaseService.backup/verify`、`DockerReleaseRuntime.apply` 和失败恢复流程切换。旧版恢复点为 `bc32a4dda489c57110f25220`，新版完整备份为 `b2fd6573f97473873c440222`；两份镜像归档与部署配置均已校验。数据库容器 ID 和启动时间、卡片 ID、启停与模型列表保持一致，数据库、认证文件和日志没有随镜像恢复或清空

这两个版本之间的网页自动回退仍会被旧校验阻止，不应描述为已修复。服务器 `/opt/litellm-releases/acceptance_deploy.py` 保存固定版本的核验恢复入口：在 release-worker 内执行 `python /opt/litellm-releases/acceptance_deploy.py --recover --verify-only` 只检查；去掉 `--verify-only` 才会切回该旧版镜像。恢复入口验证当前 commit、目标备份及核验记录，失败时尝试恢复切换前版本；它不适用于其他版本，也不代替通用兼容检查的后续修复。恢复校验已执行，未为了测试再次中断生产服务

通过公网域名创建临时虚拟 Key，分别测试指定卡片和不限卡片。4 次模型列表请求返回 200，10 次真实推理请求返回 200，2 次未授权模型请求按预期返回 403。推理覆盖 Chat Completions 普通与流式、Responses 普通与流式、GPT-6 Astra，以及包含真实加密思考块的 Sol 同模型续聊和切换 Terra 续聊。此次推理总耗时约 1.46～3.03 秒，流式首次响应约 1.38～1.73 秒；这是少量验收请求，不是负载测试或延迟保证

10 次成功请求均有运行日志、完整日志和已知费用，合计约 0.015738 美元；通过 Prisma 只读查询生产 `LiteLLM_SpendLogs` 确认同样的 10 次成功计费与两次零费用拒绝记录。两把临时 Key 已删除，数据库查询剩余数量为 0。仪表盘等 11 个管理接口全部返回 200，调用统计实际读取 LiteLLM PostgreSQL。此前被跳过的 pytest 用例未伪记为通过，这些线上查询另作为真实数据库验证

浏览器通过 SSH 本地转发访问同一个线上控制台，正常登录后检查仪表盘、原地单卡配置和账号策略、提供商、OAuth、认证文件、自动化上号供应商目录、额度、代理、上游更新、版本备注与全局总览。配置弹窗以取消或关闭退出，没有保存账号变更；全局总览可跳转日志设置。运行日志和完整日志均实际跳到第 2 页，运行日志操作了每页 25 条选项；完整日志按本次会话筛选出 7 次请求，可展开本轮与完整上下文，并触发会话 JSONL 导出。未读取下载文件，因此不将按钮点击写成已核验下载文件完整性

补跑 `FullLogsPanel.integration.test.tsx`、`conversationExport.test.ts`、`logConversation.test.ts` 共 3 文件、14 项通过，覆盖分页参数、导出跨页收集及中途中断。创建虚拟 Key 的线上表单能列出 3 张卡片及模型数量；另建一把 5 分钟、零预算测试 Key，通过 `/key/reveal` 取回内容并在内存比较与创建值相同，随即删除。浏览器默认遮盖正常，但本次未稳定观测到展开后的密钥状态，不能声明浏览器显隐与剪贴板全流程已验收；另补跑 `VirtualKeySecret.integration.test.tsx`，4 项通过

保留的已知状态：plus02 在部署前已认证失效并冷却，Grok 在部署前已停用且额度读取曾被上游浏览器验证拦截。未重新授权、启用或修改这些卡片。本轮只有一张健康卡，未完成两张健康卡之间的真实故障切换；没有外部中转站凭据，未验证跨站签名续聊。旧版一次 GPT-6 Astra 请求返回 400，新版同请求成功，不能仅凭这两次结果认定该间歇问题由本轮修复

### 2026-09-21 跨站签名恢复的格式兼容与诊断

起点为 `3f4f80ae5e30d5e4ff992bef5806f9347133e6db`，本轮读取到的生产镜像仍为 `3f86b651da4b5b31c4b4006cb5a29d424b934624`。已确认报错方向为 Codex 桌面端在原会话中从其他中转站切回本项目。北京时间 01:56:45 的运行记录显示 `/v1/responses`、`gpt-6-astra` 返回 400，恢复说明为 `signature_recovery: unsupported_history; same_card=true`。该请求的完整日志未保存，同会话也没有可读的完整记录，因此不能将具体原因认定为压缩块、工具格式或某个客户端字段

对照 New API `df43f801536b348b00bfa4da7639b42c2c036821`：`setting/operation_setting/channel_affinity_setting.go` 使用 `prompt_cache_key` 固定 Responses 会话渠道；`relay/common/override.go` 支持根据重试上下文条件清理对象。但未找到 OpenAI 加密思考签名专用修复，`normalize_thinking_signature` 在其测试中明确是不支持的操作。渠道绑定不能恢复其他站点的加密状态；其多 Key 渠道还会独立选取 Key，不能直接等同于绑定加密状态所属账号。本项目继续复用 LiteLLM 原生亲和性与思考块清理函数，没有移植另一套路由器

修改范围为既有签名恢复辅助函数、号池 HTTP/SSE 转发与错误展示。按本地 OpenAI SDK 2.33.0 的类型定义，补齐 Responses 拒绝回答、命名空间内的函数和自定义工具，以及 Chat 自定义工具、空 tools/tool_calls、拒绝回答、音频和文件内容。清理思考块时保留同条消息上的工具调用和拒绝回答，不删除原本为空的内容。Anthropic Messages 继续使用原生清理函数，成对工具调用及结果保留。普通请求成功时转发内容不因恢复功能而改变

只有上游明确拒绝签名且尚未输出时，才允许同一卡片、同一凭据清理旧思考状态后重试一次。压缩上下文、服务器消息引用、服务器状态、未知工具和不完整工具历史继续拒绝自动重建；只保留加密上下文时，不能声称清理后仍有完整会话。HTTP 与 SSE 返回中文恢复原因，运行日志记录协议类别与数组位置，不记录用户正文、签名或未知类型原值。该机制不包含 WebSocket 会话重建，也不提供跨站解密能力

新增回归在修改前有 10 项失败、2 项通过，修改后覆盖三种接口的 HTTP/SSE 请求、可见历史与工具保留、压缩块/消息引用拒绝、错误信息脱敏。号池网关完整测试在工作树运行 289 项通过、1 项跳过；跳过项需要 `ACCOUNT_POOL_STATS_TEST_DATABASE_URL`，不可视为数据库验收。最后收紧命名空间只能包含函数/自定义工具后，重跑签名与加密相关测试 36 项通过。Manager 504 项通过；四个源码文件的 Ruff、格式检查与 basedpyright 均通过，类型检查为 0 错误、0 警告。日志保存在本地 `.git/signature-*.log`

本轮没有部署新镜像、修改线上日志设置或重放用户私密请求，没有外部站点凭据进行真实跨站验收。官方文档抓取返回 403，格式核对依据为本地 SDK 源码。不能将本轮模拟上游的回归测试或上一轮真实普通调用验收，描述为这条 Codex 旧会话已经恢复；部署后应根据具体错误位置或成功续聊记录继续核验

### 2026-09-21 程序回退检查与强制确认

起点为 `534746f086`，本轮仅修改版本管理。回退始终只替换 LiteLLM 与 Manager 镜像，沿用当前环境变量、加密配置、数据连接和挂载；不恢复历史数据库、认证文件或日志，也不增加数据快照恢复流程。release-worker 独立更新，不随业务镜像回退

结构指纹按定义汇总，不再包含源码路径或文件分组；字段、枚举、别名和 DDL 差异仍保留。凭据检查提取实际密钥派生与状态加密类，并比较数据库凭据存储和 LiteLLM 加密实现，不再比较整个 EnvironmentService；此项不代表上游 OAuth 有效、CLIProxyAPI 认证文件协议或所有行为兼容。重复文件名按路径保留，避免扫描时覆盖。旧备份的镜像和配置校验和仍必须通过，历史指纹不再充当执行门槛；重新从镜像提取依据，检查本身不重写原备份。镜像已被清理时允许从已核验归档导入后检查，不启动目标容器

普通回退只接受全部检查通过。未验证项可通过管理员的强制回退执行，网页须输入目标版本，再获取绑定完整目标 commit 的新票据，等待十秒并二次点击确认。备份损坏、缺失、危险迁移启动参数、检查证据不可读取、确认过期及检查后配置变化仍拦截。强制标志只允许 apply 操作，后台入队及切换前重新核验；失败时尝试恢复切换前程序镜像和配置，不承诺能撤销旧程序已产生的数据写入

构建前验证：Manager 512 项通过，网关 290 项通过、1 项真实 PostgreSQL 测试跳过；回退网页 8 项集成测试通过，生产源码 TypeScript 检查通过。新增接口字段通过 `npm run gen:api` 生成；六个相关后端源码 basedpyright 为 0 错误、0 警告，Ruff 通过；前端 ESLint 无错误，保留 9 条复杂度和表达式警告。最后另补镜像清理后从归档检查的回归，结果另记。详细日志位于本地 `.git/rollback-*.log`

在生产旧 release-worker 中独立进程加载新检查器，只检查现有归档，未切换服务：`dbbe36cc3a`、`8ec5678b6e` 与当前 `3f86b651da` 的数据库、号池状态、凭据和日志证据一致，仅部署配置不同；更早五份备份显示实际定义或功能差异。未用强制回退在生产执行已知数据定义不同的历史版本。以上为镜像静态检查，不是连接实际数据库验证所有字段和值；不能据此保证任意旧版兼容。真实 Codex 跨站旧会话由用户部署后自行复测
