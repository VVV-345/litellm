# LiteLLM 与号池迁移手册

适用场景：业务镜像已经发布到 GHCR，将现有服务迁到另一台 Linux Docker 主机，保留账号、密钥、模型配置、日志和版本记录。本文提供人工分阶段执行的模板，不是一键脚本，也不表示已在下一台服务器实跑

最重要的一点：**拉镜像只搬程序。完整迁移还需要两套数据库、Manager 配置卷、每个账号的独立认证卷、原密钥及实际部署配置**。先迁移相同运行版本，验收后再单独升级

实际操作顺序：盘点旧机 -> 拉取相同版本镜像 -> 停写并打包数据 -> 新机空环境恢复 -> 验收 -> 切换入口 -> 保留旧机以便回退。一次计划内停机迁移最简单可靠；本文不提供不停机双向同步方案

## 1. 要搬什么

| 对象 | 迁移方法 | 漏掉的影响 |
| --- | --- | --- |
| LiteLLM、Manager 镜像 | 按原版本从 GHCR 拉取，记录完整 commit、tag、digest 和平台 | 误升级可能触发 schema 或协议变化 |
| CLIProxyAPI 镜像 | 独立记录每个环境的实际镜像，不只看 Manager 默认值 | 老账号可能依赖不同版本 |
| LiteLLM PostgreSQL | 逻辑备份及恢复 | 用户、虚拟密钥、模型、费用和请求记录缺失 |
| 号池 PostgreSQL | 单独逻辑备份及恢复 | 环境 UUID、卡片 Key、策略、代理和设置缺失 |
| Manager 数据卷 | 停写后归档，含各 UUID 目录中的 Compose | 数据库有账号，但找不到对应容器定义 |
| 每个账号的独立数据卷 | 逐卷备份整个 `/data`，包括 `config`、`auths`、`plugins` 及其他运行状态 | 认证、API 配置、插件或冷却状态丢失 |
| `.env`、Compose、override、配置文件和密钥 | 从实际部署路径原样备份，单独加密保管 | 连接失败、鉴权失败、加密数据不可读 |
| Mihomo/Clash | 配置、订阅材料、选择状态、数据库及所有实际挂载 | 代理页报错，账号无法出网 |
| 完整日志与运行日志 | 备份实际 volume/bind 目录，SQLite 停写后连同 WAL 等文件保存 | 日志页缺历史，PostgreSQL 备份无法补回正文 |
| release-worker 与版本档案 | 保存 worker 版本、启动参数、令牌、部署目录及完整归档 | 网站可用但版本管理不可用或无法回退 |
| 反向代理、域名与 HTTPS | 保存 Caddy/Nginx 配置、相关证书卷，重建 DNS/入口路由 | 内网健康但公网不可达 |
| Prometheus 等可选组件 | 配置加独立存储备份，或明确放弃历史并签字确认 | 监控历史丢失 |
| 外部依赖 | 列清外部数据库、Redis、对象存储、SSO、SMTP、IP 白名单等实际使用项 | 容器正常也可能业务失败 |

Manager 配置卷与账号数据卷是两类不同数据。当前实现按环境 UUID 派生 `account-pool-<UUID去连字符>-data`，不能只备份 Compose 顶层列出的几个卷。卷名、数据库中的 UUID、生成的 Compose 三者必须一致，不要通过“重新创建同名卡片”代替恢复

不需要迁移 `/var/lib/docker` 整棵目录、Docker Socket、本机 Docker 二进制或 SSH 登录私钥。新机安装符合目标架构的 Docker/Compose；如果旧版 Manager 挂载宿主 CLI/插件，要重新核对新机路径及动态库兼容性

## 2. 先盘点，再确定迁移范围

### 执行约定

以下命令在 Linux **Bash** 中执行，数据库二进制 dump 不经 Windows PowerShell 重定向。需要 Docker 权限及读取数据目录的权限，建议在专用维护会话中使用 `sudo -i`。安装好 Docker Engine、Compose v2、Python 3、GNU tar、SSH 和 SHA-256 工具

`SOURCE_*` 表示源机实测值，`TARGET_*` 表示目标机确认值。先替换大写占位内容，任何值不清楚就停在本步骤，不能猜。每个章节可使用新会话，需重新设置它用到的变量

在源机只读盘点：

```bash
set -euo pipefail
umask 077
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="/root/litellm-transfer-$RUN_ID"
mkdir -m 700 "$BACKUP"
docker ps -a --format '{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}'
docker compose ls -a
```

人工确认属于本项目的容器，包含独立 Compose 项目的账号容器、数据库、代理和 worker。不要把同机其他服务加入停机列表。然后导出原始清单到私密目录，**不要把 inspect 或渲染配置贴到聊天、工单或 Git**，其中可能含密码

