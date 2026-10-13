# 统一更新、拉取、替换与回退手册

本文件是本项目后续发布和上游更新的统一操作入口。先完成检查，再按对应更新类型执行；不要凭上一次记忆拼命令。实际机制见 `RELEASES.md`，首次安装见 `DEPLOY.md`，换服务器或数据迁移见 `MIGRATION.md`

**适用范围：** LiteLLM、号池 Manager、独立 release-worker、受控浏览器 worker，以及 CLIProxyAPI / LiteLLM 上游源码更新。它们不是同一个更新操作

**完成标准：** 正确提交的镜像已经发布；凭据和配置有效；备份可验证；兼容性检查通过；实际运行镜像符合目标；健康、鉴权、账号网络正常。镜像构建成功、拉取成功、HTTP 200 均不能单独代表发布完成

## 0. 本机环境卡片与当前断点

下面是 2026-10-12 现场核对值，每次执行仍需重新确认，不适用于另一台机器

| 项目 | 实际值 |
| --- | --- |
| 网站 | `https://massallgood.zlia.ai` |
| 入口 | 已登录的 JumpServer“号池”终端；复用用户已打开的连接，不重复新建 |
| 主机名 | `lima-stbk-pool` |
| 项目主线 | `VVV-345/litellm` 的 `CLIProxyAPI分支`，不是仓库默认分支 |
| 部署目录 | `/opt/zlia-pool/litellm-migration/runtime` |
| Compose 项目名 | `litellm-migration` |
| 发布器配置入口 | `/opt/zlia-pool/litellm-migration/runtime/docker-compose.yml` |
| 版本目录 | `/opt/zlia-pool/litellm-releases-arm64`，与部署目录分离 |
| GHCR 登录配置目录 | `/opt/zlia-pool/litellm-migration/docker-config`，发布器挂载到 `/root/.docker` |
| 业务容器 | `litellm-migration-litellm-1`、`litellm-migration-account-pool-1` |
| 发布器 | `litellm-migration-release-worker-1` |
| 两库容器 | `litellm-migration-db-1`、`litellm-migration-account-pool-db-1` |
| 回退配置 | 发布器维护 `active-compose.json`、`active-pair.json`、`switch-compose.json`，不要手改 |

目录受权限保护，使用已有的 `sudo -n` 权限，不用修改目录权限解决访问失败。该服务器没有把 4000/8091 暴露到宿主机回环地址，健康检查在容器内执行；不能把宿主机 `curl 127.0.0.1:4000` 失败误判为服务故障

**当前断点，非成功发布记录：**

- 运行提交：`74f4897111c725bf582d2e734a4dc815c9cdfb22`
- 当前可用镜像备份 ID：`82da4d495ed976e78500fdb1`
- 候选：`f93ebe79b058d6da93ed548a094dc1235fe135df`，GHCR 构建和两个业务镜像拉取已成功
- 发布任务：`ac61739a22a3b4501f8b77c2c883674b`，`failed`，CLI 退出 1；停在“备份当前运行版本”，没有替换业务容器
- 原因：新增 `TransportPolicy.read_timeout_seconds` 改变持久化 JSON 契约。Prisma 定义相同，但旧版严格拒绝未知字段；不能因“没有改表”就判为可回退
- 后续候选 `f35ab2d9aa` 在 2026-10-13 再次被同一门禁拦截，任务 `f169a7112891de7c629b989b1b4238c5a`；旧业务容器健康，未替换，不能重复提交同一候选
- 失败后旧业务容器仍 healthy，公网 `/health/liveliness` 为 200；数据库备份功能开启，但本次在数据库快照阶段之前停止，不能声称生成了本次新快照
- 用户已同意修复兼容性后继续部署。修复尚未完成，不能反复重试原候选或关闭检查。后续文档提交也不等于兼容性已修复
- 现场非敏感核验文件：`/opt/zlia-pool/litellm-migration/audit-sse-f93ebe79b0/baseline.json`

## 1. 先选更新类型

