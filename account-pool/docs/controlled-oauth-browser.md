# 受控 OAuth 浏览器设计

## 2026-10-09 发布收尾范围

用户当前没有备用账号，真实供应商 OAuth 与真实账号压力测试延期。本次发布收尾包含已有 Key、切号、代理绑定修复，严格受控数据库升级和独立发布器 EXEC 通道；不扩大 Manager 权限，不启用未经实测的浏览器入口。发布器在停业务前先核对数据库身份和 EXEC 访问，双库快照及临时恢复仍是上线硬门槛。真实 OAuth 延期不代表其已验收，也不保证多 Manager 进程安全

状态：`IMPLEMENTED, GHCR AND SERVER ACCEPTANCE PENDING`。会话、worker、Manager、LiteLLM 和 Dashboard 接入已实现并通过下述源码验证。镜像只通过 GitHub Actions 构建，不在本地 build 或 pull；真实容器隔离和供应商 OAuth 尚未验收，不能据此宣称可上线。

## 方案选择

方案 A（推荐）：每个授权会话创建短生命周期 Chromium worker，worker 使用 Playwright 官方库驱动 headed Chromium，Xvfb 提供显示，noVNC/websockify 只暴露经 LiteLLM 鉴权后的临时浏览器画面。worker 通过独立 egress relay 出站，默认拒绝直连。优点是会话隔离和浏览器自动化边界清楚；代价是需要维护已验证的浏览器镜像和资源配额。

方案 B：供应商提供的远程浏览器服务。优点是减少镜像维护；缺点是供应商可能看到 OAuth 页面和验证码，代理强制、数据驻留和审计依赖外部合同，因此不是默认方案。

方案 C：在 Manager 容器内运行浏览器。不采用。它扩大控制面权限，破坏 Manager、Docker 和用户浏览器会话的隔离。

## 会话流程

1. 管理员在号池卡片点击“开始授权”。LiteLLM 管理路由校验现有管理员权限，向 Manager 请求会话。
2. Manager 为环境生成一次性 session id、过期时间和随机 websocket ticket，并锁定当前 `proxy_profile_id` 及解析后的代理出口版本。锁定期间禁止切换代理、删除环境或启动第二个浏览器会话。
3. Worker 只接收短期 session credential、目标授权 URL 和严格允许的回调 origin，不接收 LiteLLM bearer、Docker socket、代理凭据或供应商 token。Worker 的数据卷是会话专属临时卷，过期、回调成功、取消或失败均销毁。
4. 用户在 noVNC 画面中人工完成登录、验证码和 MFA。系统不得读取、绕过或自动提交验证码，也不得记录画面、键盘、Cookie 或页面正文。
5. Worker 仅监听与供应商契约匹配的回调 URI（例如 Codex 的 `127.0.0.1:1455/auth/callback`），通过受控 callback relay 将 `code`、`state` 或明确错误转发至现有 `submit_oauth_callback`。Manager 继续负责 state 签名、一次性消费、provider state 转换和 CLIProxyAPI token 阶段。
6. 回调成功后立即关闭 websocket、删除 worker 和临时卷，现有验证流程完成后才显示 ready。超时和断连均进入可重试的失败状态并清理资源。

## API 边界

新增接口应保持 Manager 为状态唯一写入者：创建、查看状态、取消、回调 relay 和清理均由 Manager 提供；LiteLLM 仅做代理和权限检查。Dashboard 将一次性 ticket 放在 POST 的 Authorization header 中，换取会话路径限定的 HttpOnly、SameSite=strict Cookie，HTTPS 时附带 Secure。ticket 不进入 URL 或持久缓存。静态资源与 WebSocket 使用此 Cookie；服务端验证来源、状态、到期时间和代理绑定后才转发，连接期间每两秒复验，终态关闭连接。任何错误响应必须脱敏，不返回 session credential、Cookie、代理 URL 中的认证信息或完整配置。

## 隔离与代理强制

worker 网络不得加入 LiteLLM control、Docker socket、数据库或其他账号网络。浏览器网络应为每会话独立网络，默认 `internal: true`，仅连接到受限 egress relay；relay 只允许锁定代理出口和必要 DNS/HTTPS，relay 不接受来自公网的连接。若代理不可达、配置变化、出口身份变化或 relay 不可用，浏览器必须 fail closed，不能回退直连。

CLIProxyAPI 的 token 交换必须继续在该环境的既有代理配置下执行。浏览器代理与 CLIProxyAPI 代理在创建 session 时绑定同一不可变出口版本，回调完成前后都检查出口身份；不一致则拒绝完成并要求重新授权。Docker socket 仅由 Manager 通过现有受限 socket proxy 使用，浏览器服务绝不能挂载 socket。

## 部署与版本决策

部署前必须实测并记录：Playwright、Chromium、Xvfb、noVNC、websockify 和 egress relay 的具体镜像 digest；rootless 用户、只读根文件系统、capabilities、seccomp、资源上限、网络连通性和清理行为。未完成这些实测前不添加默认 compose 服务、不写镜像 tag、不宣称可用。

## 验收矩阵

必须有自动化测试覆盖 ticket 绑定与重放拒绝、管理员权限、过期和取消清理、并发会话互斥、回调 state 原子消费、错误脱敏、代理变更拒绝、relay 不可用时无直连、worker 无 socket/token、容器无发布端口，以及成功回调后 CLIProxyAPI 验证状态转换。真实 Docker 和供应商 OAuth 验收另列为部署检查；当前未运行，不能以 mock 结果替代。

## 当前代码审查结论