```bash
CONTAINERS=(SOURCE_LITELLM SOURCE_MANAGER SOURCE_DB SOURCE_POOL_DB SOURCE_ACCOUNT_1 SOURCE_PROXY SOURCE_WORKER)
docker inspect "${CONTAINERS[@]}" > "$BACKUP/containers.PRIVATE.json"
python3 - "$BACKUP" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
items = json.loads((p / 'containers.PRIVATE.json').read_text())
with (p / 'mounts.tsv').open('w') as f:
    print('container\ttype\tvolume\tsource\tdestination\trw', file=f)
    for c in items:
        for m in c['Mounts']:
            print(c['Name'].lstrip('/'), m['Type'], m.get('Name', ''),
                  m['Source'], m['Destination'], m['RW'], sep='\t', file=f)
with (p / 'images.tsv').open('w') as f:
    for c in items:
        print(c['Name'].lstrip('/'), c['Config']['Image'], c['Image'], sep='\t', file=f)
with (p / 'compose-labels.json').open('w') as f:
    json.dump({c['Name']: {k: v for k, v in c['Config'].get('Labels', {}).items()
                          if k.startswith('com.docker.compose.')}
               for c in items}, f, indent=2)
PY
```

依据 labels 找到全部 `project.config_files` 和 `project.working_dir`，保留 Compose 文件顺序、项目名、`.env`、`env_file` 和外部 secrets 文件。显式指定所有 `-f`，不要依赖当前目录自动加载可能过期的 override

```bash
SOURCE_PROJECT=SOURCE_PROJECT_NAME
SOURCE_DIR=/ABSOLUTE/SOURCE_DEPLOY_DIRECTORY
SOURCE_COMPOSE=/ABSOLUTE/SOURCE_COMPOSE_FILE
SRC=(docker compose --project-directory "$SOURCE_DIR" --env-file "$SOURCE_DIR/.env" -p "$SOURCE_PROJECT" -f "$SOURCE_COMPOSE")
"${SRC[@]}" config > "$BACKUP/compose-rendered.PRIVATE.yaml"
"${SRC[@]}" config --services > "$BACKUP/services.txt"
"${SRC[@]}" config --images > "$BACKUP/compose-images.txt"
docker image inspect "$(docker inspect -f '{{.Image}}' SOURCE_LITELLM)" > "$BACKUP/litellm-image.json"
```

有多个文件就按原顺序在 `SRC` 数组中追加 `-f`；运行实例没有 `.env` 时移除 `--env-file`，改用经确认的实际变量来源。最终以 `docker inspect` 的运行镜像、mounts、网络和 labels 为准，README、默认 Compose 或 `current-deployment.json` 可能落后于运行状态

再核对数据库中的环境列表、Manager UUID 目录、所有账号卷。已经停止或容器已删除的账号也可能有待恢复数据，单靠 `docker ps` 不足以查全。为每个 mount 标注 `PG逻辑备份 / 普通卷归档 / bind归档 / 新机重装 / 不迁移及原因`，不能有未分类项

## 3. 镜像从 GHCR 拉，版本要锁定

本仓库 `.github/workflows/publish-deployment-images.yml` 使用 commit 前 10 位作为 tag，为下列两个镜像发布 `linux/amd64`、`linux/arm64` 及共同的多架构 tag：

```text
ghcr.io/vvv-345/litellm:<DEPLOY_TAG>
ghcr.io/vvv-345/account-pool-manager:<DEPLOY_TAG>
```

CLIProxyAPI 来自另一个仓库的 `ghcr.io/vvv-345/cliproxyapi`，其 tag/digest 以实际发布结果为准，不套用上述命名规则。`release-worker` 复用 Manager 镜像，但可能固定在另一个历史 commit，必须单独记录

在目标机，私有包使用仅有必要读取权限的凭据，不把 PAT 写入命令历史：

```bash
read -r -p 'GHCR username: ' GHCR_USER
read -r -s -p 'GHCR read token: ' GHCR_TOKEN; printf '\n'
printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
unset GHCR_TOKEN
IMAGE_REF='ghcr.io/vvv-345/litellm:SOURCE_DEPLOY_TAG'
docker buildx imagetools inspect "$IMAGE_REF"
docker pull "$IMAGE_REF"
docker image inspect "$IMAGE_REF" --format '{{.Os}}/{{.Architecture}} {{json .RepoDigests}}'
```

对 Manager、worker、各账号镜像和基础镜像重复验证。优先在迁移配置中固定多架构 manifest 的 digest；不要把 amd64 子镜像摘要用于 arm64。也不要继承旧 Compose 的 `platform: linux/amd64` 到 arm64 主机而不核对

本工作流清理策略保留最近 20 个镜像版本。**记录 digest 不会阻止 GHCR 删除镜像**。长期回退需保留实际镜像归档或受控 registry 副本；跨架构必须保留目标架构的变体，源机 `docker save` 通常不能提供尚未拉过的另一架构镜像。旧版本已被清理时，先从准确 commit 重新构建所需架构并验证，不能悄悄改用 `latest`

