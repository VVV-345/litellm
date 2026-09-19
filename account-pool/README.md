# LiteLLM 号池管理服务

本目录提供 LiteLLM 号池的独立控制面。每个账号使用不可变 UUID 创建一个 Docker Compose 项目和独立网络，运行固定版本的 CLIProxyAPI。CLIProxyAPI 不发布宿主机端口，只能由号池管理服务和 LiteLLM 网关经该环境网络访问

## 架构

```text
Dashboard -> LiteLLM 管理 API -> Account Pool Manager -> Docker Compose
                                      |                    |
                                      |                    +-- 每账号一个 CLIProxyAPI
                                      +-- PostgreSQL            独立网络、无宿主端口
```

LiteLLM 负责管理员鉴权和页面 API。Manager 通过受限 Docker Socket Proxy 执行 Compose 所需的容器、网络和卷操作，不直接挂载宿主机原始 Socket。每个 CLIProxyAPI 只声明自己的账号网络，Manager 与 LiteLLM 网关按环境接入该网络，账号容器之间无法通过显示名直接寻址。账号网络不设为 `internal`，以保留 CLIProxyAPI 访问 OpenAI 等预期上游的默认出站能力。该网络不发布宿主机业务端口，只有预期的控制面容器会被接入其中。推理流量由 LiteLLM 网关通过环境内部地址访问，不经过 Manager

## 运行要求

- Linux 服务器、Docker Engine 25+ 和 Docker Compose 2.20.2+
- PostgreSQL 14+
- Python 3.11+
- 一个仅供 LiteLLM 与 Manager 使用的内部共享 Docker 网络

复制 `.env.example` 中的变量到部署环境。`ACCOUNT_POOL_MANAGER_TOKEN` 至少 32 个字符，LiteLLM 与 Manager 必须配置相同值。`ACCOUNT_POOL_CALLBACK_PORT` 需要在服务器 SSH 可访问的回环地址监听，Manager 对外发布时只绑定回环地址。用户创建环境后会得到如下隧道命令：

```bash
ssh -N -L 1455:127.0.0.1:8091 user@example.com
```

浏览器完成 OpenAI 登录后会访问本机 `http://localhost:1455/auth/callback`，SSH 将请求送到 Manager。Manager 根据 OAuth `state` 把回调转交给正确的隔离环境

## 启动

```bash
uv sync --extra test
uv run uvicorn account_pool.app:create_app --factory --host 127.0.0.1 --port 8091
```

首次启动会创建数据库表。使用仓库根目录 Compose 时，先启动 LiteLLM，Compose 会创建内部共享控制网络并将网关加入该网络：

```bash
docker compose up -d litellm
```

如果 LiteLLM 使用其他部署文件，则先创建同名内部网络：`docker network create --driver bridge --internal litellm-control`

Manager 使用固定非 root UID 运行，根文件系统为只读，只挂载运行 Docker Compose 所需的 Docker CLI、Compose 插件和账号数据卷。依赖按 `uv.lock` 冻结安装，构建基础镜像使用固定摘要。Socket Proxy 使用固定版本并只开放当前 Compose 生命周期和网络接入所需的容器、镜像、网络、信息和 POST 类 API。Socket Proxy 仅加入 `account-pool-socket` 内部网络，号池数据库仅加入 `account-pool-manager-db`，Manager 通过独立的 `account-pool-public` 网络访问外部服务，并通过 `litellm-control` 与 LiteLLM 通信。部署文件在 Manager 启动前运行 `docker-cli-check`，它使用同样的宿主 CLI 和 Compose plugin 挂载执行 `docker compose version`；如果宿主路径、插件或动态库不兼容，Manager 不会启动。该检查不证明真实生命周期 API allowlist 可用，目标 Docker Engine 上仍须执行实际的 `docker compose` 生命周期和 `docker network connect` 验证。此限制并不把 Socket Proxy 变成恶意容器创建请求的完整安全边界：这些 Compose API 仍可能被滥用以取得宿主等价权限。生产部署必须把 Manager API、Manager 容器及其 Socket Proxy 网络视为高信任控制面，并限制可调用 Manager 的主体、审计 Docker API 使用，以及使用独立受控宿主机