| 更新类型 | 正确入口 | 不会自动完成的事情 |
| --- | --- | --- |
| 本项目业务版本 | 合并主线、Actions 发布、`releasectl.py deploy TAG` | 不更新发布器、数据库镜像、代理、账号容器 |
| 独立发布器 | 检查无运行任务后，只重建 `release-worker` | 业务发布不会替它升级 |
| CLIProxyAPI 上游 | 页面上游更新的分析、审阅、晋升流程 | 不等于已替换每个账号的运行容器 |
| LiteLLM 上游源码 | 对应独立上游工作流，兼容审阅后纳入本项目发布 | 不能直接用官方 latest 覆盖定制号池代码 |
| OAuth 浏览器 worker | 固定 digest、兼容审阅和实际会话验证 | 不属于两个业务容器的发布；禁用状态不要顺手启用 |
| 数据迁移 | 单独迁移与恢复方案 | 镜像回退不回放数据库，不恢复认证文件和外部日志 |

## 2. 凭据清单：三类 Token，分别检查

| 凭据 | 谁使用、做什么 | 保存和权限要求 |
| --- | --- | --- |
| Actions `GITHUB_TOKEN` | GitHub 执行镜像构建、推送和保留策略 | Actions 自动提供，不复制到服务器；workflow 需要 packages 权限，仓库须具有包的 Actions access |
| GHCR 拉取凭据 | 服务器 / release-worker 拉取私有镜像 | 使用具备目标包读取权限的凭据；classic PAT 常用 `read:packages`，组织 SSO 按实际要求授权；Docker 配置包含秘密，目录 0700、文件 0600，不提交 Git |
| `ACCOUNT_POOL_UPSTREAM_SYNC_GITHUB_TOKEN` | Manager 查询 CLIProxyAPI 同步分支并触发工作流 | 对实际 fork 仓库，至少验证 Contents read、Actions read/write；工作流内部写代码所需权限另由工作流自身处理 |
| `ACCOUNT_POOL_LITELLM_UPSTREAM_SYNC_GITHUB_TOKEN` | Manager 查询 LiteLLM 同步分支并触发工作流 | 单独配置和验证，不能假定自动复用前一个 Token |
| `ACCOUNT_POOL_RELEASE_TOKEN` | LiteLLM、维护 CLI 到发布器的内部认证 | 各消费端保持一致，不用 GitHub Token 代替 |
| `ACCOUNT_POOL_MANAGER_TOKEN` | 网关到 Manager 的内部认证 | 保留原值及配置一致性 |
| `ACCOUNT_POOL_SECRET_SEED`、数据库密码、业务密钥 | 加解密历史凭据、连接原数据库、用户鉴权 | 普通升级不得重新生成或替换 |

### 2.1 “写在 .env”不等于“容器收到”

项目 `config.py` 读取 `ACCOUNT_POOL_` 前缀环境变量。本次修改后的通用 `deploy/docker-compose.yml` 和 `account-pool/docker-compose.manager.yml` 已引用两类上游 GitHub Token 及独立版本/分支配置；现场旧 Compose 不会自动获得这些引用，必须核对。只写 `.env` 而 Compose 没有引用，应用不会获得配置；`restart` 也不会加载修改后的容器环境

确认需要配置时，在**服务器实际 Compose 的 account-pool.environment** 中引用，不写真实值到版本库：

```yaml
ACCOUNT_POOL_UPSTREAM_SYNC_GITHUB_TOKEN: ${ACCOUNT_POOL_UPSTREAM_SYNC_GITHUB_TOKEN:-}
ACCOUNT_POOL_LITELLM_UPSTREAM_SYNC_GITHUB_TOKEN: ${ACCOUNT_POOL_LITELLM_UPSTREAM_SYNC_GITHUB_TOKEN:-}
```

这是配置变更，不是无条件粘贴指令。先备份受保护的实际配置，保留原密钥、卷、网络和服务归属，经审阅后用受控发布更新容器。配置修改后同时核对发布器的部署输入和运行配置，避免下次发布覆盖回已缺失的配置

以下命令只输出是否设置和非秘密的目标参数，不输出 Token：

```bash
sudo -n docker exec -i litellm-migration-account-pool-1 python - <<'PY'
import os
from account_pool.config import Settings
s = Settings()
for name in (
    'ACCOUNT_POOL_UPSTREAM_SYNC_GITHUB_TOKEN',
    'ACCOUNT_POOL_LITELLM_UPSTREAM_SYNC_GITHUB_TOKEN',
    'ACCOUNT_POOL_MANAGER_TOKEN',
):
    print(name, 'SET' if os.environ.get(name, '').strip() else 'MISSING')
for prefix in ('upstream_sync_', 'litellm_upstream_sync_'):
    for suffix in ('fork_repository', 'workflow', 'workflow_ref', 'branch'):
        print(prefix + suffix, getattr(s, prefix + suffix))
PY
```

