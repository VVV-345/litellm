# 项目版本管理

入口在“号池 / 版本管理”，管理 LiteLLM 和号池 Manager 的配套镜像。CLIProxyAPI 上游更新仍在原来的上游同步页面

页面下拉框显示当前版本、备份版本和手写备注。完整 commit 可复制，备注和代码回退说明可编辑，保存时等待 5 秒再确认。应用等待 5 秒，删除等待 10 秒，等待结束后均须手动点击确认；确认 5 分钟后失效

## 首次启用

先将包含本功能的提交推送到 GitHub，等待两个镜像构建成功。本机不需要构建 Docker 镜像。将此目录的 `docker-compose.yml`、`releasectl.py` 同步到服务器现有部署目录，保留现有 `.env` 和业务数据

先确认当前项目名和运行 commit，不能因为新增版本管理而更换 Compose 项目名或数据卷名：

```bash
docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' litellm
docker inspect -f '{{.Config.Image}}' litellm
docker inspect -f '{{.Config.Image}}' litellm-account-pool
```

在服务器 `.env` 中增加以下配置。示例路径应替换为实际部署目录，`COMPOSE_PROJECT_NAME` 填上面查到的项目名，令牌可用 `openssl rand -hex 32` 生成。部署目录和备份目录必须使用不同的绝对路径

```dotenv
COMPOSE_PROJECT_NAME=litellm
ACCOUNT_POOL_DEPLOY_DIRECTORY=/opt/litellm
ACCOUNT_POOL_RELEASE_ROOT=/opt/litellm-releases
ACCOUNT_POOL_RELEASE_IMAGE_TAG=包含本功能的提交前10位
ACCOUNT_POOL_RELEASE_TOKEN=独立的随机令牌
```

`ACCOUNT_POOL_RELEASE_IMAGE_TAG` 固定到包含版本管理功能的 Manager 镜像，日常切换不修改它。部署后台使用同一仓库、同一个 Manager 镜像，单独运行在 `release-worker` 容器中，以便业务容器被替换时任务继续完成。它只通过内部 Socket Proxy 管理 Docker，宿主机 8092 仅监听回环地址。它以 root 读取只读部署目录中的 `.env`，备份目录权限设为 0700，配置文件权限为 0600

先启动部署后台，保留两个旧业务容器：

```bash
docker compose pull release-worker
docker compose up -d --no-deps release-worker
python3 releasectl.py status
```

首次启动自动备份当前配套镜像，再将本机已有且带完整 commit 标签的历史镜像转为实际归档。历史镜像使用导入时的配置，页面明确标注来源。完整备份会跳过；导入失败保留已成功生成的备份，用 `python3 releasectl.py scan` 重试。仅有 Docker 镜像缓存不会冒充备份文件

确认后台正常后，用下一节的 `deploy` 命令安装新版本。新 LiteLLM 容器会获得版本管理接口需要的内部令牌，页面入口随之可用

## 日常更新

### 从两个 Compose 项目迁移

如果 LiteLLM 和 Manager 分属不同项目，不能只改 `.env` 中的项目名。先备份两边的实际 Compose 配置、环境变量、数据库、账号认证卷和当前镜像，再生成统一配置。逐项保留运行中的镜像、密钥、命令、端口及挂载，现有数据卷和网络使用明确的 `name` 与 `external: true` 引用，避免生成空卷。两个数据库的服务名应区分，原连接地址需要的网络别名也须保留

统一配置可以放在源码目录之外，避免后续 Git 更新覆盖服务器配置。停止并重建需要更换项目归属的容器，不执行 `down -v` 或卷清理。迁移期间可能中断请求；失败时用保存的原配置恢复原项目。已有账号容器继续保留，业务容器重建后检查到各账号网络的连接及账号 ID

`release-worker` 除内部控制网络外，还需要连接普通桥接网络，Docker 才能正确发布 `127.0.0.1:8092`，供 `releasectl.py` 使用。私有 GHCR 仓库还需要给后台提供 Docker 登录配置，例如只读挂载服务器的 Docker 配置目录；不能把登录凭据放进 Git

迁移成功后检查两个健康接口、号池接口、`/account_pool/releases` 及 `releasectl.py status`，再确认当前版本已生成完整归档。旧部署配置保留作迁移恢复用途，不直接覆盖统一后的配置。新导入的历史镜像使用统一后的运行配置，并不代表已经逐个完成线上回退验证

如果验收发现镜像内的兼容问题，而当前不发布新镜像，可以临时以只读挂载部署补丁，但必须记录基础镜像 commit、补丁文件校验值及恢复方式。当前备份应刷新为包含补丁的配置；后续部署包含修复的新镜像前，从目标部署配置中移除对应挂载，保留补丁文件供旧备份恢复。不要把带补丁的运行状态当成未经修改的原始镜像

