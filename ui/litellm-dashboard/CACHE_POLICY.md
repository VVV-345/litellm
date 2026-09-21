# 控制台缓存与加载规则

## 当前行为

普通 React Query 查询默认保鲜 15 秒，离开页面后未使用的数据最多保留 5 分钟。保鲜时间不等于轮询间隔，5 分钟也不代表打开页面 5 分钟后强制刷新。模块原有的 staleTime、轮询、权限、enabled 和主动刷新设置优先

同一查询并发请求复用一次结果。切回窗口不自动刷新，网络恢复时按查询是否过期决定重新请求。普通查询默认最多重试一次，公共客户端产生的 401/403 不重试，写操作默认不重试

管理请求默认使用 HTTP `no-store`，数据复用由内存中的 React Query 管理，不依赖浏览器磁盘中的接口响应。覆盖公共客户端、OpenAPI 客户端、networking.tsx，以及仍直接请求接口的管理 Hook、工作流、主题与费用配置。调用方可显式覆盖 RequestCache。Playground、提示词会话与外部代码解释器资源没有改接管理请求封装

成功的非 GET/HEAD/OPTIONS 管理请求会把现有查询标记为过期，不立即让所有页面同时重新请求。原有保存后的定向刷新继续执行，离开后再回来会重新查询。这是保守的全局失效规则，包括使用 POST 的管理查询，后续若需要减少这类失效，应按具体端点验证后区分

退出登录清空查询与旧的用户模型、角色、花费 sessionStorage 项，保留界面偏好。身份、角色、凭据或 Worker 改变时隔离查询客户端；取消的旧查询完成后不能写回新会话。重复选择相同 Worker 不重复清理。登录页保留自身挂载，避免存入 Token 后丢失登录完成回调，进入后台时再切换到对应身份的缓存

模型管理、号池和日志中的次级模块按需加载。侧栏只有鼠标移入或键盘聚焦的入口启用路由预加载，不预加载全部模块。页面跳转和模块加载提供占位反馈。未访问的日志标签页不挂载，请求日志和审计标签切走后保留已访问视图的状态，已删除密钥／团队只在选中时挂载，切回时分页状态重新初始化

号池环境查询只在需要卡片数据的标签页启用。原有 24 张卡片分页以及密钥、团队、日志的分页保留，没有引入虚拟滚动

## 代理响应头

| 响应 | Cache-Control |
| --- | --- |
| UI HTML、路由文本和未带版本的资源 | `no-cache`，允许保存但使用前必须校验 |
| `_next/static` 内文件名带内容版本的资源 | `public, max-age=31536000, immutable` |
| 自定义 SERVER_ROOT_PATH 下会被启动脚本改写的文本资源 | `no-cache`，即使文件名带版本 |
| 已登记的管理／信息／管理员查看路由、号池管理、UI discovery | `no-store` |
| 上述静态资源的 404 等错误 | `no-store` |

支持 `/_next`、`/litellm-asset-prefix/_next`、`/ui/_next` 及服务器子路径。GET、HEAD、304 保留对应规则。中间件不读取或缓冲响应体，不改推理和号池内部转发的流式内容。后端管理路由集合不等于全部代理接口，新增管理端点应检查是否纳入集合

缓存头不能解决部署目录仍保留旧文件的问题。发布时仍需保证 HTML 和代码来自同一构建，并保留旧版本分块供已经打开的页面使用；不应在发布过程中清空在线静态目录。本次没有改部署流程或清理运行目录

## 2026-09-21 验证记录

起始版本 `3f4f80ae5e`，开始前快进拉取无更新。期间另一项任务提交了 `72d52b0bdc`，本任务在该版本上提交，不包含或撤销该任务的签名兼容修改。用户已有 `tsconfig.tsbuildinfo` 修改保留，不纳入提交

前端核心回归 12 文件、148 项通过，管理请求与登录调用方 22 文件、265 项通过，真实 AuthProvider 与查询 Provider 联动 1 文件、2 项通过，合计 35 个不同文件、415 项。网络边界为测试替身，不代表线上数据库或供应商已经验证

运行命令在 `ui/litellm-dashboard`：