**权限验证：** 使用应用真实配置的 fork、workflow 和 ref，分别验证 release 查询、同步分支内容读取、workflow 可见性。仅“SET”不证明 Token 有效或有触发权限。只在用户确实要更新上游时触发 analyze，观察 GitHub 返回 204 和对应实际 run；只读 GET 成功不能证明 Actions write。401 查失效，403 查权限/SSO/限流，404 同时查仓库、ref、workflow 和访问权。不要自动扩大权限或把 Token 发到 URL、日志和聊天

### 2.2 GHCR 登录要对准实际消费端

宿主机用户登录成功，不代表 root、release-worker 也有凭据。先核对发布器的 Docker 配置挂载，再由发布器拉取指定候选验证。当前服务器使用上表的 `docker-config` 目录，不是普通用户 `~/.docker`

若拉取报 401/403，由管理员在交互终端对正确的 Docker 配置执行 `docker --config DIRECTORY login ghcr.io -u USER`，在密码提示中输入现有凭据。不要把明文 Token 写在命令参数中，不使用 `cat .env`、`docker inspect` 全量输出或 `set -x` 排查秘密。获取或扩大凭据权限必须单独确认，文档不授权该操作

## 3. 发布前检查，任何一项失败都先停止

### 3.1 代码和镜像身份

本地确认主线、保留未提交改动，拉取后合并已验证修复，再推送。不要把 `litellm_internal_staging` 误当本项目主线，不强推

```powershell
git branch --show-current
git status --short
git pull --ff-only
git rev-parse HEAD
gh run list --repo VVV-345/litellm --commit FULL_COMMIT --limit 5
gh run view RUN_ID --repo VVV-345/litellm --json status,conclusion,headSha,jobs
```

`FULL_COMMIT`、`RUN_ID` 必须替换为本次核验值。核对 `Publish deployment images` 的 headSha 和结论；LiteLLM、Manager、Browser Worker 的 amd64/arm64 以及共享多架构 manifest 均完成后，记录前 10 位 TAG。文档更新、特性分支 push、拉取成功不能冒充发布验收

### 3.2 服务器身份、版本与空间

```bash
hostname
sudo -n docker ps --format '{{.Names}} | {{.Image}} | {{.Status}}'
sudo -n docker inspect -f '{{.Name}} | {{index .Config.Labels "com.docker.compose.project"}} | {{index .Config.Labels "com.docker.compose.project.working_dir"}} | {{index .Config.Labels "com.docker.compose.project.config_files"}}' \
  litellm-migration-litellm-1 litellm-migration-account-pool-1 litellm-migration-release-worker-1
df -h /opt/zlia-pool/litellm-releases-arm64
sudo -n sh -c 'cd /opt/zlia-pool/litellm-migration/runtime && python3 releasectl.py status'
```

确认项目和目录一致；当前两个业务镜像完整 commit 一致；没有 queued/running 的发布任务；备份 available；`problems` 为空；数据库备份已开启；剩余空间足以容纳镜像归档、两库 dump 和临时试还原。不能只用固定“剩余 1 GB”判断足够

记录每个子命令的退出状态。`cmd1; cmd2; echo $?` 只能代表最后一个命令，不能覆盖前面的失败。管道使用 Bash `set -o pipefail` 或明确保留各阶段退出码

### 3.3 在途请求和基线健康

```bash
sudo -n docker exec litellm-migration-account-pool-db-1 \
  psql -U account_pool -d account_pool -Atc \
  'SELECT count(*) FROM account_pool_leases WHERE expires_at > now();'
sudo -n docker exec litellm-migration-litellm-1 python -c \
  'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:4000/health/liveliness", timeout=10).status)'
sudo -n docker exec litellm-migration-account-pool-1 python -c \
  'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8091/health", timeout=10).status)'
curl --fail --silent --show-error --max-time 15 https://massallgood.zlia.ai/health/liveliness
```