仓库 `deploy/.env.example` 中的历史 CLIProxyAPI 默认值不等于当前生产值，恢复时使用清单中的实际引用

## 4. 备份：初始演练与最终快照分开

### 4.1 确定停写边界

可先做初始快照用于演练，但它不是最终切换数据。最终快照前进入维护窗口：阻断新业务请求和后台管理写入，等待流式请求结束，暂停上号、定时任务、同步、日志清理、发布任务，再停止 LiteLLM、Manager、CLIProxyAPI 账号实例及其他写入进程

**CLIProxyAPI 的 token 自动刷新也属于写入**。同一份认证在源机和目标机同时运行可能轮换 refresh token，导致一边失效。离线演练保持目标账号进程停止或在启动前隔离其上游出网；需要真实上游验收时，先停源端对应账号，或用独立测试凭据

```bash
WRITERS=(SOURCE_LITELLM SOURCE_MANAGER SOURCE_ACCOUNT_1 SOURCE_WORKER)
docker stop --time 300 "${WRITERS[@]}"
docker inspect --format '{{.Name}} {{.State.Status}}' "${WRITERS[@]}"
```

列表必须补齐所有动态账号、外部写入者，以及需归档的 Mihomo、监控等文件写入者。确认没有 systemd、cron、外部编排器把它们拉起，也不要在冻结期间重启 Docker。数据库保持运行用于逻辑备份。两个数据库各自的 dump 只有单库一致性；只有共同停止全部写入，才能与认证文件形成同一冻结时间点

### 4.2 两套 PostgreSQL 逻辑备份

使用数据库容器内匹配版本的 `pg_dump`，不在线打包 PostgreSQL 数据目录，不在迁移时顺带升级 PostgreSQL 大版本。下例 `POSTGRES_USER/POSTGRES_DB` 仅适用于实际采用这些变量的官方容器，先在服务器内核对；外部数据库改用受控连接与 `.pgpass`，不要在命令里暴露密码

```bash
DB_CONTAINERS=(SOURCE_DB SOURCE_POOL_DB)
DB_FILES=(litellm account-pool)
for i in 0 1; do
  c="${DB_CONTAINERS[$i]}"; n="${DB_FILES[$i]}"
  docker exec "$c" sh -ec 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$BACKUP/$n.dump"
  docker exec "$c" sh -ec 'pg_dumpall -U "$POSTGRES_USER" --globals-only' > "$BACKUP/$n-globals.PRIVATE.sql"
  test -s "$BACKUP/$n.dump"
  docker exec -i "$c" pg_restore --list < "$BACKUP/$n.dump" > "$BACKUP/$n-toc.txt"
done
```

globals 包含角色及可能的密码散列，和认证卷一样按密钥材料保管。如果实例上还有其他业务库，不能把整份 globals 直接应用到共享目标数据库

记录停写后的环境数、卡片 Key 数、模型与用户数、主要日志表行数、最新业务时间。可保存全部业务表精确计数 SQL 的结果，但大日志表 `COUNT(*)` 有开销，应提前估算停机时间。恢复后用**同一条查询**比较，不能只比较 dump 文件大小

### 4.3 普通 named volume 与账号认证卷

从清单手工生成 `volumes.txt`，每行一个需要归档的非 PG 卷名，包含 Manager 卷、每个账号卷及独立日志卷。不要按一个 Compose 项目名前缀过滤，否则容易漏掉动态账号

以下示例仅用于 rootful Docker 的本地普通卷。NFS、插件卷、rootless 或 user namespace remap 需要按实际存储驱动和 UID 映射设计备份恢复方法

```bash
mkdir -m 700 "$BACKUP/volumes"
while IFS= read -r vol; do
  test -n "$vol"
  [[ "$vol" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]
  docker volume inspect "$vol" > "$BACKUP/volumes/$vol.inspect.json"
  test "$(docker volume inspect -f '{{.Driver}}' "$vol")" = local
  test "$(docker volume inspect -f '{{len .Options}}' "$vol")" = 0
  mp="$(docker volume inspect -f '{{.Mountpoint}}' "$vol")"
  test -d "$mp"; test "$mp" != /
  tar --numeric-owner --acls --xattrs -C "$mp" -czpf "$BACKUP/volumes/$vol.tar.gz" .
  (cd "$mp"; find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum) > "$BACKUP/volumes/$vol.files.sha256"
done < "$BACKUP/volumes.txt"
```

单独保留权限、目录、符号链接及 UID/GID 信息，不要只复制 `auths/*.json`。当前常见账号进程 UID/GID 为 `65532:65532`，但应从实际容器核对；不能给所有卷一律 `chmod 777` 或 `chown 65532`

