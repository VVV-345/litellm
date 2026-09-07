# 号池单机部署指南

四个容器一起起：LiteLLM 主服务、号池 Manager、两个独立数据库、Docker Socket Proxy。镜像从 ghcr.io/vvv-345 拉取，不需要在服务器上构建

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

隧道开着时点开授权链接，回调会经服务器 8091 端口进入 Manager。FreeBuff（设备码流程）不需要隧道

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

仅 CLIProxyAPI 渠道（Claude/Codex 等）走这些网关；FreeBuff 渠道不认 HTTP 代理，配置无效

## 日常操作

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
- 非美区服务器：先在代理网关面板选好出口节点再授权（CLIProxyAPI 走页面里的代理配置即可生效；FreeBuff 容器不吃 HTTP_PROXY，需要宿主机透明代理或上游中继，详见 account-pool/README.md）