以上查询按现场数据库用户名和容器名编写，变更后重新确认。租约为 0 是瞬时样本，不保证下一秒没有流量，也不覆盖所有外层请求。提前通知维护窗口，让活跃流式请求完成；需要严格无损切换时必须有另行设计的排空/多副本能力。不要用永久降低并发掩盖升级问题

同时保存旧镜像 ID/commit、卷和账号 ID/网络、配置摘要、凭据存在性与内部一致性。敏感配置备份只写服务器权限受限目录，不输出正文。新旧比较排除“镜像标签应该改变”这一项，不能忽略密钥或数据位置变化

## 4. 新旧数据兼容：发布器是门槛，不是报错后绕开的障碍

| 差异 | 必须证明的事情 | 禁止的捷径 |
| --- | --- | --- |
| 仅逻辑修复、结构相同 | 原输入下旧/新行为、错误路径、回滚行为 | 只看测试数量或镜像 tag |
| JSON 增加可选字段 | 新版读旧数据；未设置时序列化能否仍被旧版读取；设置后旧版如何处理 | 认为可选字段永远向后兼容，或全局改成 extra=ignore |
| 新表/索引 | 只允许已审阅的新增集合，保留旧结构，实际备份和恢复验证 | 泛化为所有 schema 差异都允许 |
| 删改列、改变枚举/默认值/状态 | 单独迁移、双向兼容与回退方案，必要时停写 | 直接强制同步数据库或跳过迁移 |
| 凭据加密/密钥派生变化 | 旧数据解密与写回兼容、固定 seed 和密钥 | 重新生成 seed 让服务“先起来” |
| 存储目录/卷/网络变化 | 保留原数据身份并验证挂载/账号互通 | 换 Compose 项目后意外创建空卷 |

`read_timeout_seconds` 的本次修复必须包含：旧策略读入；默认/未设置不污染旧版 JSON；显式设置 60 秒后往返保存；未知额外字段仍拒绝；确切来源和结构证据下的单向升级；缺备份、额外结构变化、反向回退继续拦截。未完成这些验证不得更新运行版本

本字段的受控处理已经固化在发布器门禁中：Manager 只有在精确检测到 `TransportPolicy.read_timeout_seconds: int | None`、默认值为 `None`、持久化使用 `exclude_none=True`，且已启用两库数据库备份时，才允许该单向升级。发布器仍保留结构指纹差异，因此显式写入该字段后回退到旧镜像会继续被拦截。其他新增字段不会自动获得例外，必须新增针对性测试、兼容规则和回退说明

批准升级规则不等于旧版自动可回退。显式使用新字段后，旧版可能无法读取当前数据，需要专门兼容处理；不要在一次镜像回滚中悄悄删除用户的新配置。回滚检查失败就停止，数据库快照恢复必须另行确认具体快照及数据损失范围

## 5. 何时先更新 release-worker

发布器独立固定版本，业务镜像包含新规则不代表正在运行的发布器已经会检查。只有本次确实修改发布器时执行这一节

1. 记录 worker 原镜像 ID/digest、环境摘要、部署和备份目录挂载、Docker 登录目录、内部 Socket Proxy、网络、端口及旧配置备份
2. 确认无 queued/running 任务。失败任务先确认没有“需要人工恢复”；不要杀正在执行数据库备份或恢复的 worker
3. 对比新旧发布器源码和 release 数据存储契约，核对已有备份索引/任务能读取。只改变实际配置中的 worker 镜像，不重生成整份 Compose，不轮换令牌
4. 用核验过的新 Manager 镜像更新 worker 固定版本，先拉取，再执行下面的最小范围命令。`ACCOUNT_POOL_RELEASE_IMAGE_TAG` 只有被实际 Compose 引用才有效，现场使用固定 digest 时按其方式修改

```bash
sudo -n sh -c 'cd /opt/zlia-pool/litellm-migration/runtime && docker compose --project-name litellm-migration -f docker-compose.yml pull release-worker'
sudo -n sh -c 'cd /opt/zlia-pool/litellm-migration/runtime && docker compose --project-name litellm-migration -f docker-compose.yml up -d --no-deps --no-build --pull never release-worker'
sudo -n sh -c 'cd /opt/zlia-pool/litellm-migration/runtime && python3 releasectl.py status'
```