### 4.4 bind、配置、密钥、日志与版本目录

将选定的 bind 路径写进 `bind-paths.txt`，使用相对 `/` 的路径，每行一项。包括实际部署目录、散落在其他位置的配置、Mihomo 数据、完整日志、release 根目录、反向代理及 HTTPS 材料。不包含 PG data、Socket、宿主二进制、其他项目目录或备份输出目录自身

```bash
tar --numeric-owner --acls --xattrs -C / -czpf "$BACKUP/binds.tar.gz" -T "$BACKUP/bind-paths.txt"
tar -tzf "$BACKUP/binds.tar.gz" > "$BACKUP/binds-index.PRIVATE.txt"
```

SQLite 完整日志应在所有写入进程停止后保存整个目录，不能运行中仅复制主 `.sqlite3` 而漏掉 WAL。运行中的监控数据库按其官方快照机制备份，或停机后归档

密钥核对表只记“已保存/已核对”，不写具体值：

| 配置 | 恢复要求 |
| --- | --- |
| `ACCOUNT_POOL_SECRET_SEED` | 原样保留；它派生环境管理/网关密钥和授权状态加密密钥，重新生成会破坏现有关系 |
| `ACCOUNT_POOL_MANAGER_TOKEN` | LiteLLM 与 Manager 保持相同原值 |
| `LITELLM_MASTER_KEY`、实际使用的 `LITELLM_SALT_KEY` 等 | 连同数据库恢复，保留加密/解密所依赖的原值；不假设所有版本都只依赖一个 key |
| 数据库账号、密码和连接串 | 与目标数据库实际角色一致；只改 `.env` 不会修改已初始化库的密码 |
| `ACCOUNT_POOL_RELEASE_TOKEN` | worker、LiteLLM 及维护 CLI 的配置一致，不能忽略未设置警告 |
| `ACCOUNT_POOL_CLASH_SECRET`、订阅、上游 Key、证书 | 原样私密迁移，必要的轮换另开步骤 |
| UUID、生成配置中的管理 Key 和 API Key | 保留标识及对应关系，不根据卡片显示名称重建 |

### 4.5 校验与传输

```bash
(cd "$BACKUP"; find . -type f ! -name SHA256SUMS -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum > SHA256SUMS)
(cd "$BACKUP"; sha256sum -c SHA256SUMS)
```

备份目录权限保持 0700，敏感文件 0600；离线或长期备份再用批准的加密备份工具加密，解密密钥与备份分开保管。摘要检测损坏，不能代替加密或可信来源验证

有 SSH 时，先在目标建立仅迁移账号可访问的目录，再通过受信任连接复制整个包。核对目标 SSH host key，不关闭 host-key 检查。只有 JumpServer 时，用对应资产的 SFTP 文件管理上传，不操作旁边的其他资产

```bash
TARGET_SSH=USER@TARGET_HOST
TARGET_BACKUP=/ABSOLUTE/PRIVATE_TRANSFER_DIRECTORY
scp -rp "$BACKUP/." "$TARGET_SSH:$TARGET_BACKUP/"
```

在目标机先收紧权限并执行 `sha256sum -c SHA256SUMS`，全部通过才解包。临时 `/tmp` 上传副本同样含认证，需限制权限并登记在迁移后清理清单，不能遗忘

## 5. 目标机恢复

### 5.1 先准备隔离环境

目标使用新的部署目录和空卷，不覆盖任何现有生产数据。确认磁盘能容纳压缩包、解压数据、数据库导入、镜像和回滚副本，检查系统时间、DNS、架构及 Docker 网络子网与宿主 VPN/局域网是否冲突

先解包到 staging，**不要直接向 `/` 覆盖**：

```bash
BACKUP=/ABSOLUTE/TARGET_PRIVATE_TRANSFER_DIRECTORY
(cd "$BACKUP"; sha256sum -c SHA256SUMS)
STAGING=/ABSOLUTE/NEW_EMPTY_STAGING
mkdir -m 700 "$STAGING"
tar -tzf "$BACKUP/binds.tar.gz" > "$STAGING/archive-index.PRIVATE.txt"
tar --numeric-owner --acls --xattrs -xzpf "$BACKUP/binds.tar.gz" -C "$STAGING"
```

只接受自己生成且已校验的归档，先检查路径和符号链接。将 staging 内各对象复制到确认的新绝对路径，保留原始备份不改。生成“源路径/卷名 -> 目标路径/卷名”映射，随后只修改目标配置副本