OAuth 创建请求现在接收 `proxy_profile_id`，LiteLLM 会透传到 Manager，Manager 在创建容器前检查 profile 并把其 URL 写入初始 CLIProxyAPI 配置，再应用配置后才启动授权。授权等待期间，配置接口拒绝更换代理。Dashboard、请求 schema 与 Manager 测试覆盖选择值透传和未授权前拒绝不可用 profile。

会话持久化、一次性 ticket、回调 HTTP handler、代理出口不一致检查、后台清理和 noVNC 双向 WebSocket 已实现。成功回调先返回响应，再落终态并清理 sidecar，避免响应未送达就删除容器。browser 仅加入会话私网，callback-relay 使用唯一 control-network 别名；启动等待健康检查，失败清理

同一代理 profile 的创建、重新授权及节点切换使用共享锁，OAuth 状态持久化前不得切节点。锁不等待人工登录，不同代理可以并行。此约束覆盖单 Manager 进程及项目 API，不覆盖外部直接改 Clash 或多个 Manager 进程

## 2026-10-08 源码验证

Manager 全套 `python -m pytest account-pool/tests -q`：584 passed、3 skipped。LiteLLM 号池管理测试全套：322 passed、1 skipped；最后修正 root-path Cookie 和类型后，对应 endpoint 文件再次运行：39 passed。Dashboard 号池范围：26 files、144 passed。以上为本地单元和集成测试，不是真实供应商验收

前端 `npm run build -- --webpack` 在同盘临时源码副本通过，exit 0。原 worktree 的跨盘 node_modules junction 引发 webpack 路径错误，因此使用不修改原工作区的同盘副本。全量 tsc 仍有既有源码/测试诊断，不能把生产 build 或 Vitest 的输出称为全量类型检查通过。新增网关类型问题已消除，文件仍有已有 FastAPI route 未使用及一项已有 Any 诊断

## 发布和服务器验收门槛

push 修复分支后手动触发 `publish-deployment-images.yml`，固定完整 commit。等待 LiteLLM、Manager、browser-worker 的 amd64/arm64 镜像与 manifest 完成，记录 digest。服务器先拉取完整 worker digest，再配置 `ACCOUNT_POOL_OAUTH_BROWSER_IMAGE`；Manager 启动会话使用 `--pull never --wait --wait-timeout 45`，避免向 Manager 提供私有 registry 凭据

仅通过用户指定的 JumpServer“号池”资产验收，保留当前业务镜像、Compose、数据库备份及回滚路径。先执行现有 release guard，不能跳过 schema/configuration 检查。反向代理需要支持 WebSocket、至少 120 秒请求读取时限以及受信的 HTTPS/WSS 转发头

真实待验收项：选定节点返回有效 delay_ms；浏览器和 token 阶段同代理；代理不可达时无直连；noVNC、回调与 CLIProxyAPI ready；不同账号并发及同账号互斥；超时、取消和成功清理。登录、验证码与 MFA 由用户人工完成。额度冷却和故障切换已有回归覆盖，但仍需线上观测。移除两候选上限后仍受配置、最多 10 次、90 秒及流输出安全边界限制，不是无限重试

## 2026-10-08 受控数据库升级

旧版 `5b76f5170d` 到受控浏览器版的 Manager schema 新增一张 OAuth 会话表及三个索引。现有发布器因结构指纹不同拒绝替换，这是保护性拦截，不代表已有账号数据损坏。此前服务器备份、双库临时恢复及候选 schema 重复执行检查已通过，生产服务尚未切换。

新增例外仅允许这一对经过审阅的非 `release_*.py` Manager 源码树摘要；文件路径、源码内容、旧持久化契约与四条新增 DDL 均须匹配。LiteLLM 原始 Prisma（仅归一 CRLF）及非空启动/迁移历史证据必须一致。不改原指纹算法，不允许任意“只新增表”的迁移，不通过目标镜像自报的白名单放行。以后非发布模块发生变化时，需要重新审阅，不能直接更新摘要。

该例外还要求 `ACCOUNT_POOL_RELEASE_DATABASE_BACKUPS=true`，由实际发布器暂停业务、刷新双库快照并完成临时恢复验证之后才能替换。旧备份存在不够。备份失败不启动新版；新版健康失败或发布器中断时仅回退镜像，保留新表及当前业务数据，不自动回放旧快照。`restore_data` 的显式数据库恢复语义保持不变；镜像回退不是认证文件、OAuth 外部状态和全部副作用的完整回滚。

本次源码验证：Manager 全套 612 passed、3 skipped，其中含升级白名单拒绝、刷新备份、备份失败、健康失败和中断后的镜像回退。最初使用旧 checkout 的 Python 环境时，pytest-asyncio 插件缺少 hook，收集退出 3；改用已有 MiniConda 环境后通过，未修改依赖或业务代码来掩盖该错误。

服务器替换前仍须完成：

1. 在临时还原库上应用新 schema 后，用旧版实际执行 `initialize` 和账号列表读取，检查既有数据数量和摘要不变。
2. 从实际 release-worker 配置的 Docker endpoint 执行无副作用 `exec ... true`，再验证双库备份路径。不能把宿主机 sudo 备份成功当成发布器具备权限。共享 Socket Proxy 若禁止 EXEC，不得直接给 Manager 共用代理扩权；须另行审阅独立发布通道。
3. 新 commit 在 GHCR 构建成功后，先更新独立 release-worker，按实际镜像核对固定源码、Prisma、启动证据及完整 digest，再通过原 prepare/execute 流程上线。
4. 刷新上线前备份，检查健康和真实浏览器隔离；真实 OAuth 仍由人工完成。以上源码测试不替代这些门槛。