前提是已完成 1 至 4，不能未修改/核对配置就粘贴。业务容器、数据库和账号容器此时不应被替换。验证 worker 健康、版本、内部鉴权、原备份列表和当前业务身份；失败按受保护的旧 worker 配置恢复它，不动业务数据，不直接修改备份指纹或状态库

## 6. 统一拉取和替换业务版本

前五节通过后，把 TAG 换成确切候选提交前 10 位：

```bash
sudo -n sh -c 'cd /opt/zlia-pool/litellm-migration/runtime && python3 releasectl.py deploy TAG'
```

核对终端显示的当前 commit、目标和操作，等待倒计时后手动输入 `CONFIRM`。不要用 `yes`、管道确认或后台忽略退出状态

发布器负责：拉取两个业务镜像并校验身份；验证/创建当前配套镜像归档；兼容性检查；暂停业务、两库新快照和试还原；替换 LiteLLM/Manager；容器及应用就绪检查；失败时按恢复点处理。数据库快照期间业务会暂停，停机时长包含导出、试还原和启动，不能承诺固定几秒

连接中断不等于任务停止。复用已打开终端，重新运行 `status` 读取任务，不盲目再提交 deploy。不得使用 `docker compose down -v`、卷清理、直接更改 active-pair 或伪造 schema_fingerprint。若已得到明确的运维确认并接受数据不兼容风险，只能通过 release-worker 的 `ACCOUNT_POOL_RELEASE_SKIP_COMPATIBILITY_CHECKS=true` 配置跳过 deploy/apply 的兼容性阻断；镜像身份、备份、人工确认、健康检查和失败恢复仍必须执行，确认响应与任务记录必须标明已绕过

这条命令不部署 Browser Worker。若之前未启用，保持未启用；若本次明确要升级它，需要对应 GHCR `@sha256:`、会话兼容和独立验证，不把 tag 填进只接受 digest 的配置

## 6.1 网页端替换代理配置

号池代理配置可从 Dashboard 的代理网关面板更新，不需要登录服务器手工覆盖文件。更新入口通过 LiteLLM 管理接口转发到 Manager，仍受代理管理员权限和 Manager 鉴权保护。当前页面支持两种输入方式，二选一：

- 将完整 Clash YAML 粘贴到“配置内容”
- 填写 HTTPS 或 HTTP 订阅 URL

当前页面没有直接上传本地文件的控件。需要替换本地文件时，先读取文件内容再粘贴，或将文件放在受信任的 HTTPS 地址后使用订阅 URL。URL 不允许携带用户名或密码，不允许重定向，也不允许指向 localhost、内网、链路本地、保留或未指定地址。内容上限为 2 MiB，必须是 UTF-8 且解析结果必须为 YAML 对象

Manager 在写入前保存当前文件，并保留已登记的 `gateway-<port>` listener 与 `clash-gateway-<port>` proxy-group，避免更新订阅时丢失现有网关端口。配置使用同目录临时文件和原子替换写回，随后请求 Clash reload。reload 失败时恢复原文件并再次 reload；恢复也失败则保持错误状态，不把更新报告为成功

发布或回滚后必须重新打开 Dashboard 页面，确认当前配置路径、登记端口、节点列表和切换功能仍可读。不要直接在服务器覆盖配置文件，也不要把完整订阅内容、代理凭据或 Token 写入日志、提交或工单。若更新失败，先查看 Manager 的脱敏错误和配置文件 hash，确认原文件仍存在，再按第 8 节处理，不要重复提交同一候选

## 6.2 代理节点与已选端口的延迟诊断

代理面板现在展示两条独立结果：

- **节点延迟**：Clash 控制器对选中节点调用 `/proxies/<node>/delay`，反映节点到探测地址的结果
- **端口链路**：Manager 从容器内经 `http://ACCOUNT_POOL_PROXY_GATEWAY_HOST:<port>` 发起同一探测请求，反映实际 listener、宿主机端口、Docker 到宿主机网络和选中节点的完整链路

两项不能互相替代。节点延迟正常而端口链路失败，优先检查端口 listener、`ACCOUNT_POOL_PROXY_GATEWAY_HOST`、宿主机防火墙、Docker 到宿主机的路由和 Clash mixed port；两项都失败，优先检查 Clash 控制器、节点本身或服务器出网；两项都正常但模型请求仍慢，则继续检查账号容器、上游供应商和请求体处理