| 必查字段 | 新机要核对的内容 |
| --- | --- |
| 所有 Compose 文件与 `.env` | 不混用旧 override；JSON Compose 同样有效，扩展名不是问题 |
| `COMPOSE_PROJECT_NAME`、`volumes.*.name` | 项目名变化会改变默认卷名，显式绑定恢复后的卷，防止启动出空库 |
| bind source、`ACCOUNT_POOL_DATA_ROOT` | 区分宿主路径与容器内路径；生成 Compose 的宿主可访问位置正确 |
| `ACCOUNT_POOL_MANAGER_CONTAINER`、`ACCOUNT_POOL_GATEWAY_CONTAINER` | 必须等于实际容器名，Compose 自动命名后常与旧值不同 |
| `ACCOUNT_POOL_DOCKER_HOST` | 指向 Socket Proxy 内网地址，Manager 不改为直接挂载 Docker Socket |
| 代理地址、控制器 URL、端口和 `extra_hosts` | 从 Manager 及每个账号容器实际可达；不硬编码旧 Docker 网关地址 |
| `ACCOUNT_POOL_SSH_HOST/USER`、回调端口 | 页面提供的 OAuth 隧道需指向新机或新堡垒机访问路径 |
| release 的部署路径、项目名、归档路径 | `ACCOUNT_POOL_RELEASE_DEPLOYMENT/PROJECT/ROOT` 与实际运行文件一致，环境变量映射见原 Compose |
| 域名、SSO/OAuth 回调、反向代理 upstream | 新地址、证书、允许来源及 cookie 策略匹配，不能用关闭校验解决 |

账号网络保留独立 bridge 和上游出网能力，不设置 `internal: true`，也不发布 CLIProxyAPI 宿主端口。共享控制网络、数据库网络和 Socket Proxy 网络保持受限；Socket Proxy 只允许可信 Manager/worker 接入，LiteLLM 不接入 Socket Proxy 网络

代理端口只给必要的容器网络/管理来源使用。迁移前后的 host 模式、bridge 模式不能机械互换；桥接容器里的 `127.0.0.1` 不是宿主机。反向代理若在容器中，也不能将它的 localhost 当成 LiteLLM 宿主发布端口

### 5.2 恢复普通卷

依据映射表逐卷执行。下面的目标卷必须不存在；拒绝覆盖已有卷。保留 UUID 派生的账号卷名，Manager 等 Compose 前缀卷需要显式映射

```bash
SOURCE_VOLUME=SOURCE_VOLUME_NAME
TARGET_VOLUME=TARGET_VOLUME_NAME
if docker volume inspect "$TARGET_VOLUME" >/dev/null 2>&1; then
  printf '拒绝覆盖已有卷: %s\n' "$TARGET_VOLUME" >&2
  exit 1
fi
docker volume create "$TARGET_VOLUME"
mp="$(docker volume inspect -f '{{.Mountpoint}}' "$TARGET_VOLUME")"
test -d "$mp"; test "$mp" != /
test -z "$(find "$mp" -mindepth 1 -maxdepth 1 -print -quit)"
tar --numeric-owner --acls --xattrs -xzpf "$BACKUP/volumes/$SOURCE_VOLUME.tar.gz" -C "$mp"
(cd "$mp"; sha256sum -c "$BACKUP/volumes/$SOURCE_VOLUME.files.sha256")
```

核对文件总数、目录/链接、权限、UID/GID 和 hash，记录目标卷名。目标如启用 user namespace 或 SELinux，应先完成正确的 UID 映射或挂载标签设计，不照抄上述 rootful 本地卷命令

### 5.3 初始化并恢复数据库

先定义目标 Compose 命令，所有卷映射已经确认后才创建容器：

```bash
TARGET_DIR=/ABSOLUTE/TARGET_DEPLOY_DIRECTORY
TARGET_COMPOSE=/ABSOLUTE/TARGET_COMPOSE_FILE
TARGET_PROJECT=TARGET_PROJECT_NAME
DST=(docker compose --project-directory "$TARGET_DIR" --env-file "$TARGET_DIR/.env" -p "$TARGET_PROJECT" -f "$TARGET_COMPOSE")
"${DST[@]}" config --quiet
"${DST[@]}" config --services
"${DST[@]}" config --images
"${DST[@]}" pull
"${DST[@]}" up -d --no-deps db account-pool-db
"${DST[@]}" ps
```

这里 `db/account-pool-db` 是仓库示例服务名，实际名称以清单为准。确认数据库健康，且对应的是**本次新建空库**，没有业务表、没有应用写入者。逻辑恢复有两种策略，先选定再执行：

* 常见单应用角色部署：目标用原应用角色初始化，把所有业务对象恢复为该角色所有，不恢复额外 ACL。下面命令对应此策略
* 存在额外角色、GRANT、扩展 owner 或 tablespace：先审阅两份 globals，重建必要角色、目录和授权；处理与目标 bootstrap 角色重复的问题，再保留原 owner/ACL 恢复。不能把 globals 盲目执行到共享集群，也不能一概忽略恢复错误

