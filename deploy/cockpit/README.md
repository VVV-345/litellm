# LiteLLM + Cockpit 隔离发布

此目录只部署测试栈，不调用旧发布器，不包含 Manager。旧生产目录 `/opt/zlia-pool/litellm-migration/runtime`、旧镜像包及生产密钥保持不变。不要把这里的文件覆盖到旧目录

## 镜像与版本

新包固定为 `ghcr.io/vvv-345/litellm-cockpit`。同一个 LiteLLM commit 前 10 位为版本号 `VERSION`，组件标签分别是 `litellm-VERSION` 和 `cockpit-VERSION`。两个组件各有 Linux AMD64、ARM64 镜像，部署使用 Release 清单中的 digest，不依赖可漂移的标签

推送 `codex/cockpit-reuse-plan` 会触发独立工作流 **Publish isolated LiteLLM Cockpit images**，不会部署服务器。也支持手动选择该分支运行。工作流先检查固定 Cockpit commit，再构建四份架构镜像，在两个原生架构 runner 启动隔离栈检查健康、页面和未鉴权请求，最后配对发布 `cockpit-test-VERSION` 预发布 Release。它不会递增生产版本、设置 latest、运行生产清理任务或删除任何 Packages

发布版本是一条独立记录，从 `deploy/cockpit/VERSION` 开始：首个成功构建是 `v1.0`，之后每次成功构建默认递增补丁位（`v1.0.1`、`v1.0.2`），由工作流在发布成功后创建不可变版本标签。构建失败不创建标签，重跑同一提交沿用已分配版本。要在下一次发布显式换系列（如 `v1.1`、`v2.0`），只修改 `deploy/cockpit/VERSION`，不改代码。版本标签只用于发布记录，部署仍使用 10 位提交号

`release_bundle.py` 固定 Cockpit commit 为 `ec9989c009042a3f8770781926f6087000825ee9`。该 commit 必须先存在于 GitHub，禁止因找不到而换成 main。源代码推送、工作流触发均需用户明确授权。新 workflow 首次发布走分支 push 触发，不依赖手动运行按钮；GitHub 只有在默认分支存在 workflow_dispatch 定义时才显示该按钮，不能为此擅自改默认分支或触发旧生产工作流

只有包含 `release.json`、`runtime.tar.gz`、`SHA256SUMS` 的完整测试 Release 才可部署。组件发布中断可能留下未配对标签，但不会生成可部署清单。禁止覆盖已有组件标签或 Release；这种失败需检查残留记录后使用新的源提交重发，不自动删除镜像强行重跑

线上 Packages 需把新包关联到 `VVV-345/litellm`，授予该仓库 Actions 访问权。两个组件的 OCI source 都指向此仓库，各自 revision 指向对应源码提交。旧包不改名、不删历史版本，以免生产拉取和回滚失效。包默认可能是私有的，服务器需要只读 GHCR 凭据；不要为了访问方便自动改为公开

## 首次安装

先确认目标服务器身份、磁盘和内存余量、Docker Compose v2（支持 `--wait` 和多个 `--env-file`），以及回环端口 18400 未被占用。配置给三个服务的内存上限合计 6 GiB、CPU 上限合计 4 核，上限不是资源预留，同机测试仍可能影响生产。旧 Docker socket 管理服务若有 daemon 权限，同机隔离也不是安全沙箱

从已核对的测试 Release 下载上述三个附件，核对来源和版本，在下载目录执行 `sha256sum -c SHA256SUMS`。这个校验防止文件损坏，不代替对 GitHub 发布者的信任。以下安装命令仅用于第一次空目录安装，不能拿新包覆盖已有 `.env`、数据或待恢复事务

```bash
sudo -n install -d -m 700 /opt/zlia-pool/litellm-cockpit-test/runtime
sudo -n tar -xzf runtime.tar.gz -C /opt/zlia-pool/litellm-cockpit-test/runtime
sudo -n sh -c 'cd /opt/zlia-pool/litellm-cockpit-test/runtime && python3 releasectl.py init'
```

`init` 生成独立 DB 密码、LiteLLM master key、网页密码、Cockpit 管理 token、签名 key 和 routing seed，保存到权限 0600 的 `.env`。Cockpit 数据为空，不会自动复制生产账号。保持这些密钥长期稳定，并将 `.env` 与数据备份保存在受控位置，不能提交 Git 或贴入聊天

若新包私有，用有 `read:packages` 的凭据在执行部署的 root Docker 配置下交互登录 `ghcr.io`，可用 `sudo -n docker login ghcr.io -u GITHUB_USER`。不要把 token 写到命令参数、仓库或工作流日志。新栈不借用旧发布器的 Docker 登录目录

## 一条命令部署

将 `VERSION` 替换为**新测试 Release** 的 10 位提交号，不能直接使用旧生产示例中的 `a124c53814`

```bash
sudo -n sh -c 'cd /opt/zlia-pool/litellm-cockpit-test/runtime && python3 releasectl.py deploy VERSION'
```

看到正确测试路径、项目和版本后输入 `CONFIRM`。命令通过固定仓库的 GitHub API 查找目标 Release 和唯一的 `release.json` 附件，经 API 下载并核对 GitHub 返回的 SHA256，再核对本地运行包 hash，拉取两份 digest 并核对 OCI 身份。未发布的草稿、缺失或重复附件、缺失或不符的摘要均拒绝，不回退到普通 Release 网页下载地址，也不读取 Docker 凭据下载清单。更新时先停止测试写入，保存测试 PostgreSQL SQL 快照、Cockpit 全部数据和独立密钥，再启动新镜像，核对运行镜像 ID、Compose health、数据库 readiness 和 `/ui/`。全部通过才记录 current.json 并输出 DEPLOYED