每个已选端口单独探测，即使多个端口选择同一节点也不合并端口结果。切换节点发生在测速期间时，结果会标记为无效，不能把旧节点的延迟显示给新节点。探测地址是代码中的固定无副作用 HTTPS 地址，若该地址在服务器出口被屏蔽，节点和端口都可能显示错误；这时应先在服务器和容器内用相同出口核对网络，再调整探测地址，不要仅凭页面延迟修改路由策略

## 6.3 SSE 断流与 response body 解码错误

`stream disconnected before completion: Transport error: network error: error decoding response body` 不是一个足以定位根因的错误。号池网关会把断流按来源写入 `upstream_code`，常见值如下：

| `upstream_code` | 含义 | 处理方向 |
| --- | --- | --- |
| `upstream_read_timeout` | 上游在配置的读取窗口内没有继续发送数据 | 调大卡片传输读取超时，检查上游是否长时间无 token |
| `stream_incomplete` | 上游提前结束且没有终止事件 | 检查 CLIProxyAPI、节点链路和上游服务日志 |
| `upstream_invalid_response` | 响应内容或编码无法解析 | 检查上游压缩、编码和反向代理是否改写 SSE |
| `upstream_connection_error` | 建连、连接复用或传输层失败 | 检查节点端口、服务器出网和连接数 |
| `downstream_disconnected` | 调用方主动关闭连接 | 不重试带副作用或已输出内容的请求 |

网关已启用 SSE keepalive，并发送 `Cache-Control: no-store` 和 `X-Accel-Buffering: no`，避免空闲期间被代理缓冲或超时。已经发送响应头后不能把请求安全地当作未执行重新提交；可重试只限于尚未输出内容且明确属于建连失败的情况。`httpx.DecodingError` 现在会归类为 `upstream_invalid_response` 并写入可脱敏的 SSE 错误事件，不再笼统报告为连接错误

现场排查必须同时看三层：Manager 的结构化事件、LiteLLM/反向代理的 SSE access log，以及 CLIProxyAPI 和 Clash 的同一请求时间段日志。若 Manager 记录 `upstream_invalid_response`，优先查响应编码或压缩；若记录 `upstream_read_timeout`，优先查读取超时和节点空闲；若 Manager 已记录 `downstream_disconnected`，服务端通常没有代码故障。不要只依据浏览器或 SDK 的 `Transport error` 修改重试次数，也不要把一次健康检查 200 当成流式链路正常

## 7. 更新后验收与版本对齐

```bash
sudo -n sh -c 'cd /opt/zlia-pool/litellm-migration/runtime && python3 releasectl.py status'
sudo -n docker ps --format '{{.Names}} | {{.Image}} | {{.Status}}'
sudo -n docker inspect -f '{{.Name}} | {{.Image}} | {{.State.Status}} | {{if .State.Health}}{{.State.Health.Status}}{{end}} | restarts={{.RestartCount}}' \
  litellm-migration-litellm-1 litellm-migration-account-pool-1
```

- job 为 succeeded，current.commit 为目标完整提交，两项实际镜像 ID 对应已核验候选，不仅是字符串 tag 相同
- 重跑第 3.3 节健康检查；发布器应用就绪检查包括数据库连接和经鉴权的 Manager 列表。公网入口单独验证
- 对比配置、加密材料、卷、账号身份和动态账号网络。数据库、代理、账号容器没有因本次业务升级被替换
- 重新检查上游更新两个 Token 的容器存在性和目标参数，验证页面可读与鉴权；不能只检查升级前
- 只看本次发布起点之后的脱敏错误日志；不把历史错误算新版本回归，不打印提示词/Authorization/完整环境变量
- 经批准的小流量真实模型验收，验证 SSE 的 `response.completed` 或明确失败事件、记账与调用延迟。避免重放带工具副作用的请求，不从健康 200 推断上游稳定
- 验收通过后，按实际 Compose 使用方式同步持久化版本选择；使用 DEPLOY_TAG 的更新它，硬编码/digest 的维护相应输入。保留发布器维护的运行状态，不用普通 compose up 再覆盖一遍
- 保留旧归档和新快照，清理是另一个需确认的操作。记录哪些检查未执行，不把“已部署”和“生产故障根治”混为一谈