然后启动号池 Manager。Manager 通过同一 Compose 文件中的 `docker-socket-proxy` 服务连接 Docker Engine，需要挂载 Docker CLI 和 Compose 插件，但不应再挂载 `/var/run/docker.sock`。`ACCOUNT_POOL_MANAGER_CONTAINER` 和 `ACCOUNT_POOL_GATEWAY_CONTAINER` 必须填写宿主机上的两个真实容器名，便于把每个隔离网络接入控制面。生产环境应由反向代理或防火墙确保 Manager API 只对 LiteLLM 主机可见，并保留 Socket Proxy 的 API allowlist

## 渠道与供应商

号池按两层组织上游账号。渠道是承载账号的反代程序，供应商是渠道内提供模型的订阅来源

- CLIProxyAPI（正式实现）：默认使用 `ghcr.io/vvv-345/cliproxyapi` 的提交 SHA 镜像，并同时固定多架构 digest。支持 OAuth、设备码、API Key 和 Vertex 服务账号供应商，均通过 CLIProxyAPI 的 OpenAI-compatible 数据面对外提供模型：
  - OpenAI Codex：浏览器 OAuth，回调端口 1455，路径 `/auth/callback`
  - Anthropic Claude：浏览器 OAuth，回调端口 54545，路径 `/callback`
  - Google Antigravity：浏览器 OAuth，回调端口 51121，路径 `/oauth-callback`
  - Kimi：设备码授权，返回用户码，无 SSH 隧道
  - xAI：设备码授权，返回用户码，无 SSH 隧道
所有生命周期操作（创建、授权、读取、配置、删除）都按环境记录中持久化的渠道与供应商分派。旧数据缺省为 CLIProxyAPI + OpenAI Codex，无需迁移。环境级并发由 LiteLLM 的 `max_parallel_requests` 承担，当前 CLIProxyAPI 镜像没有并发管理端点。额度刷新会通过 CLIProxyAPI 的受控管理调用读取供应商真实接口：Codex 包含 5 小时、周、Code Review、附加窗口和重置次数，Claude 包含 5 小时、7 天、模型窗口及 Extra Usage，xAI 包含周、月、产品、任务、按量和余额，Antigravity 包含模型、额度桶、积分及套餐。Kimi、Gemini API Key 和 Vertex 没有可用的真实个人订阅额度接口时会明确标记为不支持，不使用固定套餐估算。Docker 项目、网络、别名和数据卷的名称继续只由环境 UUID 派生，升级不重建既有资源

授权结果接收后，账号先进入“验证中”。容器启动或模型读取暂时失败时，后台默认每 5 秒重试，关闭页面或重启 Manager 也会继续。启动等待期限为授权结果接收后的 2 分钟，持续失败时显示超时原因；成功后才进入可用状态并交给 LiteLLM 同步模型。重试验证不会重复领取授权、写入凭据或重启容器

## 公共代理出口

CLIProxyAPI 账号共用 `ProxyGatewayService` 登记的代理名单。比如配置 7891 到 7910 共 20 个端口后，多张卡片可以选择同一个 7891。代理设置里更换 7891 的 Clash 节点，该端口上的所有账号随之使用同一出口；已有连接可能继续使用原节点，新连接使用更新后的节点

Manager 发起的授权请求从最新账号记录读取同一代理地址；等待授权期间换端口后，下一次轮询使用新端口。浏览器打开登录页面仍使用浏览器自身的网络

Clash 在 Docker 宿主机运行时，`ACCOUNT_POOL_PROXY_GATEWAY_HOST=host.docker.internal`；Manager 和账号容器均设置宿主机地址映射。Clash 的监听地址必须允许 Docker 网络访问，代理端口和控制器端口只向受信任的网络开放。Clash 在其他主机上时，填所有账号容器和 Manager 都可访问的主机名或 IP

## 当前边界

额度页支持主动刷新，并保留最近一次被动观测作为上游接口失败时的回退。页面只展示供应商实际返回的窗口、金额、重置时间和订阅字段，不假设固定周限或月限；部分端点失败会保留已成功获取的数据并写入结构化日志。并发配置表示整个账号环境的总并发，所有模型 Deployment 使用同一个环境级限流键

仪表盘也提供刷新额度入口，并显示上次完成时间和下次计划时间。认证文件页提供独立的手动刷新和 5、15、30、60 分钟刷新周期，默认 15 分钟；它同步上游认证文件状态，不强制请求实时额度，也不会重新登录或替换已经失效的 refresh token

