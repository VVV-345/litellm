# 卡片设置底层适配

本目录只保存固定源码版本、来源、窄补丁和应用入口，不保存另一份账号池引擎。保留现有 LiteLLM 入口、选卡、鉴权、预算、计费和重试，当前默认镜像没有改变

`sources.json` 记录本轮从 GitHub 获取后固定的提交。后续不要用移动的 latest 构建标签替代提交号；更新版本要重新验证补丁与兼容性。许可和复用文件见 [NOTICE.md](NOTICE.md)，本次仅用于个人非商业自用

## 应用和验证

先在可丢弃目录取得 sources.json 指定的 CLIProxyAPI 提交，再运行 `python account-pool/cliproxy-adapter/apply.py SOURCE_DIR`。入口检查固定提交、干净状态和补丁哈希，不下载、启动服务或替换镜像。重复应用会因目录已有改动被拒绝

在补丁后的源码目录运行以下命令，使用该版本 go.mod 要求的 Go 工具链

```sh
go test ./internal/runtime/executor/helps ./internal/runtime/executor ./internal/api/handlers/management ./internal/watcher/synthesizer ./internal/api -run 'Test(Managed|ManagementManagedIdentity|PatchAuthFileFields_ClaudePolicyAttributesFollowMetadata|FileSynthesizer_ClaudePolicyAttributes|Codex.*(Identity|Fingerprint|Cache|Websocket))' -count=1
go build -o cliproxy-adapted ./cmd/server
python account-pool/cliproxy-adapter/probe_managed_identity.py --binary ./cliproxy-adapted
```

最后一条会在本机启动该二进制。未带管理密钥时 `GET /v0/management/codex/managed-identity` 返回 401，带 `X-Management-Key` 时返回 `{"supported":true,"version":1}`

源码钉点和补丁哈希仍由 `python account-pool/cliproxy-adapter/image_ref.py` 校验。v1.9 起 `.github/workflows/publish-cliproxy-adapted-image.yml` 转交统一四组件发布，在干净钉点上应用补丁，分别构建并探测 `linux/amd64` 和 `linux/arm64`。运行时使用配套清单中 `ghcr.io/vvv-345/litellm-cliproxyapi` 的 CLIProxyAPI 组件 digest，不覆盖旧包、旧标签或 `latest`

已经在跑的卡片仍使用创建时写入的镜像。Manager 重启只恢复连接和配置同步，不更换卡片镜像、不自动重建账号。官方 `eceasy/cli-proxy-api` 和未打补丁的 `sha-bebf587f5a940af676f6e503065140d4991ae1f3` 没有这条路由，保存 managed 仍会返回「当前 CLIProxyAPI 不支持托管安装 ID，请先升级适配底层；配置已保存但尚未生效」

容器镜像的验收与二进制相同，把 `--binary` 换成 `--image IMAGE_REF`。探测等待真实 HTTP 响应，不把 Docker 发布端口的 TCP 监听视为应用就绪。仅对连接重置、拒绝或超时做有界就绪重试；HTTP 返回后仍必须通过未授权 401 和授权后 `supported=true, version=1` 两道门禁。产品重试仍是 `request-retry: 0`、`max-retry-credentials: 1`，不要改成上游示例的 3 和 0

安装档案位于认证目录的 `.codex-managed-identity/<scope>.json`，备份和迁移认证卷时一并保留；删掉它会重新生成安装身份，不会改变供应商账号身份

## 受控单卡更新

先按 `deploy/UPDATE_RUNBOOK.md` 备份数据库、该卡 Compose 和完整认证卷，记录旧镜像 digest 与容器运行状态。在页面停用目标卡，等配置同步完成、在途请求结束。从已成功发布的配套清单选取 CLIProxyAPI 组件 digest，不能用其他组件、移动标签或未经验证的镜像

在持有现有 Manager 环境变量、数据目录和专用 Docker Socket Proxy 访问权限的 Manager 容器内显式执行，`CARD_UUID` 是卡片不可变 ID，`IMAGE_DIGEST` 是上述组件的 64 位 SHA-256

```sh
python -m account_pool.runtime_maintenance CARD_UUID --image ghcr.io/vvv-345/litellm-cliproxyapi@sha256:IMAGE_DIGEST
```

命令只处理一张已停用且配置收敛的 CLIProxyAPI 卡。整个操作持有与日常配置相同的跨进程操作锁和环境行锁，有活动租约就拒绝，不等待后直接强制重建。只替换既有 Compose 的 image 字段，不重新渲染其他设置，不播种、不删除认证卷或安装档案。先拉取固定 digest，再短暂启动完成 401 和严格能力检查，最后保留原来的运行或停止状态；数据库 enabled 和认证停用标志不改变。原本运行的停用卡仍可通过原页面重新启用；原本停止的卡保持停止，须由运维显式启动后再操作页面

拉取失败不替换容器；替换或探测失败会尝试恢复旧 Compose、旧镜像及原运行状态。失败时保持卡片停用，人工检查容器和备份，不自动重新启用，也不声称补偿一定成功。成功后再按原页面流程刷新卡片并同步已保存策略，确认托管身份生效后由用户决定是否启用。该命令不会自动部署整套服务或迁移其他卡片

## 生效边界

Codex 新增 `identity_fingerprint_mode=managed`，默认仍为 off。当前钉点上的 device、session、full 仍会改写请求标识：device 改安装标识，session 再改会话和对话标识，full 再改缓存、线程、回合、窗口和请求标识。新卡用 managed。managed 为每个认证文件及账号主体独立持久化 UUIDv4，令牌刷新、代理变化和进程重启不更换它。不同账号、API key 或上游地址使用不同范围，坏档案或无法确定 OAuth 主体时拒绝请求，不悄悄生成替代身份

HTTP、SSE、compact、WebSocket 和通用请求入口复用同一身份处理。只改已知安装字段，不扫描业务对象、工具或密文历史。standalone API-key 图片和音频请求不注入 Codex 安装字段。`identity_confuse` 继续使用已有显式开关，同时开启时安装字段以 managed 为准，缓存映射仍遵循原开关

Manager 在任何运行时写操作前检查受管理鉴权保护的 `GET /v0/management/codex/managed-identity`，严格要求 `supported=true, version=1`。旧镜像保存 managed 会明确报告未生效，不假装支持。已有卡片不会自动迁移或开启

Claude/Kimi 的 fingerprint_profile 在热更新、恢复 inherit 和文件重载时同步到执行器读取的属性。Claude 的 rebuild_mid_system_message 同样同步 true/false。九供应商的实际配置入口见 [供应商映射](providers.md)，没有给不支持的协议虚构同名开关

换卡、续聊和流式不可重放仍由原号池与 LiteLLM 执行。此改动不提供跨账号解密，不承诺消除供应商风控。本地模拟上游测试不等同于真实供应商、真实额度和跨账号旧密文续聊验收
