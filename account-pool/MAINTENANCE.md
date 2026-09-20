# 号池与 LiteLLM 解耦复用操作手册

本文记录号池整理解耦的操作方法、复用入口和校验经验，供后续维护及 Agent 执行同类任务时使用。适用范围包括独立号池服务、号池前端，以及 LiteLLM 密钥、日志、路由和运行设置页面对这些模块的引用

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

测试跟随对应模块。密钥、日志、路由和运行设置页直接从 `@/features/account-pool/` 引用功能，不再依赖账号池页面目录。通用控件继续复用 `components/ui`，没有新增一套下拉、按钮或弹窗实现

所有账号池查询键由 `hooks/accountPoolQueryKeys.ts` 定义。代理和策略选项通过 `hooks/accountPoolOptions.ts` 复用；页面仍负责权限、加载时机、刷新间隔和缓存时长。版本命令列表刷新使用 `releaseCommandsRoot(token)`，具体版本查询使用 `releaseCommands(token, pairId)`，避免把带 `undefined` 的具体键误用为前缀

提交 `2bccb7863c` 删除了经引用核对无调用方的 `AccountPoolScopePanel`、10 个前端 API 包装，并移除未使用的类型或缩小类型导出范围。服务端对应接口保留；现有页面使用的卡片插件、凭据删除、运行配置保存和镜像回退接口均保留

## 后端目录

| 路径 | 职责 |
| --- | --- |
| `account_pool/application/authorization.py` | 授权有效期和回调 URL 的纯计算 |
| `account_pool/application/environment_state.py` | 配置补偿和冷却状态判断 |
| `account_pool/application/profile_updates.py` | 命名配置与卡片原配置的差异计算 |
| `account_pool/application/plugin_validation.py` | 插件来源和版本校验 |
| `account_pool/channels/cliproxyapi/protocol.py` | 管理协议响应模型 |
| `account_pool/channels/cliproxyapi/provider_requests.py` | 供应商额度请求参数和错误信息提取 |
| `account_pool/channels/cliproxyapi/quota_state.py` | 主动额度、被动额度与缓存合并 |
| `account_pool/providers/usage/codex.py` | Codex 额度和订阅解析 |
| `account_pool/providers/usage/claude.py` | Claude 额度和套餐解析 |
| `account_pool/providers/usage/xai.py` | xAI 账单与额度解析 |
| `account_pool/providers/usage/common.py` | 复用 JSON、数值、时间和额度窗口解析 |
| `account_pool/shared/` | 错误脱敏、结果类型与密钥基础设施 |

`EnvironmentService` 保留事务、锁、授权及配置恢复的编排；`HttpCLIProxyClient` 保留网络请求和供应商调用顺序。解析模块不导入客户端或服务编排，避免循环依赖

`account_pool.provider_quota` 继续作为原有公开导入入口。`AuthorizationStart`、`_AuthFile` 及现有测试使用的服务导入名称保持可用。旧的 `result`、`secrets`、`error_safety` 和 `cliproxy` 兼容入口继续保留，不能仅凭当前内部引用数删除

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
