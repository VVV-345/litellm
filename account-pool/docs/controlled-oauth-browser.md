# 受控 OAuth 浏览器设计

状态：`WORKER RUNTIME IMPLEMENTED, DEPLOYMENT UNVERIFIED`。本文是已批准方向的可执行设计和验收边界，不代表浏览器、Docker 部署或真实 OAuth 已完成。Docker daemon 当前未启动，镜像、浏览器版本、网络策略和真实回调必须在部署阶段实测后固定，不能凭文档宣称成功。

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

新增接口应保持 Manager 为状态唯一写入者：创建、查看状态、取消、回调 relay 和清理均由 Manager 提供；LiteLLM 仅做代理和权限检查。浏览器画面使用带 `Authorization: Bearer <短期 ticket>` 的 websocket，ticket 绑定用户、环境、session、过期时间和一次性 nonce，服务端验证来源、状态和权限后才转发。任何错误响应必须脱敏，不返回 session credential、Cookie、代理 URL 中的认证信息或完整配置。

## 隔离与代理强制

worker 网络不得加入 LiteLLM control、Docker socket、数据库或其他账号网络。浏览器网络应为每会话独立网络，默认 `internal: true`，仅连接到受限 egress relay；relay 只允许锁定代理出口和必要 DNS/HTTPS，relay 不接受来自公网的连接。若代理不可达、配置变化、出口身份变化或 relay 不可用，浏览器必须 fail closed，不能回退直连。

CLIProxyAPI 的 token 交换必须继续在该环境的既有代理配置下执行。浏览器代理与 CLIProxyAPI 代理在创建 session 时绑定同一不可变出口版本，回调完成前后都检查出口身份；不一致则拒绝完成并要求重新授权。Docker socket 仅由 Manager 通过现有受限 socket proxy 使用，浏览器服务绝不能挂载 socket。

## 部署与版本决策

部署前必须实测并记录：Playwright、Chromium、Xvfb、noVNC、websockify 和 egress relay 的具体镜像 digest；rootless 用户、只读根文件系统、capabilities、seccomp、资源上限、网络连通性和清理行为。未完成这些实测前不添加默认 compose 服务、不写镜像 tag、不宣称可用。

## 验收矩阵

必须有自动化测试覆盖 ticket 绑定与重放拒绝、管理员权限、过期和取消清理、并发会话互斥、回调 state 原子消费、错误脱敏、代理变更拒绝、relay 不可用时无直连、worker 无 socket/token、容器无发布端口，以及成功回调后 CLIProxyAPI 验证状态转换。真实 Docker 和供应商 OAuth 验收另列为部署检查；当前未运行，不能以 mock 结果替代。

## 当前代码审查结论

OAuth 创建请求现在接收 `proxy_profile_id`，LiteLLM 会透传到 Manager，Manager 在创建容器前检查 profile 并把其 URL 写入初始 CLIProxyAPI 配置，再应用配置后才启动授权。授权等待期间，配置接口拒绝更换代理。Dashboard、请求 schema 与 Manager 测试覆盖选择值透传和未授权前拒绝不可用 profile。

Task 2 已实现 digest-only 的短生命周期 worker Compose、内部浏览器网络、TLS 校验的 egress relay、callback sidecar 和会话清理。websocket ticket、回调 HTTP handler、代理出口不一致检查和真实 Docker 验收仍未实现；Docker daemon 不可用，必须等运行环境可用后按验收矩阵部署验证
