# 号池单机部署指南

五个基础容器一起起：LiteLLM 主服务、号池 Manager、两个独立数据库、Docker Socket Proxy。账号容器由 Manager 按需创建。镜像从 ghcr.io/vvv-345 拉取，不需要在服务器上构建

## 首次部署

1. 把 `deploy/` 目录拷到服务器（比如 `/opt/litellm-pool/`）

2. 填环境变量

   ```bash
   cd /opt/litellm-pool
   cp .env.example .env
   nano .env
   ```

   要填的值：
   - `DEPLOY_TAG`：镜像版本号，用 commit 号，本指南写的是 `47b84b0023`
   - `LITELLM_DB_PASSWORD`、`ACCOUNT_POOL_DB_PASSWORD`：自己起强密码
   - `ACCOUNT_POOL_MANAGER_TOKEN`：`openssl rand -hex 32` 生成
   - `ACCOUNT_POOL_SECRET_SEED`：`openssl rand -hex 32` 生成，**定了以后不能换**
   - `ACCOUNT_POOL_SSH_HOST`：服务器公网 IP 或域名
   - `ACCOUNT_POOL_SSH_USER`：你 SSH 登录用的用户名

3. 登录 ghcr 并启动

   ```bash
   echo "<GitHub PAT>" | docker login ghcr.io -u VVV-345 --password-stdin
   docker compose up -d
   docker compose ps   # 五个服务都应为 healthy/running
   ```

   GitHub PAT 需要 `read:packages` 权限。包目前是私有的；如果不想服务器登录，可以在 GitHub 包设置里改成 public

4. 验证

   ```bash
   curl http://127.0.0.1:8091/health          # Manager 就绪
   curl http://127.0.0.1:4000/health/liveliness   # LiteLLM 就绪
   ```

5. 浏览器打开 `http://<服务器IP>:4000/ui/`，进 号池 页面：
   创建环境 → 卡片上点配置选好出站代理（非美区服务器必做）→ 点授权完成账号绑定

## 浏览器 OAuth 回调怎么到达服务器

CLIProxyAPI 的 Codex/Claude/Antigravity 授权需要浏览器回调到本机端口。页面会显示一条 SSH 隧道命令，在你**本地电脑**执行：

```bash
ssh -N -L 1455:127.0.0.1:8091 <SSH_USER>@<SSH_HOST>
```

隧道开着时点开授权链接，回调会经服务器 8091 端口进入 Manager。Kimi 和 xAI 使用设备码，不需要隧道

## 代理网关（Clash，可选）

配置后号池页面会出现"代理网关"面板：每个 Clash 端口是一个网关，可以在页面上给每个网关切换出口节点；多个账号可以共用同一个网关。账号卡片配置里选"指定代理"时会自动列出这些网关

1. 服务器上安装 Clash，导入订阅，开启 `external-controller`（默认 9090 端口）
2. 在 Clash 配置里为每个端口预定义一个选择器，名称必须是 `clash-gateway-<端口>`：

   ```yaml
   external-controller: 0.0.0.0:9090
   secret: "你的管理密钥"
   proxies:
     - {name: 美国01, type: ss, server: ..., port: ..., cipher: ..., password: ...}
     - {name: 日本02, type: ss, server: ..., port: ..., cipher: ..., password: ...}
   proxy-groups:
     - {name: clash-gateway-7891, type: select, proxies: [美国01, 日本02]}
     - {name: clash-gateway-7892, type: select, proxies: [美国01, 日本02]}
   listeners:
     - {name: gateway-7891, type: mixed, port: 7891, proxy: clash-gateway-7891}
     - {name: gateway-7892, type: mixed, port: 7892, proxy: clash-gateway-7892}
   ```

3. 在 `.env` 里填：

   ```
   ACCOUNT_POOL_CLASH_CONTROLLER_URL=http://<服务器IP>:9090
   ACCOUNT_POOL_CLASH_SECRET=你的管理密钥
   ACCOUNT_POOL_CLASH_GATEWAY_PORTS=7891,7892
   ```

4. `docker compose up -d` 重启后，网关条目会自动登记进代理名单，页面上即可管理

CLIProxyAPI 账号共用这些网关。比如设置 7891 到 7910 共 20 个端口后，账号 A、B 都可以选择 7891，也可以各选不同端口。修改 7891 的节点会影响所有使用该端口的账号的新连接；已有连接可能继续使用原节点

Clash 在宿主机上时设置 `ACCOUNT_POOL_PROXY_GATEWAY_HOST=host.docker.internal`，监听地址须允许 Docker 网络访问，代理端口和控制器端口只向受信任的网络开放。账号卡片显示所选端口与当前节点

## 日常操作

### 发布新版本

向 `CLIProxyAPI分支` 推送提交后，GitHub Actions 会自动构建并发布以下两个镜像到 GHCR。镜像 tag 是该提交号的前 10 位，例如 `56db4cf0a3`

- `ghcr.io/vvv-345/litellm:<DEPLOY_TAG>`
- `ghcr.io/vvv-345/account-pool-manager:<DEPLOY_TAG>`
首次启用前，需要在 GitHub 的两个现有 GHCR 包设置中进入 `Package settings` -> `Manage Actions access`，添加仓库 `VVV-345/litellm` 并授予 `Admin`。两个包分别是 `litellm` 和 `account-pool-manager`。这是一次性设置，授权后 Actions 使用短期 `GITHUB_TOKEN` 发布和清理旧版本，不需要创建或保存个人访问令牌

发布完成后，进入服务器部署目录，修改 `.env` 中的 `DEPLOY_TAG` 为 Actions 页面显示的版本号，再执行：

```bash
docker compose pull && docker compose up -d
```

这不会删除数据库或账号数据卷。需要回滚时，把 `DEPLOY_TAG` 改回之前已发布的提交号并重复同一命令

GitHub Actions 的构建缓存由 GitHub 按容量和最近使用时间自动淘汰。发布流程对每种 GHCR 镜像只保留最近 20 个版本，旧版本会自动删除，保留的版本可用于回滚。服务器可定期执行 `docker image prune -f` 清理未被任何容器使用的旧镜像，不能执行会删除数据卷的清理命令

```bash
docker compose logs -f account-pool   # 看 Manager 日志
docker compose pull && docker compose up -d   # 升级（先改 .env 里的 DEPLOY_TAG）
docker compose down                   # 停止（数据卷保留）
```

## 数据在哪

- `litellm_postgres_data` 卷：LiteLLM 的 key、模型、日志
- `account_pool_db` 卷：号池环境记录
- `account_pool_data` 卷：每个环境的 compose 文件

## 注意事项

- `ACCOUNT_POOL_SECRET_SEED` 一旦投入使用不可更换，换了所有已授权凭据作废
- 两个数据库密码同理，换密码只改 `.env` 会导致已有数据连不上
- Manager 只监听 127.0.0.1:8091，外部无法直接访问；LiteLLM 通过内网 `account-pool:8091` 调它
- 非美区服务器：先在代理网关面板选好出口节点，再在账号配置中选端口并授权