号池按环境和凭据标识串行发送供应商管理请求，Codex 的账号信息、用量和重置额度接口依次调用，避免同一凭据的管理请求同时触发 token 轮换。不同凭据仍可并发，请求失败或取消后会释放锁。这是单个 Manager 客户端的调用保护，CLIProxyAPI 自身的后台刷新与模型请求继续由其凭据刷新锁保护

成功请求日志中的缓存率为缓存命中输入 token 除以输入 token 总数。例如输入 100 token、命中缓存 80 token，显示 80.0%。流式和非流式的 Chat Completions、Responses 响应使用同一解析规则。失败、重试、缺少用量、输入为零或缓存命中超过输入总数时，逐请求缓存率显示为未知。既有日志保留，新公式不重写历史请求记录

本次实现与验证记录见 [认证刷新、额度入口与缓存率修复记录](REFRESH_CACHE_RATE_VERIFICATION.md)

NewAPI 使用卡片 Key 接入 LiteLLM 公共模型入口，配置方法和当前协议边界见 [NEWAPI_INTEGRATION.md](NEWAPI_INTEGRATION.md)


## 签名续聊与日志设置（2026-09-20）

号池继续使用 LiteLLM 原生 encrypted_content_affinity 和会话亲和性。切换模型时，仅允许在虚拟密钥授权范围内选择原生判定为同一 `(api_base, api_key)` 的部署；`previous_response_id` 仍严格绑定原部署。其他中转站产生的来源标记在本站无法找到时，只有完整的客户端历史才会移除旧思考状态并重新路由。来源未知的原始签名仍先正常请求，上游明确拒绝签名后，仅在同一卡片、未输出有效内容时恢复一次

恢复复用 LiteLLM 的 Anthropic 思考块清理，保留 Responses、Messages 和 Chat Completions 中配对的客户端工具调用及结果。历史缺失、工具未配对、服务端工具、background、conversation 或 previous_response_id 不执行自动恢复，也不会生成、伪造或解密签名。恢复移除隐藏思考状态，不承诺跨供应商完整继承内部推理。切换到其他网站后的兼容处理由对方网站决定

日志设置统一位于 LiteLLM 的 Logs 设置页，分为日常日志与完整日志。日常请求记录保持完整计费，新增记录数上限；LiteLLM 运行输出支持级别、文本或 JSON、控制台、异步文件、大小轮转、堆栈和第三方库降噪。级别不采样费用记录。文件按进程分开轮转，写入队列最多 10000 条，满时丢弃运行输出以保护请求；跨进程、跨重启文件总容量仍应由宿主存储策略管理

完整日志支持成功正文开关、同会话一致的成功采样、失败正文开关、单方向采集大小、保留天数、正文与摘要容量、附加字段脱敏。签名和凭证字段始终脱敏，成功正文采样不影响失败正文及 LiteLLM 费用记账。容量按压缩正文与摘要计算；后台每分钟检查、每次最多删除 1000 条，因此短时可能超过限制，SQLite 文件实际大小还包含索引和可复用页面。运行输出的自定义模板、按小时轮转和远程目标没有另造一套配置，远程输出继续使用 LiteLLM 现有 Logging 集成

新增日志选项独立保存在现有配置表的 `log_options` 列，与设置版本同事务保存，旧版本仍读取原 `payload`。已经在独立 PostgreSQL schema 中验证新版保存、旧版读取历史及更新、新版再次读取和配置回退；不会恢复或重写业务数据。整个版本能否回退仍取决于其他模块的兼容检查

自动化上号通过后台供应商目录读取认证能力，展示全部现有供应商。已有 OAuth / 设备码适配器继续使用；仅支持 API Key 或服务账号的供应商显示认证方式，并提供提供商设置入口，不把这些供应商标为可自动 OAuth

验收入口为 `/ui/logs?log_view=settings` 和 `/ui/account-pool?tab=onboarding`。定向回归覆盖完整工具历史的普通与流式恢复、拒绝不完整历史、原生模型边界、日志采样脱敏、配置版本控制与异步运行输出。临时测试容器连接真实 CLIProxyAPI 上游，Sol / Terra 普通与流式请求均返回 200，约 1.3～2.3 秒；该批真实请求未触发签名拒绝，不能替代所有客户端跨站续聊的验证