```bash
TARGET_DBS=(TARGET_DB_CONTAINER TARGET_POOL_DB_CONTAINER)
DB_FILES=(litellm account-pool)
for i in 0 1; do
  docker exec -i "${TARGET_DBS[$i]}" sh -ec \
    'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges --exit-on-error --single-transaction' \
    < "$BACKUP/${DB_FILES[$i]}.dump"
done
```

不用 `--clean` 擦除现有库。失败后保留日志，查明原因，在另一个新空库重试，不把部分导入当成功。通过后比较停写时记录的表计数、UUID 集合和时间边界，并验证应用角色确实可读写、必要扩展存在。初始化环境变量不会重置已存在数据库中的角色密码

### 5.4 按依赖启动，不先放公网流量

恢复顺序是：数据与密钥、数据库、Socket Proxy 和出网代理、账号运行环境、Manager/LiteLLM、worker、入口。先检查 Compose 的真实 `depends_on`，必要时分阶段创建容器解决控制面互相引用

```bash
"${DST[@]}" up -d --no-deps docker-socket-proxy
"${DST[@]}" up --no-start --no-deps --no-build litellm account-pool
```

在存在 Mihomo 的实际 Compose 中启动其服务，并从预期容器网络验证控制器和出口。然后对每个已恢复的环境 Compose 执行检查，确认镜像、volume name、无宿主端口、代理地址及网络无误。**只有源端对应账号已停止，才运行下方 `up`**：

```bash
ENV_COMPOSE=/ABSOLUTE/RESTORED_MANAGER_DATA/ENV_UUID/compose.yaml
docker compose -f "$ENV_COMPOSE" config --quiet
docker compose -f "$ENV_COMPOSE" pull
docker compose -f "$ENV_COMPOSE" up -d --no-build
"${DST[@]}" start account-pool
```

Manager 的启动恢复流程可能需要 LiteLLM 和 Manager 容器已经存在，以便连接账号网络，所以前面先创建但不启动。若仍等待，检查实际容器名、网络与对应版本日志，不修改数据库绕过恢复，不把增加超时当作修复

Manager 健康后启动 LiteLLM，再验证内部接口。保留原来手动 disabled 的账号，不批量启用，不因迁移重置模型范围或并发限制

```bash
"${DST[@]}" start litellm
"${DST[@]}" ps
```

恢复 worker 前要确认它指向新的实际部署配置，不能指向过期的源码目录。保留其固定镜像版本、归档索引和令牌，并按 [版本管理说明](RELEASES.md) 验证：

```bash
"${DST[@]}" up -d --no-deps release-worker
python3 "$TARGET_DIR/releasectl.py" --directory "$TARGET_DIR" status
```

worker 启动可能做镜像备份/历史扫描，因此应在配置及归档核对后再启动。跨架构迁移保存的 amd64 归档不保证能在 arm64 `apply`，必须逐版本验证。不要为了验收在生产随意执行 `deploy/apply/delete`

## 6. 验收：网站、账号、模型分别检查

所有结果记录命令、输入、时间、退出状态及脱敏输出。敏感原始日志仅在受控服务器私密文件保存。Docker `running`、Compose `config` 成功和首页 HTTP 200 都不能替代下表

| 验收项 | 通过标准 |
| --- | --- |
| 配置与镜像 | 无缺失必要变量，运行 commit/platform 符合清单，卷名及挂载正确 |
| 数据恢复 | 两库关键计数/UUID/时间边界一致，所有账号卷文件数和 hash 一致 |
| Manager 与 LiteLLM | Manager `/health`、LiteLLM `/health/liveliness` 返回正常状态 |
| 完整登录 | 真实浏览器登录后预加载全部完成，密钥、模型、号池、代理和日志可打开 |
| 管理接口 | 登录态访问 `/account_pool/environments`、`/account_pool/proxy-gateways`、`/logs/operations?offset=0&limit=1` 成功，不只看 HTTP 状态，还检查业务内容 |
| 认证与卡片 | 旧 UUID、禁用状态、模型范围、并发/冷却、代理和凭据数量保留；有效凭据通过验证，失效项明确登记 |
| 模型请求 | 使用原有限权业务 Key，`/v1/models` 返回预期模型，至少完成一次普通及一次流式真实请求 |
| 记账和日志 | 上述请求可查，状态、用量、费用及请求关联正确；不能把缺失价格的 0 当作免费 |
| 完整日志 | 旧会话可查，按原记录设置检查新请求正文，未开启时不强行打开 |
| 版本管理 | worker 健康，status 能读取当前运行版本及备份，目标架构可用归档明确 |
| 安全与重启恢复 | Socket/数据库/代理不对公网开放，账号间隔离保留；维护窗口重启相关业务服务后再次通过验收 |
| 公网入口 | 域名、HTTPS、根路径跳转、登录和流式转发正常，外部访问落在目标实例 |

健康检查端口以实际配置为准：