## 8. 失败与回退分支

| 观察结果 | 动作 |
| --- | --- |
| GitHub/GHCR 网络错误、401/403、构建未完成 | 旧服务不动；处理网络/授权/构建；记录真实失败命令和退出状态 |
| 兼容性拦截 | 保存精确差异，补测试和兼容方案，重新构建；不反复提交相同候选 |
| 备份失败 | 确认旧服务仍健康、空间和权限正常，不切换；不能把镜像缓存当归档 |
| 自动替换失败且自动恢复成功 | 核验 current.commit 和健康确实回到旧版；保留失败记录，自动更新保持启用并等待下一轮，不宣称本轮发布成功 |
| 需要人工恢复 | 核对恢复点，用维护 CLI recover；仍失败则停止扩大操作 |
| 运行正常但业务回归 | 选择已验证备份 apply；先读回退兼容报告，不自动强制 |

```bash
sudo -n sh -c 'cd /opt/zlia-pool/litellm-migration/runtime && python3 releasectl.py recover'
# 仅在已选定 BACKUP_ID 且兼容报告通过后：
sudo -n sh -c 'cd /opt/zlia-pool/litellm-migration/runtime && python3 releasectl.py apply BACKUP_ID'
```

两条是不同分支，不能整段顺序执行。`recover` 只适用于发布器存在可用故障恢复点的情况；`apply` 用备份 ID，不用 commit/tag。数据库恢复走 `restore_data ... --snapshot ...`，必须另行确认快照时刻及之后的数据损失，不写进普通更新自动流程

## 9. CLIProxyAPI / LiteLLM 上游更新专用清单

1. 明确 target；从 Manager 运行配置读取 fork_repository、workflow、workflow_ref、branch，不凭默认 main 或文档旧值猜测
2. 先检查第 2 节对应 Token；实际 workflow 必须存在于配置的 ref。当前主线目录没有 `upstream-sync.yml` / `litellm-upstream-sync.yml`，不等于所有配置 ref 都没有，也不证明配置一定正确，应查询实际 ref
3. Analyze 只启动兼容分析。记录 request_id、run 链接、target_tag、测试与差异报告；失败不能 promote
4. 人工审阅再 promote，确认报告 passed 且对应当前 latest target，确认产物真正合并/发布，不把 API 204 当流程完成
5. LiteLLM 上游纳入本项目后按第 3 至 8 节发布配套镜像；CLIProxyAPI 镜像更新按账号环境的受控入口进行，保留原身份、凭据、代理和网络，必要时分批验收
6. 账号认证失效、额度不足、代理 EOF、供应商过载分别处理。更新镜像不会自动恢复 refresh token，也不应改变并发/重试策略掩盖上游问题

## 10. 每次发布必须填写的记录

```text
日期/时区、执行人：
对象：HOST / 网站 / Compose 项目 / 部署目录
更新类型：业务 / 发布器 / CLIProxyAPI / LiteLLM 上游 / 浏览器 worker
原 commit、镜像 ID：
目标 commit、TAG、GHCR digest、Actions run：
凭据：各消费端 SET/MISSING、只读校验结果、授权范围；不记明文
配置：运行与持久化来源、哈希、允许变化项
兼容性：schema / JSON / 凭据 / 启动 / 反向回退检查
备份：镜像归档 ID、hash、两库快照 ID、恢复验证结果
在途请求、维护窗口、开始/结束时间：
执行：每条命令、任务 ID、退出状态；失败原文经脱敏
最终：实际 commit、健康、鉴权、账号网络、上游/SSE 验收
回退：是否实际执行、恢复到哪个版本；未执行写未执行
未完成项与下一条可执行操作：
```

下次从此记录恢复，不重新猜主机、容器名、Token、版本和路径。执行期间要报告实质进展；碰到新阻断应立即说明原服务状态，不长时间静默


## 上游源代码兼容更新

LiteLLM 和 CLIProxyAPI 的仓库、分支、权限、启用步骤及故障处理见 [上游更新维护说明](../account-pool/docs/upstream-updates.md)。兼容检测通过不等于可上线，本手册的备份、真实服务和数据库验收门槛仍然适用