### 发布新版本

GitHub Actions 在 `CLIProxyAPI分支` 和 `codex/fix-*` 分支推送时构建两个配套镜像，也支持手动运行。镜像标签是实际提交的前 10 位，镜像内保存完整 commit 标签

两个镜像都发布成功后，在服务器部署目录执行：

```bash
python3 releasectl.py deploy 新提交前10位
```

命令显示目标，等待 5 秒后要求输入 `CONFIRM`。后台拉取两个新镜像，核对 commit，校验或创建当前版本备份，再只替换 LiteLLM 和 Manager。拉取和备份期间旧容器继续运行；归档失败不替换。切换期间请求可能中断，健康检查失败会尝试从原备份恢复

更新后可以将 `.env` 中的 `DEPLOY_TAG` 同步为当前版本，页面以运行镜像身份为准。**后续更新统一使用 `releasectl.py deploy`，不要用普通 `docker compose up -d` 替换这两个业务服务**，后者绕过自动备份，并可能将刚回退的版本覆盖为 `.env` 指定的版本。系统重启由 Docker 的 restart 策略恢复现有容器，不需要重新执行 Compose

部署后台自身的更新应单独安排，确认没有进行中的任务后再更换它的固定镜像标签。旧版业务镜像无法删除或替换这个后台

## 回退与删除

页面选择备份后点击“应用”，等待 5 秒再确认。后台先核对归档 SHA-256，使用 `docker image load` 导入配套镜像，并按归档中的镜像 ID 启动。即使 Docker 缓存或 GHCR 历史镜像已经被清理，完整归档仍可使用。此路径禁止构建和拉取，没有其他镜像来源作为备用

删除会真正删除选中的 `images.tar.gz`、配置、清单和数据库中的对应备注；正在运行的版本不能删除。不会清理 Docker 镜像缓存、GHCR 包、Git 提交、认证文件或业务数据库。删除后的备份不会在后台重启时自动重新导入；手动“扫描并备份”可能从仍在 Docker 中的历史镜像重新生成它

每次备份保存配套镜像、完整 commit、镜像 ID/仓库摘要、SHA-256、压缩大小、时间和部署配置。归档及管理索引存入 `ACCOUNT_POOL_RELEASE_ROOT`，与日志目录分开。配置可能包含凭据，不能公开下载、同步到 Git 或随普通日志输出

镜像备份不包含数据库、认证文件和聊天记录。自动应用仅接受两个版本的镜像内数据库结构指纹相同的情况；目前比较 Prisma schema 与 Manager 内建 DDL，不能代替实际数据库兼容性评估。结构不同或无法读取时停止自动应用，需另行制定数据库备份和兼容升级步骤。数据卷原地保留，镜像回退不会撤销数据写入

## 旧版本没有管理页面时

后台和服务器命令不随业务镜像回退。在服务器部署目录使用：

```bash
python3 releasectl.py status
python3 releasectl.py apply 备份ID
python3 releasectl.py scan
```

`status` 列出当前版本、备份 ID 和最近任务。如果切换与自动恢复都失败，可再次尝试保存的恢复点：

```bash
python3 releasectl.py recover
```

后台重启时会处理未完成的切换：替换阶段的任务尝试恢复原版本，尚未开始的队列任务重新核对运行版本后执行。磁盘损坏或 Docker 不可用时仍可能需要人工修复，可先查看 `docker compose logs release-worker`，不要执行删除业务数据卷的命令

## 从运行版本的代码继续修改

服务器镜像回退不会修改电脑上的 Git 历史。在页面选中目标版本，说明卡片下面提供两组 PowerShell 命令，可直接复制

创建修复分支适合从任意旧版本继续修改：先保存本地修改，再从选中 commit 创建 `codex/fix-提交前缀` 分支，原分支保留。分支名重复时改用新的名称。完成修复并测试后提交，再按需要推送该分支，GitHub 将构建新的 commit 镜像

如果分支起点很旧，尚未包含新的自动构建触发规则，在 GitHub Actions 的 `Publish deployment images` 中选择含本功能的工作流分支，手动运行并将 `source_ref` 填为修复分支名或完整 commit。构建会使用选中源码的真实提交号作为标签和 revision，不会误记成工作流所在分支的提交号

`git revert` 适合在当前分支撤销目标 commit 之后的改动，产生新的撤销提交。命令先检查工作区、祖先关系及合并提交，显示准备撤销的范围，并要求输入 `REVERT`。发生冲突时解决后继续，或 `git revert --abort` 取消。已推送的提交不通过 reset 或强制推送删除，因此上一版本也推荐用分支或 revert

以上命令不自动 push，不自动切换服务器版本。说明内容可以手写修改，命令中的目标 commit 始终根据选中的版本生成
