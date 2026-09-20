# 号池代码维护

本次整理覆盖独立号池服务、账号池前端，以及 LiteLLM 密钥、日志、路由和运行设置页面对这些模块的引用。请求数据面继续由 LiteLLM 网关负责，控制面继续由 Manager 负责

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

删除了静态引用扫描确认无调用方的 `AccountPoolScopePanel`、10 个前端 API 包装，并移除未使用的类型或缩小类型导出范围。服务端对应接口保留；现有页面使用的卡片插件、凭据删除、运行配置保存和镜像回退接口均保留

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

## 验证与边界

本次没有修改数据库结构、HTTP 路径、请求和响应字段、Docker 配置、计费规则、网关路由、重试顺序或签名处理。没有对生产服务器执行部署或数据操作

后端完整回归在仓库根目录运行，确保同时能导入 Manager 和 LiteLLM。只有号池独立虚拟环境时，跨服务模型契约测试会缺少 LiteLLM 导入路径

```powershell
.venv/Scripts/python.exe -m pytest account-pool/tests -q
```

前端相关测试在 `ui/litellm-dashboard` 下运行。Vitest 路径过滤使用不带路由组括号的片段，确保首页测试被实际选中

```powershell
npx vitest run --project unit --project component --project integration src/features/account-pool src/components/Settings/RuntimeSettings account-pool/page.integration.test.tsx --maxWorkers 2
npm run test:types
npx eslint src/features/account-pool 'src/app/(dashboard)/account-pool' src/components/Settings/RuntimeSettings
```

全库 TypeScript 检查包含大量现有测试类型错误；生产源码检查与 Vitest 类型项目需分别报告，不能用后者通过代替整个仓库类型检查通过。相邻路由设置的旧测试有中文文案及会话亲和性默认值断言不一致，其他页面也有既有文案断言失败，本次未改变这些运行行为

2026-09-20 验证：Manager 回归 498 项通过，账号池及运行设置回归 130 项通过，生产源码 TypeScript 检查通过，类型测试 4 项通过。受影响的密钥创建 74 项、日志 21 项测试通过；ESLint 无错误。Knip 全库扫描中，账号池范围没有未使用文件或多余导出；全库其他告警没有批量删除。后端 wheel 构建成功，并对迁移的 208 个定义做了 AST 等价检查

本次清理不表示整个上游 LiteLLM 已无冗余。环境生命周期、供应商网络调度以及独立的版本、批量和上号服务仍有继续细分的空间，应以明确职责和回归覆盖为依据，避免为了缩短文件而改变事务、并发锁或故障恢复边界
