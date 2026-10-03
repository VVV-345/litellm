# 三种应用镜像与号池迁移

本项目只发布两种本仓库应用镜像。CLIProxyAPI 由独立仓库发布，三种镜像统一存放在 `ghcr.io/vvv-345/` 命名空间。PostgreSQL、Prometheus 和 Docker Socket Proxy 是运行依赖，不计入这三种应用镜像

| 应用镜像 | 源码仓库 | GHCR 仓库 | 当前源服务器使用的源码版本 |
| --- | --- | --- | --- |
| LiteLLM | [VVV-345/litellm](https://github.com/VVV-345/litellm) | `ghcr.io/vvv-345/litellm` | `9c35172f3b8906b94cfeee666ac7c3571e4e8575` |
| Manager | [VVV-345/litellm](https://github.com/VVV-345/litellm)，构建目录 `account-pool/` | `ghcr.io/vvv-345/account-pool-manager` | 主服务为 `9c35172f3b8906b94cfeee666ac7c3571e4e8575`；独立版本管理 worker 为 `d4b774ce2645f31ff77721335616b3b9245df906` |
| CLIProxyAPI | [VVV-345/CLIProxyAPI](https://github.com/VVV-345/CLIProxyAPI) | `ghcr.io/vvv-345/cliproxyapi` | `bebf587f5a940af676f6e503065140d4991ae1f3` |

Manager 主服务与版本管理 worker **共用 `account-pool-manager` 镜像仓库**，但保留两个运行容器。worker 在回退业务镜像时不能随业务容器一起替换；不创建第四种应用镜像。迁移当前状态时，两者还需分别使用上表所列版本，不能仅因镜像名相同就统一版本

本仓库的 `.github/workflows/publish-deployment-images.yml` 在原生 `amd64` 和 `arm64` Runner 分别构建，然后在同一个提交前缀标签下发布多架构清单。手动运行工作流时用 `source_ref` 指定完整提交，不要用当前分支代替旧版。CLIProxyAPI 仓库有独立的双架构发布流程；当前使用的 `sha-bebf587f5a940af676f6e503065140d4991ae1f3` 标签已包含两种架构

每次发布后执行 `docker buildx imagetools inspect ghcr.io/vvv-345/<镜像>:<标签>`，确认同时列出 `linux/amd64` 和 `linux/arm64`，记录顶层多架构清单 digest。部署时锁定已验证的标签和顶层 digest，不要直接使用 `latest`。能从 GHCR 拉取不代表数据库和配置已经兼容

号池 `192.168.64.1` 的源端快照位于 `/opt/zlia-pool/litellm-migration/`。后续在该目录隔离拉取对应架构的镜像，并恢复两份 PostgreSQL 逻辑导出、号池账号数据卷、日志、配置和密钥。先用独立容器名、数据卷和未占用端口验证数据库结构、认证材料、服务健康和业务接口；日志归档需承认它不是严格同一时刻快照。源服务器持续运行，因此切换前还要安排停写窗口，补齐数据库及可变文件的增量，核对记录后再切流量。保留源端和原站点作为回退，不覆盖 `/opt/zlia-pool/site` 或其他服务

## 数据库快照与受控回退

版本管理新增可选的 PostgreSQL 逻辑快照。默认 `ACCOUNT_POOL_RELEASE_DATABASE_BACKUPS=false`，更新代码或推送镜像不会自动停止业务、备份或恢复线上数据库。

### 启用与首次备份

1. 在隔离环境演练恢复，安排维护窗口并停止外部数据库写入。只支持同一 Compose 项目的 `db` 和 `account-pool-db`，业务连接必须分别指向它们，使用容器的 `POSTGRES_DB` 和 `POSTGRES_USER`。不支持外部数据库、跨 PostgreSQL 主版本恢复或自定义数据库连接映射。
2. 更新独立 `release-worker` 的镜像，并检查镜像 digest 属于该新版本；不要只改 tag 却保留旧 digest。同步新版 `releasectl.py`。worker 与业务镜像仍独立更新。
3. 确认 LiteLLM、Manager 与 worker 都已更新到支持该协议的版本，再在部署 `.env` 设置 `ACCOUNT_POOL_RELEASE_DATABASE_BACKUPS=true`，确保 Compose 将此变量传给 worker。自定义 `compose-db.json` 的部署需手动同步该环境变量，保留既有挂载、项目名及持久 Docker 认证，不改为其他部署目录。回退到旧版页面可能无法解析新快照协议，此时用新版独立 worker 和 `releasectl.py` 管理；不要跟随业务镜像回退 worker。
4. 重建 worker 后用 `python3 releasectl.py status` 确认 `database_backups_enabled=true`。点击“扫描并备份”或执行 `python3 releasectl.py scan`，确认后两个业务服务暂停，归档两库并在临时库实际试还原，完成后启动原容器。
5. 核对当前版本的 `database_snapshot` 编号、时间和两份文件。每次备份都刷新当前数据库，不因已有镜像归档而跳过。镜像归档时间和数据库快照时间分别展示。

两库没有跨库分布式事务；一致性依赖暂停全部业务写入者。外部直连写入者也必须停写，否则不能宣称两库属于同一业务时刻。临时库试还原需要数据库卷有足够空间，归档目录也需容纳新旧两代快照。备份、验证或启动失败会报告任务失败，不将损坏文件发布为新快照。

### 两种回退

- “检查并回退”只替换程序，保留当前数据。现有兼容门禁与受控强制确认保持不变；备份功能不解除新版部署的 schema 差异阻断，真实变更仍需迁移审查。
- “恢复程序与数据”需要该版本实际运行时生成的两库快照。界面显示恢复时刻并要求输入 `RESTORE <完整快照编号>`，服务器签发确认后还需等待倒计时。数据库回到快照时刻，后续新增和修改不再出现在运行库中，不承诺无损降级。
- 命令入口：`python3 releasectl.py restore_data <24位备份版本ID> --snapshot <32位快照ID>`。备份版本 ID 不是 Git commit。任务先保存操作前的新恢复点，校验目标归档并在临时库恢复两份归档，之后才用 PostgreSQL `pg_restore --clean --create` 重建目标业务数据库并启动目标镜像。
- 仅接受相同 PostgreSQL 主版本、数据库名称、部署配置与加密设置。认证文件、外部日志协议变化仍会阻断。未运行过的历史镜像不能使用“当前数据库”冒充自己的历史快照。
- 数据库恢复中途或健康检查失败，会暂停业务、终止此 worker 遗留的数据库会话，并尝试恢复操作前两库与镜像。worker 重启也按持久任务阶段恢复。自动恢复失败时保持故障记录，禁止新发布，使用 `python3 releasectl.py recover` 继续；先确认两库和应用健康再恢复入口流量。

### 文件替换与搬迁

数据库归档位于 `<ACCOUNT_POOL_RELEASE_ROOT>/backups/<版本ID>/database-<快照ID>-db.dump` 和 `database-<快照ID>-account-pool-db.dump`。每代使用独立文件名，权限为 `600`；两份归档验证成功后更新 manifest 和 SQLite 元数据，再清理没有被未完成恢复任务引用的旧代。生成失败不覆盖旧文件，不会因同名镜像归档而跳过数据库更新。

覆盖只更新该版本的最新快照引用，不代表合并两代数据。需要长期保留的每日或里程碑快照应另做离线备份。发布存储、两个业务数据库、账号认证卷、日志卷、环境配置与加密密钥仍需一起搬迁；这项功能不包含 OAuth 文件、密钥轮换、角色/扩展安装、对象存储或 WAL 连续归档，也不能把新 schema 的 WAL 直接回放到不兼容的旧 schema。

恢复保证仅限经过验证的版本与快照组合，不保证任意历史版本都能启动或调用上游。上线前还需在目标架构验证站点登录、账号认证、真实请求和数据库读写。