独立资源：Compose 项目 `litellm-cockpit-test`，卷 `litellm-cockpit-test_postgres_data`，网络 `litellm-cockpit-test_database/gateway`，宿主 Cockpit 目录 `runtime/cockpit-data`。没有固定 container_name、生产 external 卷或 Docker socket 挂载。数据库与 Cockpit 不发布宿主端口，只有 LiteLLM 绑定 `127.0.0.1:18400`

更新必须通过保守兼容指纹门禁：包含 Prisma schema、真实 migrations 目录、原生 Cockpit/授权模块、Compose 和 Cockpit commit。不同指纹直接拒绝，不能通过手改清单绕过。指纹相同不证明所有业务逻辑兼容，仍需审阅变更和真实测试。发布器本身更新也需要单独审阅，不自动自更新

Compose 中唯一已审阅的兼容归一化是 `UI_USERNAME: admin` 改为 `UI_USERNAME: ${UI_USERNAME:-admin}`：默认值仍为 admin，不改变数据格式。其余 Compose 字节和数据结构继续进入兼容指纹；运行包 hash 始终校验原始文件，不做归一化

## 已有服务器的一次性部署器升级

旧版部署器仍使用普通 Release 下载地址，单独发布新镜像不会升级它。先核对当前主机、实际 runtime、`current.json` 和 `pending.json`；有未完成事务时先处理恢复，不能覆盖运行文件。若该栈已承接网站流量，后续 deploy 会暂停网站写入，不能按目录中的 test 名称当作无影响操作

从同一个已核验的新 Release 获取 `release.json`、`runtime.tar.gz` 和 `SHA256SUMS`，下载失败时可经 GitHub API 的 release asset ID 下载。先执行 `sha256sum -c SHA256SUMS`，仅在校验通过后解压到独立暂存目录，并复核暂存的 `releasectl.py`、`compose.yaml` 与清单中的 runtime hash 一致。不要把归档直接覆盖到运行目录

保留现有 `.env`、`images.env`、`current.json`、Cockpit 数据及卷。备份运行目录中的 `.env`、`releasectl.py` 和 `compose.yaml` 到权限 0700 的独立目录，文件权限 0600。若此前直接修改过 Compose 的 UI_USERNAME，将实际正在使用的用户名写入现有 `.env` 的 `UI_USERNAME`，密码和其他密钥原样保留，不运行 init。核对除这一项外没有其他未迁移的现场 Compose 差异，再在无部署任务运行时持有 `.release.lock`，安装经核验的两个新运行文件

使用现有 `.env`、`images.env` 对新 Compose 执行 `docker compose config --quiet`，并仅检查解析后的 UI_USERNAME 是否与原登录名一致，不打印完整配置或密码。复核运行包 hash、旧版本的兼容指纹和待部署清单一致，再执行 deploy。不能修改 `current.json` 的兼容字段、略过 runtime 校验或用普通 compose up 绕开备份流程。安装阶段失败且尚未开始 deploy 时恢复本次备份的三个文件；已进入部署事务时使用 recover，不能混用两种回退

```bash
sudo -n sh -c 'cd /opt/zlia-pool/litellm-cockpit-test/runtime && python3 releasectl.py status'
sudo -n sh -c 'cd /opt/zlia-pool/litellm-cockpit-test/runtime && python3 releasectl.py recover'
```

部署异常或中断时保留 pending.json，禁止叠加新部署。`recover` 也要求 CONFIRM：更新失败时校验备份，恢复旧数据与旧镜像；首次启动失败时重试同一候选，不删除数据库卷。恢复会倒退测试数据，停止所有外部测试请求后执行。恢复本身失败可以再次执行，不能删除 pending.json 冒充完成。备份、恢复前的失败数据目录不会自动清理，需人工检查磁盘空间和保留期

## 页面、Key 与模型调用

不必开放 18400 公网端口。通过已授权 SSH/JumpServer 隧道映射本机端口到服务器 `127.0.0.1:18400`，浏览器访问 `http://127.0.0.1:18400/ui/`。本机没有隧道时这个地址不会访问服务器。也可以单独配置测试域名和 HTTPS 反向代理到该端口，不能复用或改写生产域名；JumpServer 的“查看网页”取决于管理员是否配置 Web 应用入口，不会自动生成测试网址

网页使用 `.env` 中的 `UI_USERNAME`（未设置或为空时为 `admin`）和 `UI_PASSWORD`，不是生产密码。只在环境文件中配置用户名，不直接编辑 Compose 模板。先导入批准用于测试的 Cockpit 账号凭据，创建 LiteLLM 原生 Key 并设置账号/模型授权，按原生页面完成预检、签名回执、scope 发布、激活、Cockpit 重启及运行 revision 核对。只保存 staged 不代表模型已可调用。当前声明支持范围仍是原生 Key、单账号、明确模型范围、Responses

在本机隧道已建立后，以测试虚拟 Key 调用，不能使用 Cockpit 管理 token 对外调用

```bash
curl http://127.0.0.1:18400/v1/responses \
  -H "Authorization: Bearer $TEST_VIRTUAL_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"MODEL_FROM_AUTHORIZED_MODEL_LIST","input":"Reply with ok"}'
```

部署健康通过不代表真实模型验收。仍需单独完成授权/撤权、流式与中断、重试与续聊、费用对账、重启持久化、数据库与凭据恢复演练；这些结果通过并另获批准后才考虑替代生产