```bash
MANAGER_BASE=http://127.0.0.1:TARGET_MANAGER_PORT
LITELLM_BASE=http://127.0.0.1:TARGET_LITELLM_PORT
curl --fail-with-body --max-time 15 "$MANAGER_BASE/health"
curl --fail-with-body --max-time 15 "$LITELLM_BASE/health/liveliness"
```

真实模型验收使用一个已启用、额度可用的模型，可能产生少量正常调用费用。下面只给模型调用模板，不把管理 token 当业务 Key，不导出浏览器 cookie：

```bash
set -euo pipefail
umask 077
EVIDENCE=/ABSOLUTE/PRIVATE_ACCEPTANCE_DIRECTORY
mkdir -m 700 "$EVIDENCE"
API_BASE=https://TARGET_DOMAIN
read -r -s -p 'Limited business API key: ' API_KEY; printf '\n'
printf 'Authorization: Bearer %s\n' "$API_KEY" > "$EVIDENCE/auth.header"
unset API_KEY
curl --fail-with-body --max-time 30 -H @"$EVIDENCE/auth.header" \
  "$API_BASE/v1/models" -o "$EVIDENCE/models.json"
read -r -p 'Enabled model ID from models.json: ' MODEL
python3 - "$MODEL" "$EVIDENCE" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[2])
for stream in (False, True):
    payload = {'model': sys.argv[1], 'messages': [{'role': 'user', 'content': 'Reply OK'}], 'stream': stream}
    (p / ('stream.json' if stream else 'request.json')).write_text(json.dumps(payload))
PY
curl --fail-with-body --max-time 120 -H @"$EVIDENCE/auth.header" -H 'Content-Type: application/json' \
  --data-binary @"$EVIDENCE/request.json" "$API_BASE/v1/chat/completions" -o "$EVIDENCE/response.json"
curl --fail-with-body --max-time 120 --no-buffer -H @"$EVIDENCE/auth.header" -H 'Content-Type: application/json' \
  --data-binary @"$EVIDENCE/stream.json" "$API_BASE/v1/chat/completions" -o "$EVIDENCE/stream.txt"
rm -- "$EVIDENCE/auth.header"
```

若中途失败，按清单单独清理 `auth.header`，不要把它提交到 Git。验证 response 的实际内容及流式结束标记，不把 HTTP 200 中的错误对象当成功。生产使用 Responses、Messages、WebSocket 等其他协议时，还需逐个验收，Chat Completions 成功不代表全部兼容

**认证文件恢复不等于重新授权**。上游已过期、撤销、轮换失效的 refresh token，需要在新机重新授权；原先 disabled 或没有启用模型的账号不会因为迁移自动恢复可用。`/v1/models` 返回 `[]` 时先处理认证和模型选择，不能宣布推理验收通过

## 7. 最终切换与防止双活

1. 提前记录 DNS TTL、旧入口配置和回退负责人，确认不会误改同机其他网站或堡垒机
2. 演练完成后，停止并隔离演练环境，保留演练记录；最终恢复使用另一套新空卷/空库，不覆盖演练或生产数据
3. 源端进入维护并完成全部停写，生成同一冻结时间点的两库 dump、账号卷、配置与日志快照，重新生成摘要
4. 目标恢复最终包，源端保持停止；完成私网验收及经批准的真实上游请求，再切 DNS/反向代理/现有入口路由
5. 从外网确认请求实际进入目标，检查完整登录、普通/流式调用和记账。DNS 缓存期间旧入口保持维护或按明确设计转发，不能重新启用旧应用写入
6. 记录切换时间、镜像版本、数据快照 ID、目标首条成功请求和负责人；观察期保留源端与备份，清理另外确认

不能只切网页域名而忘记 API 客户端直连旧 IP、旧定时任务或旧账号自动刷新。未执行最终停写和最终同步，就应明确记录“初始快照恢复，尚未最终一致”

## 8. 回滚分三层

| 回滚类型 | 做什么 | 不能替代什么 |
| --- | --- | --- |
| 配置副本回滚 | 恢复原 Compose/Caddy 字节并核对 hash，在临时副本执行解析检查 | 不会恢复数据库、账号卷或线上容器状态 |
| 应用镜像回滚 | 按 release-worker 兼容检查恢复指定版本配置和镜像 | 不能撤销新数据库写入、schema 变化或 token 轮换 |
| 整站迁移回退 | 停目标写入及账号刷新，处理目标新增状态后恢复源端和入口 | 不能简单切 DNS 就保证零丢失 |

目标**完全未产生任何新状态**时，可保持目标停机，核对源端仍是最终冻结快照，再恢复源端并切回入口。仅“还没放用户流量”不够，启动迁移、后台任务、测试调用、token 刷新都可能写入