```powershell
npx vitest run src/lib/queryClient.test.ts src/lib/http/dashboardFetch.test.ts src/contexts/ReactQueryProvider.test.tsx src/lib/http/client.test.ts src/lib/http/api.sameOrigin.test.ts src/utils/cookieUtils.test.ts src/components/networking.test.ts models-and-endpoints/page.test.tsx account-pool/page.integration.test.tsx src/components/view_logs/index.integration.test.tsx src/components/leftnav.test.tsx 'src/app/(dashboard)/layout.test.tsx' --maxWorkers 1
npx vitest run useTeams.test.ts useKeys.test.ts useAccessGroups.test.ts useStoreModelInDB.test.ts useCloudZeroSettings.test.ts useCloudZeroExport.test.ts useCloudZeroDryRun.test.ts useCloudZeroCreate.test.ts useRouterFields.test.ts useProxyConfig.test.ts useUpdateProject.test.ts useProjects.test.ts useProjectDetails.test.ts useDeleteProject.test.ts useCreateProject.test.ts use_margin_config.test.ts use_discount_config.test.ts WorkflowRuns.test.tsx UIThemeSettings.test.tsx cloudzero_export_modal.integration.test.tsx LoginPage.test.tsx LoginPage.integration.test.tsx --maxWorkers 2
npx vitest run src/contexts/AuthContext.integration.test.tsx --maxWorkers 1
```

号池及日志首次动态导入曾在并行构建时超过旧的 1 秒测试等待，异步断言等待真实界面或请求，首次加载上限改为 5 秒；最终核心回归串行通过。没有修改生产重试时间来绕过测试

生产源码类型检查通过：临时配置继承项目 tsconfig，关闭 incremental，仅纳入 next-env.d.ts 和 src 下源码，排除 `*.test.*`、`*.test-d.*`。全量 `npx tsc --noEmit --incremental false` 仍有 1401 条诊断，不能声称全库类型通过。`npm run test:types` 的 4 项类型用例通过，另一个收集文件含 0 项用例

修改文件 ESLint 无错误，保留既有复杂度、any 等警告，未修改 lint 基线。Python 缓存中间件的 basedpyright 为 0 错误、0 警告；新中间件及测试的 Ruff E/F/I 检查通过

仓库根目录运行以下后台回归，26 项通过：

```powershell
$env:LITELLM_LOCAL_MODEL_COST_MAP='True'
.venv/Scripts/python.exe -m pytest tests/test_litellm/proxy/middleware/test_dashboard_cache_middleware.py tests/test_litellm/proxy/middleware/test_security_headers_middleware.py -q
.venv/Scripts/python.exe -m basedpyright litellm/proxy/middleware/dashboard_cache_middleware.py
```

生产构建在隔离源码副本执行 `npm run build -- --webpack`，最终成功生成 52 个页面，不覆盖工作区 `.next`、`out` 或类型缓存。构建副本与本次生产源码逐文件比较一致；测试文件仅作验证，不影响产物

本地代理仅监听 `127.0.0.1:4010`，使用隔离构建的 out，通过 `uvicorn ... --lifespan off` 验证静态服务和中间件，未连接数据库，也没有运行生产启动流程。实际 HTTP 检查：

```powershell
curl.exe -sS -D - -o NUL http://127.0.0.1:4010/ui/login/
# 200, cache-control: no-cache
curl.exe -sS -D - -o NUL http://127.0.0.1:4010/litellm/.well-known/litellm-ui-config
# 200, cache-control: no-store
curl.exe -sS -D - -o NUL http://127.0.0.1:4010/key/list
# 500, cache-control: no-store, 本地未连接数据库
```

浏览器实际打开并刷新生产登录页，输入框与登录按钮正常，无控制台 error；网络记录确认 HTML 为 no-cache、版本资源为 immutable、discovery 为 no-store。刷新时版本资源的 fromDiskCache 为 false，因此这里只证明响应头正确，不把它写成浏览器磁盘命中率提升

## 后续性能验收

本机 Docker 未启动且没有可用 PostgreSQL，本次没有登录后的全模块浏览器测速、生产部署、真实供应商调用或负载测试。不能据此保证刚打开网站后点击任意模块都不卡顿

在有数据库的测试环境，以相同数据量、浏览器和网络条件，对密钥、模型、号池、团队、日志分别记录首次进入和返回进入的请求数、下载字节、点击到可操作时间，以及主线程超过 50 毫秒的长任务。至少重复 5 次，分别报告中位数和最慢一次，不将开发模式编译时间当作用户访问时间

同时验证保存后返回显示新数据、退出再登录不出现旧账号内容、Worker 切换不混用数据、弱网下有加载反馈。重点检查请求日志切换后的筛选状态、号池策略和运行配置保存、已删除列表首次打开才请求。未完成这些检查前，验收结论是缓存与加载机制已改善，尚未获得全模块流畅度保证
