# 卡片设置底层适配

本目录只保存固定源码版本、来源、窄补丁和应用入口，不保存另一份账号池引擎。保留现有 LiteLLM 入口、选卡、鉴权、预算、计费和重试。默认构建引用随补丁哈希更新，现有卡片不会自动换镜像

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

## 单卡额度验证与恢复

新增受管理鉴权保护的 `GET /v0/management/codex/quota-verification`，能力响应必须为 `supported=true, version=1, token_refresh=false`。同路径 POST 只接受认证文件名、auth_index、已启用模型、当前 access token 的 SHA-256 和显式 recover 标志。Manager 对旧镜像或不匹配能力返回 `runtime_upgrade_required`，不会回退到普通探测或刷新 Token

验证固定发送最小 OK 提示词，复用该卡 Codex HTTP 执行器，不刷新、不重试、不切卡。必须收到完整 `response.completed` 且正文为 OK 才算通过；200、额度增加、空流、错误帧或超限响应都不表示恢复。仅在用户选择恢复、凭据和状态快照未变化时清理已验证的认证或额度阻断，保留手动禁用、未知错误和其他模型阻断，只持久化 cooldown store，不改写认证文件

Manager 的兑换记录持久化原兑换 ID，结果不明确时只能核对同一次兑换。选择最早到期的适用券；兑换成功与推理验证、号池恢复分别显示。CPA 成功后还要通过 Manager 数据库快照、在途租约和独立运行时冷却检查，才报告恢复。本地测试使用模拟供应商和专用 PostgreSQL，不代表线上账号已经恢复

```sh
go test ./sdk/cliproxy/auth ./internal/api/handlers/management ./internal/api -run 'Test(QuotaRecovery|ManagementManagedIdentityCapabilityRequiresAuth)' -count=1
```

新能力需要发布配套镜像并按下文受控流程更新目标卡之后才生效。发布架构仍由现有工作流参数选择，未固定为单架构；本地 commit 不会发布、部署或再消费线上重置券

## Access-token 降级绑定修复的发布和验收

文件加载器允许认证记录的 `FileName` 为空，此时认证文件列表使用 `ID` 作为 `name`。降级验证、额度验证和显式续期入口复用已有的 `matchesAuthFileLookup`，按名称或 ID 加相同 `auth_index` 查找，仍要求 Codex 供应商和当前 token 指纹。不会取消认证、手动停用、独立额度或模型冷却限制，也不会因绑定匹配就报告验证成功

本地回归覆盖文件加载、已有文件名和 ID 查找、错误名称、串用另一凭据的索引、错误供应商、缺失字段及旧 token 指纹。降级测试从文件加载真实认证结构，通过 HTTP 管理入口调用真实 Codex 执行器，再走正常调度；供应商由本地测试服务器代替，断言没有额外 OAuth 刷新且认证文件字节不变。这不代表线上两张卡已经恢复

先审查本地 commit，明确同意 push 后才推送 `CLIProxyAPI分支`。该分支的 push 会自动触发 `Publish deployment images`，构建四组件；工作流本身不执行服务器部署。服务器使用 arm64，确认工作流选择包含 arm64；如需手动指定，用 `publish-deployment-images.yml` 的 `source_ref` 指向审查通过的完整 commit。不要为同一次发布重复触发两个工作流

等发布成功，从配套发布清单取得 **CLIProxyAPI** 组件的固定 digest，核对平台、完整产品 commit、源码钉点和补丁 SHA256。不能拿 Manager digest、旧标签或 `latest` 替代。此修复只需要显式更新目标卡的 CPA，重启 Manager、更新 release-worker 或拉取镜像都不会替换已有卡片

确认目标卡配置和策略已同步，备份数据库、该卡 Compose、完整认证卷及安装身份，在现有 Manager 容器内逐卡执行。`PRODUCT_COMMIT` 是发布的完整产品 commit，`CPA_DIGEST` 是上述组件 digest，`CARD_UUID` 是当前目标卡的不可变 ID；不要并行更新两张卡

```sh
python -m account_pool.runtime_rollout --release "$PRODUCT_COMMIT" \
  --image "ghcr.io/vvv-345/litellm-cliproxyapi@sha256:$CPA_DIGEST" --card "$CARD_UUID"
```

该入口排空在途请求、持久化维护检查点并备份原运行时，保留原启停设置、认证卷和凭据绑定。每张卡都要达到 `phase=verified`、`image_verified=true`、`credential_state=preserved`。失败时先读取同一 release/card 的 `--status` 检查点，不换 release ID 绕过门禁，不重放旧认证卷；检查点中的 `model_inference=not_run` 也不代表模型验证成功

重新确认目标卡已启用、策略为 `synced`，通过 Manager 已有的降级验证接口为该卡实际启用的模型做最小调用。`MANAGER_TOKEN` 只从现有安全配置注入，禁用命令回显，不填写 refresh token

```sh
curl --fail-with-body -sS -X POST "$MANAGER_URL/api/environments/$CARD_UUID/access-token-fallback/verify" \
  -H "Authorization: Bearer $MANAGER_TOKEN" -H 'Content-Type: application/json' \
  --data "{\"model\":\"$ENABLED_MODEL\",\"confirmed\":true}"
```

只有 `passed=true`、`restored=true`、`reason=temporary_access`，状态显示对应模型已验证、令牌仍有效且正常网关调用成功，才能认定降级可用。能力接口 200、用量接口 200、额度为 100% 或卡片单独变为 ready 都不够。若存在独立冷却、停用、额度限制或 access token 已失效，仍应拒绝；本修复不强制恢复这些卡片。降级只在 access token 有效期内临时可用，不修复已失效的 refresh token，到期后仍需重新授权

## OAuth 续期保存保护

继续使用 CPA 原有临期刷新调度，不新增 Manager/LiteLLM 定时器，也不修改默认提前量。自动刷新、显式刷新和 401 恢复共用单认证锁；等待期间重新检查凭据，管理接口携带失败请求使用的 Token，避免旧请求再次轮换

刷新成功但保存失败会返回 `refreshed credentials retained in memory; persistence pending`，保留运行中已轮换的凭据。原调度器按现有失败退避补保存，也可由刷新入口补保存，不再次调用 OAuth。调用方在轮换完成后取消，不会取消随后的 15 秒保存上下文。文件保存使用同目录临时文件、同步和替换，写入失败不截断旧文件

新导入、重新注册或删除不会被旧刷新结果覆盖；并发停用保留停用状态和已轮换凭据，其他模型额度与新的认证错误不因补保存被清除。热加载旧文件不会把刚消费过的凭据写回内存。以上协调限于单 CPA 进程，同一认证文件必须只有一个续期写入者，不能同时让商家、其他实例或另一套工具刷新同一份令牌

这不能恢复 `refresh_token_reused` 的旧令牌，也不保证永久续期。所有持久化都失败时，如果进程退出，内存里的新令牌仍可能丢失。修复需要发布并受控更新目标卡的 CPA 镜像后才生效，不能以本地模拟测试代替新有效凭据的真实验收

```sh
go test ./sdk/auth ./sdk/cliproxy/auth ./internal/api/handlers/management -run 'Test(FileTokenStoreAtomic|RefreshPersistence|ExecuteAPICallReusesRefresh)' -count=1
```

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
