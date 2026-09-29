# 三种应用镜像与号池迁移

本项目只发布两种本仓库应用镜像。CLIProxyAPI 由独立仓库发布，三种镜像统一存放在 `ghcr.io/vvv-345/` 命名空间。PostgreSQL、Prometheus 和 Docker Socket Proxy 是运行依赖，不计入这三种应用镜像

| 应用镜像 | 源码仓库 | GHCR 仓库 | 当前源服务器使用的源码版本 |
| --- | --- | --- | --- |
| LiteLLM | [VVV-345/litellm](https://github.com/VVV-345/litellm) | `ghcr.io/vvv-345/litellm` | `9c35172f3b8906b94cfeee666ac7c3571e4e8575` |
| Manager | [VVV-345/litellm](https://github.com/VVV-345/litellm)，构建目录 `account-pool/` | `ghcr.io/vvv-345/account-pool-manager` | 主服务为 `9c35172f3b8906b94cfeee666ac7c3571e4e8575`；独立版本管理 worker 为 `d4b774ce2645f31ff77721335616b3b9245df906` |
| CLIProxyAPI | [VVV-345/CLIProxyAPI](https://github.com/VVV-345/CLIProxyAPI) | `ghcr.io/vvv-345/cliproxyapi` | `bebf587f5a940af676f6e503065140d4991ae1f3` |

Manager 主服务与版本管理 worker **共用 `account-pool-manager` 镜像仓库**，但保留两个运行容器。worker 在回退业务镜像时不能随业务容器一起替换；不创建第四种应用镜像。迁移当前状态时，两者还需分别使用上表所列版本，不能仅因镜像名相同就统一版本

本仓库的 `.github/workflows/publish-deployment-images.yml` 使用 Buildx 构建 `linux/amd64` 和 `linux/arm64`，在同一个提交前缀标签下发布多架构清单。手动运行工作流时用 `source_ref` 指定完整提交，不要用当前分支代替旧版。CLIProxyAPI 仓库有独立的双架构发布流程；当前使用的 `sha-bebf587f5a940af676f6e503065140d4991ae1f3` 标签已包含两种架构

每次发布后执行 `docker buildx imagetools inspect ghcr.io/vvv-345/<镜像>:<标签>`，确认同时列出 `linux/amd64` 和 `linux/arm64`，记录顶层多架构清单 digest。部署时锁定已验证的标签和顶层 digest，不要直接使用 `latest`。能从 GHCR 拉取不代表数据库和配置已经兼容

号池 `192.168.64.1` 的源端快照位于 `/opt/zlia-pool/litellm-migration/`。后续在该目录隔离拉取对应架构的镜像，并恢复两份 PostgreSQL 逻辑导出、号池账号数据卷、日志、配置和密钥。先用独立容器名、数据卷和未占用端口验证数据库结构、认证材料、服务健康和业务接口；日志归档需承认它不是严格同一时刻快照。源服务器持续运行，因此切换前还要安排停写窗口，补齐数据库及可变文件的增量，核对记录后再切流量。保留源端和原站点作为回退，不覆盖 `/opt/zlia-pool/site` 或其他服务
