# 号池单机部署指南

五个基础容器一起起：LiteLLM 主服务、号池 Manager、两个独立数据库、Docker Socket Proxy。账号容器由 Manager 按需创建。镜像从 ghcr.io/vvv-345 拉取，不需要在服务器上构建。LiteLLM、数据库和 Manager 的宿主机端口只绑定回环地址，公网入口必须使用 HTTPS 反向代理

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
   - `LITELLM_MASTER_KEY`、`UI_PASSWORD`、`LITELLM_DB_PASSWORD`、`ACCOUNT_POOL_DB_PASSWORD`：使用 `openssl rand -hex 32` 分别生成
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

5. 配置 Nginx、Caddy 等反向代理，把 HTTPS 域名转发到 `127.0.0.1:4000`，并让 80 端口只做 HTTPS 跳转。浏览器打开 `https://<域名>/ui/`，进 号池 页面：
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

主机防火墙只应向公网开放 SSH、80 和 443。Clash/Mihomo 代理端口及控制器端口只允许 Docker 私网或明确的管理来源访问

## 日常操作

### 发布新版本

向 `CLIProxyAPI分支` 推送提交后，GitHub Actions 会自动构建并发布以下两个镜像到 GHCR。镜像 tag 是该提交号的前 10 位，例如 `56db4cf0a3`

- `ghcr.io/vvv-345/litellm:<DEPLOY_TAG>`
- `ghcr.io/vvv-345/account-pool-manager:<DEPLOY_TAG>`
首次启用前，需要在 GitHub 的两个现有 GHCR 包设置中进入 `Package settings` -> `Manage Actions access`，添加仓库 `VVV-345/litellm` 并授予 `Admin`。两个包分别是 `litellm` 和 `account-pool-manager`。这是一次性设置，授权后 Actions 使用短期 `GITHUB_TOKEN` 发布和清理旧版本，不需要创建或保存个人访问令牌

发布完成后使用 `python3 releasectl.py deploy 新提交前10位` 更新。部署后台会先备份当前运行的配套镜像，备份成功后才替换业务容器；已有完整备份会跳过，切换失败会尝试恢复原版本

首次启用需要先启动独立的 `release-worker`。配置、首次接管、备份删除和代码回退步骤见 [项目版本管理](RELEASES.md)。界面入口在“号池 / 版本管理”，应用只使用本机备份归档

日常更新不要直接执行 `docker compose pull && docker compose up -d`，这会绕过自动备份，并可能覆盖页面选定的运行版本。GitHub 保留最近 20 个镜像版本，本机备份归档独立保留，远程镜像清理不会删除归档

```bash
python3 releasectl.py status         # 运行版本、备份和最近任务
python3 releasectl.py deploy 新提交前10位
python3 releasectl.py apply 备份ID   # 旧页面没有入口时恢复归档

docker compose logs -f account-pool # 查看 Manager 日志
```

## 数据在哪

- `litellm_postgres_data` 卷：LiteLLM 的 key、模型、日志
- `account_pool_db` 卷：号池环境记录
- `account_pool_data` 卷：每个环境的 compose 文件

## 日常日志与完整日志

“号池 / 设置 / 日志”提供全局“记录完整日志”开关，默认关闭，保存后应用于新请求。日常日志始终保存请求状态、模型、输入输出 Token、缓存和成本摘要；开启完整日志后，网关另外保存输入、提示词、回复及工具调用。正文不会发给环境 Manager，也不会写入标准 LiteLLM SpendLogs

“号池 / 日志”可切换日常日志和完整日志。完整日志支持按会话查看、逐轮展开、查看原始上下文和当前筛选的用量合计。客户端传入 `x-litellm-session-id`、`x-session-id`、`session_id` 或兼容会话头时，会话按号池密钥隔离关联；缺少会话标识的请求仍单独保存。这里只提供历史查看，不自动把已保存的对话注入后续请求

完整日志存入 LiteLLM 容器内 `/var/lib/litellm/full-logs/conversations.sqlite3`，由新增的 `account_pool_full_logs` 数据卷持久化，使用 SQLite 索引和 gzip 压缩正文。自定义运行环境可通过 `ACCOUNT_POOL_FULL_LOG_DIR` 指定持久化目录；当前适用于单实例或共享该目录的实例，多个独立数据卷不会自动聚合。升级时需同步 Compose 文件并备份此卷，不能只换镜像而漏掉挂载

两种日志分别配置保留天数，默认各 30 天，后台每小时执行到期清理。各自支持清理 7、14、30、45 天以前或全部记录，互不影响。关闭完整记录不会立即删除旧记录，旧记录继续按期限清理。页面显示存储位置和磁盘占用，清理日常 PostgreSQL 记录后磁盘空间通常供数据库复用，不保证文件立即缩小

网关会移除已知密钥、凭据字段和内联附件数据，流式响应或 WebSocket 每个方向最多保存 16 MiB，超过上限明确标记截断，异常结束保留已收到的内容。管理员可查看正文，原始上下文也仅在主动展开时加载。历史未记录的正文无法补回；进程被强制终止时，尚未结束请求的内存记录可能丢失

普通 HTTP 请求统一经过 LiteLLM 鉴权、权限、防护栏及标准路由，再进入号池准入和转发。LiteLLM 标准 Usage 与 SpendLogs 负责统一记账，号池按实际上游尝试保存日常摘要、模型价格快照与估算成本，不再为这些请求额外生成一份 `account-pool:` 标准费用记录。两处显示的金额不能相加，标准日志通过 `metadata.spend_logs_metadata.account_pool_request_id` 关联号池请求

缓存采用对应协议的 Token 定义，缓存率按单次成功请求的缓存输入 Token / 输入 Token 计算。成本来自用量及模型计价配置，不等同于订阅账号的实际账单。没有价格或用量时不能把 0 当作免费证明。WebSocket 等特殊路径仍保留自身的用量采集及同步处理，需要单独验证，不应将 HTTP 成功验收外推到所有协议

完整日志页的“失败请求不保存完整日志”只控制新失败请求的号池正文，日常摘要和标准错误日志继续保留，已有正文按原保留期限清理

## 注意事项

- `ACCOUNT_POOL_SECRET_SEED` 一旦投入使用不可更换，换了所有已授权凭据作废
- 两个数据库密码同理，换密码只改 `.env` 会导致已有数据连不上
- Manager 只监听 127.0.0.1:8091，外部无法直接访问；LiteLLM 通过内网 `account-pool:8091` 调它
- 非美区服务器：先在代理网关面板选好出口节点，再在账号配置中选端口并授权
