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

- Linux 服务器和 Docker Compose v2
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

Manager 使用固定非 root UID 运行，根文件系统为只读，只挂载运行 Docker Compose 所需的 Docker CLI、Compose 插件和账号数据卷。Socket Proxy 使用固定版本并只开放当前 Compose 生命周期和网络接入所需的容器、镜像、网络、卷、信息和 POST 类 API。部署时应在目标 Docker Engine 上执行实际的 `docker compose` 生命周期和 `docker network connect` 验证。此限制并不把 Socket Proxy 变成恶意容器创建请求的完整安全边界：这些 Compose API 仍可能被滥用以取得宿主等价权限。生产部署必须把 Manager API、Manager 容器及其控制网络视为高信任控制面，并限制可调用 Manager 的主体、审计 Docker API 使用，以及使用独立受控宿主机

然后启动号池 Manager。Manager 通过同一 Compose 文件中的 `docker-socket-proxy` 服务连接 Docker Engine，需要挂载 Docker CLI 和 Compose 插件，但不应再挂载 `/var/run/docker.sock`。`ACCOUNT_POOL_MANAGER_CONTAINER` 和 `ACCOUNT_POOL_GATEWAY_CONTAINER` 必须填写宿主机上的两个真实容器名，便于把每个隔离网络接入控制面。生产环境应由反向代理或防火墙确保 Manager API 只对 LiteLLM 主机可见，并保留 Socket Proxy 的 API allowlist

## 当前边界

首版只支持 OpenAI Codex OAuth。CLIProxyAPI 的额度来自最近一次上游响应的被动观测，因此账号完成授权但尚未产生请求时，页面会显示“尚未观测”。额度窗口按响应中的分钟数解析，不假设固定周限或月限。并发配置表示整个账号环境的总并发，所有模型 Deployment 使用同一个环境级限流键