目标已经产生业务数据或新认证时，先冻结目标，备份其最新两库、账号卷和配置，判断能否按本手册反向恢复到新的源端空环境。不可直接覆盖旧库或丢掉目标增量。数据库 schema 兼容与上游 token 有效性是回退条件；不能反向恢复时，要明确停机修复或与负责人确认可接受的数据损失

不要执行 `docker compose down -v`、`docker volume prune`，也不要删除源服务器来“确保迁移成功”。清理临时包、旧卷、原机器属于单独的批准步骤

## 9. 常见故障

| 现象 | 优先检查与解决 |
| --- | --- |
| 后台一直 3/5、号池/操作日志 503 | Manager 启动恢复是否被缺失账号 Compose、认证卷、实例或网络阻塞；补齐数据和依赖，不只重启网页 |
| 后台 4/5、代理网关 500 | Mihomo 是否恢复，controller 地址/secret 是否正确，容器是否可达 |
| 数据库连接失败 | 目标 volume 是否空建错了，角色/密码是否与数据库真实状态一致，连接串是否还指旧主机 |
| 卡片存在但 unauthorized | seed、UUID、生成配置与认证卷是否配套；若归档本身认证失效，重新授权 |
| 模型接口 200 但空列表 | 认证是否有效、卡片是否 disabled、enabled models 是否为空、业务 Key 是否有权限 |
| 认证文件 permission denied | 检查文件 UID/GID、目录权限、user namespace 和 SELinux，勿开放全局写权限 |
| exec format error / no matching manifest | 检查目标架构、镜像平台及 digest 是否误用了单平台版本 |
| GHCR denied / manifest unknown | 检查包读取权限、tag 是否存在及是否被清理；不能用 latest 凑数 |
| Manager 网络连接失败 | 检查实际容器名、账号网络、Socket Proxy allowlist 及版本匹配 |
| 网站正常但版本管理失败 | worker、release token、部署目录/项目名、归档及目标架构缺一不可 |
| 内网正常、公网超时或 502 | 检查入口指向、反向代理与 LiteLLM 共用网络、证书和防火墙，不扩大数据库/Socket 暴露面 |
| OAuth 浏览器回调不到新机 | 更新页面生成的 SSH 路径及端口转发，堡垒机环境确认其支持的隧道方式；不开放匿名公网回调代替 |

## 10. 2026-09-29 迁移经验与遗留项

本次曾漏掉三个独立账号运行卷，只有数据库和 Manager 配置时，后台无法完成初始化。恢复账号实例后仍因 Mihomo 缺失卡在代理模块；补齐出网代理后，网站、号池、代理和操作日志接口恢复

这是历史记录，不是阅读本文时的实时验收。最后记录中三个账号均处于禁用状态，部分旧认证显示 unauthorized，模型列表为空，没有执行真实 completion，因此不能宣称全部模型可调用。release-worker、完整版本管理及监控尚未完成目标端验收，release token 仍有未配置警告；源端未完成最终停写和数据同步

当前部署已有配置副本、diff、`VERIFICATION.txt` 与 `ROLLBACK.sh`。其 BASELINE/ROLLBACK 服务集合为 `account-pool, account-pool-db, db, litellm`，MODIFIED 另外包含 `docker-socket-proxy, mihomo`。配置解析通过及副本 hash 回滚成功只证明配置事务，不代表整站或数据库回退已测试

下一次迁移前必须重新盘点实时状态，尤其确认新授权文件、worker 恢复情况和最终数据来源，不能复用这次较旧的认证压缩包当作最新备份

## 11. 交接记录模板

把填写后的记录和私密备份分开保管，不向公共仓库提交主机清单、密钥、dump、认证包或完整 inspect

```text
迁移负责人 / 复核人：
源端 / 目标端 / 架构：
业务范围 / 不迁移项及确认人：
运行镜像 commit / tag / manifest digest / 平台：
worker 与每个账号镜像版本：
配置文件顺序 / Compose 项目名 / 路径与卷映射：
密钥核对完成（仅写是或否）：
初始演练快照 ID / 结果：
最终停写时间 / 已停止的所有写入者：
最终快照 ID / SHA256SUMS / 加密备份位置：
两库恢复结果 / 角色与 ACL 策略 / 关键计数：
Manager 卷及逐账号卷数量 / 文件 hash / 权限核对：
登录 / 管理接口 / 代理 / 认证 / 普通与流式调用：
日志费用 / 版本管理 / 其他协议 / 重启恢复：
切换时间 / 入口变更 / 首条目标请求：
遗留问题 / disabled 或待重新授权账号：
回退条件 / 目标新增数据处理方案：
观察期 / 旧机保留期 / 临时敏感副本清理负责人：
```

首次部署参考 [部署指南](DEPLOY.md)，版本操作参考 [版本管理](RELEASES.md)，号池运行与网络边界参考 [Manager 说明](../account-pool/README.md)
