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

镜像标签由钉点和补丁哈希决定，当前是 `ghcr.io/vvv-345/cliproxyapi:managed-b3c954b63df0-b1d310d5c106`。`python account-pool/cliproxy-adapter/image_ref.py` 会打印同一引用。`.github/workflows/publish-cliproxy-adapted-image.yml` 在干净钉点上应用补丁，分别构建 `linux/amd64` 和 `linux/arm64`，探测通过后才把这个标签做成多架构清单。它不发布 `latest`，也不改 `sha-bebf587f5a940af676f6e503065140d4991ae1f3`

号池默认 `ACCOUNT_POOL_CLI_PROXY_IMAGE` 已指向该标签。已经在跑的卡片仍使用创建时写入的镜像，所以只改仓库默认值不会让旧容器突然支持托管安装 ID。发布并拉取该标签后，重启 Manager：启动时若卡片 compose 里的镜像和当前配置不同，会保留认证卷并重建容器，然后按已保存策略再同步一次。官方 `eceasy/cli-proxy-api` 和未打补丁的 `sha-bebf587f5a940af676f6e503065140d4991ae1f3` 没有这条路由，保存 managed 仍会返回「当前 CLIProxyAPI 不支持托管安装 ID，请先升级适配底层；配置已保存但尚未生效」

容器镜像的验收与二进制相同，把 `--binary` 换成 `--image ghcr.io/vvv-345/cliproxyapi:managed-b3c954b63df0-b1d310d5c106`。产品重试仍是 `request-retry: 0`、`max-retry-credentials: 1`，不要改成上游示例的 3 和 0

安装档案位于认证目录的 `.codex-managed-identity/<scope>.json`，备份和迁移认证卷时一并保留；删掉它会重新生成安装身份，不会改变供应商账号身份

## 生效边界

Codex 新增 `identity_fingerprint_mode=managed`，默认仍为 off。当前钉点上的 device、session、full 仍会改写请求标识：device 改安装标识，session 再改会话和对话标识，full 再改缓存、线程、回合、窗口和请求标识。新卡用 managed。managed 为每个认证文件及账号主体独立持久化 UUIDv4，令牌刷新、代理变化和进程重启不更换它。不同账号、API key 或上游地址使用不同范围，坏档案或无法确定 OAuth 主体时拒绝请求，不悄悄生成替代身份

HTTP、SSE、compact、WebSocket 和通用请求入口复用同一身份处理。只改已知安装字段，不扫描业务对象、工具或密文历史。standalone API-key 图片和音频请求不注入 Codex 安装字段。`identity_confuse` 继续使用已有显式开关，同时开启时安装字段以 managed 为准，缓存映射仍遵循原开关

Manager 在任何运行时写操作前检查受管理鉴权保护的 `GET /v0/management/codex/managed-identity`，严格要求 `supported=true, version=1`。旧镜像保存 managed 会明确报告未生效，不假装支持。已有卡片不会自动迁移或开启

Claude/Kimi 的 fingerprint_profile 在热更新、恢复 inherit 和文件重载时同步到执行器读取的属性。Claude 的 rebuild_mid_system_message 同样同步 true/false。九供应商的实际配置入口见 [供应商映射](providers.md)，没有给不支持的协议虚构同名开关

换卡、续聊和流式不可重放仍由原号池与 LiteLLM 执行。此改动不提供跨账号解密，不承诺消除供应商风控。本地模拟上游测试不等同于真实供应商、真实额度和跨账号旧密文续聊验收
