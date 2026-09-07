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

Manager 使用固定非 root UID 运行，根文件系统为只读，只挂载运行 Docker Compose 所需的 Docker CLI、Compose 插件和账号数据卷。Socket Proxy 使用固定版本并只开放当前 Compose 生命周期和网络接入所需的容器、镜像、网络、信息和 POST 类 API。Socket Proxy 仅加入 `account-pool-socket` 内部网络，LiteLLM 不可达，Manager 同时加入该网络和 `litellm-control`。部署文件在 Manager 启动前运行 `docker-cli-check`，它使用同样的宿主 CLI 和 Compose plugin 挂载执行 `docker compose version`；如果宿主路径、插件或动态库不兼容，Manager 不会启动。该检查不证明真实生命周期 API allowlist 可用，目标 Docker Engine 上仍须执行实际的 `docker compose` 生命周期和 `docker network connect` 验证。此限制并不把 Socket Proxy 变成恶意容器创建请求的完整安全边界：这些 Compose API 仍可能被滥用以取得宿主等价权限。生产部署必须把 Manager API、Manager 容器及其 Socket Proxy 网络视为高信任控制面，并限制可调用 Manager 的主体、审计 Docker API 使用，以及使用独立受控宿主机

然后启动号池 Manager。Manager 通过同一 Compose 文件中的 `docker-socket-proxy` 服务连接 Docker Engine，需要挂载 Docker CLI 和 Compose 插件，但不应再挂载 `/var/run/docker.sock`。`ACCOUNT_POOL_MANAGER_CONTAINER` 和 `ACCOUNT_POOL_GATEWAY_CONTAINER` 必须填写宿主机上的两个真实容器名，便于把每个隔离网络接入控制面。生产环境应由反向代理或防火墙确保 Manager API 只对 LiteLLM 主机可见，并保留 Socket Proxy 的 API allowlist

## 渠道与供应商

号池按两层组织上游账号。渠道是承载账号的反代程序，供应商是渠道内提供模型的订阅来源

- CLIProxyAPI（正式实现）：镜像固定为 `eceasy/cli-proxy-api:v7.2.146`。支持五个供应商，均通过 CLIProxyAPI 的 OpenAI-compatible 数据面对外提供模型：
  - OpenAI Codex：浏览器 OAuth，回调端口 1455，路径 `/auth/callback`
  - Anthropic Claude：浏览器 OAuth，回调端口 54545，路径 `/callback`
  - Google Antigravity：浏览器 OAuth，回调端口 51121，路径 `/oauth-callback`
  - Kimi：设备码授权，返回用户码，无 SSH 隧道
  - xAI：设备码授权，返回用户码，无 SSH 隧道
- FreeBuff2API（正式实现）：使用 `freebuff2api-image/` 构建的代理适配镜像，基础版本固定为 `pingmike/freebuff2api@sha256:52e511ed...`。启动适配器为 Node 的 `fetch` 设置代理，原有 FreeBuff 业务代码保持在基础镜像中。唯一供应商 FreeBuff（Codebuff）：打开 codebuff.com 登录链接完成 Google/GitHub 授权后，Manager 轮询拿到 authToken 写入数据卷凭据文件并重启容器。数据面为 OpenAI 兼容 `/v1`（容器别名 `freebuff-<UUID>`，端口 8787）。授权请求和模型请求都支持账号选中的公共代理，免费模型需要符合上游要求的美国出口

所有生命周期操作（创建、授权、读取、配置、删除）都按环境记录中持久化的渠道与供应商分派。旧数据缺省为 CLIProxyAPI + OpenAI Codex，无需迁移。环境级并发由 LiteLLM 的 `max_parallel_requests` 承担，CLIProxyAPI v7.2.146 没有并发管理端点。额度仍来自上游响应的被动观测：Codex 解析结构化窗口，其他供应商暂只记录观测时间，不伪造百分比或窗口。Docker 项目、网络、别名和数据卷的名称继续只由环境 UUID 派生，升级不重建既有资源

## 公共代理出口

CLIProxyAPI 和 FreeBuff 共用 `ProxyGatewayService` 登记的代理名单。比如配置 7891 到 7910 共 20 个端口后，两个渠道的账号都可以选择同一个 7891。代理设置里更换 7891 的 Clash 节点，该端口上的所有账号随之使用同一出口；已有连接可能继续使用原节点，新连接使用更新后的节点

FreeBuff 的账号配置通过 `FREEBUFF_PROXY_URL` 写入 Compose，容器启动时加载 `proxy-bootstrap.mjs`。切换账号使用的端口会更新该账号容器，登录凭据和数据卷保留；取消指定代理则恢复默认出站。只改名称、模型或并发时，Compose 不会因这些字段重建容器。代理失效时请求报错，不自动回退直连

Manager 发起的授权请求从最新账号记录读取同一代理地址；等待授权期间换端口后，下一次轮询使用新端口。浏览器打开登录页面仍使用浏览器自身的网络

本地启用 FreeBuff 前，在仓库根目录构建适配镜像：

```bash
docker build -t litellm-freebuff2api-proxy:1.0.0 account-pool/freebuff2api-image
```

生产部署使用 `ACCOUNT_POOL_FREEBUFF2API_IMAGE` 指向已发布的适配镜像，推荐锁定 digest。不能直接填原始 `pingmike/freebuff2api`，因为它不含代理启动模块。发布方式见 `../deploy/DEPLOY.md`

Clash 在 Docker 宿主机运行时，`ACCOUNT_POOL_PROXY_GATEWAY_HOST=host.docker.internal`；Manager 和账号容器均设置宿主机地址映射。Clash 的监听地址必须允许 Docker 网络访问，代理端口和控制器端口只向受信任的网络开放。Clash 在其他主机上时，填所有账号容器和 Manager 都可访问的主机名或 IP

## 当前边界

CLIProxyAPI 的额度来自最近一次上游响应的被动观测，因此账号完成授权但尚未产生请求时，页面会显示“尚未观测”。额度窗口按响应中的分钟数解析，不假设固定周限或月限。并发配置表示整个账号环境的总并发，所有模型 Deployment 使用同一个环境级限流键
