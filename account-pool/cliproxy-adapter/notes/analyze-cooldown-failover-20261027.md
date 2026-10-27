# CLIProxy 冷却与换号分析（2026-10-27）

本项只读。工作分支 `cursor/capacity-retry-after-8b63`，本地 tip `644c39b1e3`。没有改 `sources.json`，没有整包 rebase，没有推镜像，没有改 Go 或号池调度代码

对照过的材料：

- 本仓 `account-pool/cliproxy-adapter/` 的 `sources.json`、`settings-adaptation.patch`、`apply.py`、`README.md`、`providers.md`
- 钉点 `VVV-345/CLIProxyAPI@b3c954b63df09156ac2ac52ad038b3dc3a20748f`
- 官方 `router-for-me/CLIProxyAPI` 上施工单列出的 SHA，以及上限 tag `v8.0.44` = `1f917e77c4562623aa49e9804d999524151a2f96`
- 号池消费面 `account-pool/account_pool/channels/cliproxyapi/protocol.py` 的 `model_states`

官方 `v8.0.44` 相对钉点为 diverged，ahead 963，behind 8。这 963 个提交只作对照上限，不作为移植集合

## 硬门禁

| 门禁 | 本项核对 |
| --- | --- |
| `sources.json` 钉点 | 仍是 `b3c954b63df09156ac2ac52ad038b3dc3a20748f`。补丁 sha256 仍是 `b1d310d5c106626ffe2177d7f2864fa540c08e1aebceeb6b049ef9e92c190e83`。`official_upstream_observed_head` 仍是 `ceb045a129d43f805e93a5e4aef3a59b108f34f7`（2026-10-23），本项不改 |
| 重试默认 | 号池文档锁 `request-retry=0`、`max-retry-credentials=1`、`streaming.bootstrap-retries=0`。钉点和 `v8.0.44` 的 `config.example.yaml` 示例仍是 `request-retry: 3`、`max-retry-credentials: 0`，`bootstrap-retries` 注释默认 0。跟官方示例会把重试主脑交回 CLIProxy。移植时保持号池生成配置的 0/1/0 |
| managed-identity | 补丁在 `server_management.go` 增加 `GET /v0/management/codex/managed-identity`，响应 `supported=true, version=1`。本簇 SHA 没有改这个文件。整包跟 v8 management 才会威胁这条路由 |
| 号池主脑 | 换卡、续聊、流式不可重放仍在号池和 LiteLLM。本簇能力是让 CLIProxy 少误冷却、少误换号，不把重试次数抬高 |
| 本项动作 | 未改 pin，未 rebase，未推镜像，未跑会改树的 Go 合入。下面的 `go test` 是日后 scratch 验收命令，本项没有执行 |

`apply.py` 要求源码 `HEAD` 等于钉点且工作区干净，然后才打补丁。cherry-pick 之后的 scratch 不能靠修改 `sources.json` 来骗过它。补丁能否应用，用钉点干净副本跑 `apply.py` 作基线，用合入后的副本跑 `git apply --check` 看 hunk，两份都不提交回本仓

## 号池已经依赖 model_states

钉点 `b3c954b` 在 `buildAuthFileEntryLocked` 写入：

```go
entry["model_states"] = modelAvailabilityPayload(auth.ModelStates)
```

`modelAvailabilityPayload` 按模型输出 `unavailable`、`reason`（`upstream_error` 或 `rate_limit`）、可选 `next_retry_after`。测试 `TestModelAvailabilityPayloadSeparatesModelsAndExcludesErrorDetails` 锁死失败模型只有这 3 个字段，不泄漏错误详情。nil 模型被跳过

号池 `model_cooldowns_from_auth` 只在 `unavailable` 且 `next_retry_after` 有值时生成模型冷却。官方 `v8.0.44` 的 `auth_files.go` 没有 `model_states` 这个响应键。直接换成官方文件，号池会把分模型冷却看成空。凭证级 `unavailable` 仍在，模型 A 限流误伤模型 B 的可见性会退回去

钉点已有运行时 `ModelStates`、`CredentialScope` 和 `credential_quota` 分支。缺的是官方后来的隔离、过期调和、快照保留和成功清冷却保护。管理 API 上的 `model_states` 键是 fork 独有，官方到 `v8.0.44` 仍未提供

## 优先、已有、暂缓

| SHA | 能力 | 相对钉点 | 建议 |
| --- | --- | --- | --- |
| `aafa4e9549f7` | 凭证级 429 与模型级 quota 的退避水位分开 | 缺口。钉点会把当前模型的 recover 延长写进凭证和其他模型 | 优先 |
| `b4ff581dafa4` | auth_files 把过期冷却调和成可用，未过期仍不可用 | 缺口。与 `model_states` 同文件 | 优先，手工重放，禁止整文件覆盖 |
| `d5816d5ef694` | 文件快照不覆盖运行时冷却、`ModelStates`、`NextRetryAfter` | 缺口。依赖后面的 `cooldownRecordedSeq` 与 `RefreshFailures` | 优先，排在 35af74 和 58461 之后 |
| `35af74f92721` | 冷却记录之后才发出的成功才能清冷却。早于冷却的 in-flight 成功保留冷却 | 缺口 | 优先 |
| `aa4130e64c84` | `stream_truncated` 不冷却、不换凭证重放。usage 失败码 `stream_truncated` | 缺口 | 优先，放在 conductor_cooldown 叠层末尾 |
| `5dbd92a0e0df` | 客户端取消时，若错误带 RetryAfter，仍记下上游冷却 | 缺口。改了补丁也改的 `codex_websockets_stream.go` | 优先 |
| `21553509662e` | 重登后清 `invalid_grant` 和已过期的非配额不可用，保留仍在窗口内的 429/quota，并清 `RefreshFailures` | 缺口。叠在 d5816 的快照分支上 | 优先 |
| `50d5fe1831fe` | `RejectedAccessToken`。刷新期间不再选刚被 401 拒绝的令牌。刷新成功保留模型 quota | 缺口 | 优先，排在 215535 之前 |
| `bbcd5734f87d` | registry 把 `credential_quota` 当成配额冷却，模型仍列出 | 缺口。与 aafa4e 配套 | 优先 |
| `db1bab011d98` | Claude 明确的每分钟限流，且没有配额窗口头时，不再把短 Retry-After 拉成统一重置窗口 | 缺口。只动 Claude 错误分类 | 优先 |
| `f351924f42cb` | 按凭证关闭 Codex cloaking。属性 `codex_disable_cloaking`，也可在 `CodexKey.disable-codex-cloaking` 覆盖 | 缺口。与 managed 安装字段并存 | 优先。移植时重放补丁，并通知 cockpit |
| `293ff4ac664c` | Codex 请求标识收敛，`codex_fingerprint.go` | 已在钉点祖先。钉点相对它 ahead 7、behind 0 | 已有。只回归 managed 与 fingerprint 的顺序，不重复移植 |
| `58461c8f476a` | disabled 且 `invalid_grant` 停止刷新。enabled 时 1 分钟起、封顶 30 分钟。写入 `RefreshFailures` | 缺口 | 优先，排在 215535 之前 |
| `b12db8c3d8b6` | Gemini 已交付终端帧之后取消，仍发布 usage。提前取消记失败 | 缺口。不在补丁文件里 | 优先 |
| `ede07cafa64a` | Antigravity 与 Gemini interactions 在终端 usage 之后发 `[DONE]` | 缺口。协议完整性 | 优先 |
| `712e4970bc98` | 首个 chunk 前取消走 `RequestCompletionCanceled`，不把提前关闭当成成功 EOF | 缺口 | 优先 |
| `56eee6a4c2f1` | HTTP/2 client conn closed 视为连接生命周期，不冷却。测试用 `SetRetryConfig(0或1, 0, 0)` | 缺口。分类可取，默认重试不可改 | 优先。生产保持 0/1/0 |
| v8 management、SIWC、`ConfigV8`、`auth_files_v8.go` | OAuth v8 与 SIWC 字段 | 官方 `v8.0.44` 已有，钉点没有 | 暂缓 |
| 整包 rebase 到 `1f917e77` / `v8.0.44` | 含上述 963 个提交和 module path `v8` | 与钉点 diverged | 暂缓 |
| 改 pin、推镜像、把重试改离 0/1/0 | 施工单禁止 | 本项未做 | 暂缓 |

## 分项

### A1 `aafa4e9549f7` 凭证级与模型级配额隔离

2026-09-23。只改 `sdk/cliproxy/auth/conductor_cooldown.go` 的 429 分支

钉点在 `CredentialScope` 的 429 上用当前模型的 `BackoffLevel` 和 `NextRecoverAt` 计算下一次时间，再把这个被模型延长的时间写到其他模型和 `auth.Quota`（reason `credential_quota`）。官方改为：凭证级退避从已有的 `credential_quota` 水位起算，否则从 0 起算。写给其他模型和凭证的截止时间用 `credentialNext`，不再吃掉当前模型更长的 recover。当前模型自己仍可保留更长的模型级 recover

冲突低。补丁不碰 conductor。和 `bbcd5734f8` 一起才完整：运行时隔离之后，registry 也不能把 `credential_quota` 当成非配额失败从而把模型从列表拿掉

验收：同一凭证上模型 A 429 之后，模型 B 仍可被选出。管理响应里两个模型的 `model_states.unavailable/reason/next_retry_after` 分开。凭证级 `unavailable` 只在凭证级窗口内为真

### A2 `b4ff581dafa4` 过期冷却在 auth_files 调和

2026-09-19。`auth_files.go` 增加 `isPersistentAuthFailure`、`isModelStateBlocked`、`reconcileAuthFileCooldownState`，`buildAuthFileEntryLocked` 用调和结果填 `status`、`unavailable`、`next_retry_after`。同提交把 `hasUnauthorizedAuthFailure` 导出为 `HasUnauthorizedAuthFailure`（`conductor_cooldown.go` 加 6 行）。测试在新文件 `auth_files_cooldown_test.go`

调和语义：disabled 保持 disabled。无刷新安排的未授权失败、access token 已过期、status message 为 `token expired` 保持不可用。凭证级冷却未过期，或所有可调度模型都仍被挡住，保持不可用。冷却时间已过且没有仍有效的模型冷却，对外变为 active，并清掉过期的 `next_retry_after`。只有部分模型仍在冷却时，凭证整体可用，方便其他模型继续服务

这是高冲突文件，重放步骤在下一节。官方此提交还没有分页，也没有把列表搬进 `auth_files_v8.go`

编译前置，未列入本簇，不要整包带上：`isPersistentAuthFailure` 调用 `AccessTokenExpirationTime`。该方法在官方 `9812b1e76872`（2026-09-02，`fix(auth): validate access token expiration...`）引入。钉点相对 `9812b1e76872` 为 diverged，ahead 8，behind 31，`types.go` 只有 `ExpirationTime`。`9812b1e76872` 还改 scheduler、selector、refresh，超出本簇。手工重放时只补这个过期判断所需的 access token 到期读取，或把调用改到钉点已有的到期信息上。不要把 9812 整提交塞进冷却序列

验收：过期冷却在列表里可用。未过期仍不可用。`model_states` 键还在，字段仍是 unavailable、reason、可选 next_retry_after，失败项长度为 3

### A3 `d5816d5ef694` 快照不覆盖运行时冷却

2026-10-22。文件快照更新（`HasFileSnapshot`）时保留已有 `ModelStates`、`LastRefreshedAt`、未改凭证时的 `NextRefreshAfter` 与 `RefreshFailures`、`Unavailable`、`NextRetryAfter`、quota 冷却字段和 `cooldownRecordedSeq`。观测用的 `ObservedAt` 与 `Signals` 仍取新快照。同提交增加 `InvalidateFileSnapshot`，丢弃的快照会触发 watcher 重读。`service_auth.go` 不再在文件快照路径上提前抄一套刷新时间

补丁改的是 `internal/watcher/synthesizer/helpers.go` 的 `ApplyClaudePolicyAttributes`，本提交不碰该文件。文本冲突低。语义上合成器继续负责 Claude/Kimi 属性，lifecycle 负责把运行时冷却盖回快照。两边都要留

`cooldownRecordedSeq` 来自 `35af74f92721`，`RefreshFailures` 来自 `58461c8f476a`。先合那两个，再合本提交，否则 lifecycle 补丁对不上

验收：热重载或相同文件再次合成之后，内存里的模型冷却和凭证冷却还在。可复述 `file_snapshot_cooldown_seq_test.go` 与 `file_snapshot_traffic_test.go` 的意图：快照 digest 相同或被标成 discarded 时，不把运行时冷却写成文件里的空状态

### A4 `35af74f92721` 进行中的成功不能清冷却

2026-10-15。给每次上游派发 `ExecutionSeq`。记下冷却时写入 `cooldownRecordedSeq`。`MarkResult` 看到成功时，若该次派发序号不大于冷却水印，则保留模型冷却或无 model key 的凭证冷却。凭证级 429 会把同一水印写到凭证和其他模型。进程重启后水印为 0，恢复出来的冷却仍允许被成功清除。`Result.ExecutionSeq == 0` 的直接 `MarkResult` 调用不走这道闸

只动 auth SDK。冲突低到中，因为 `conductor_cooldown.go` 上还有 aafa4e、58461、56eee6、aa4130。按日期叠

验收：冷却已经写上之后，更早发出的成功返回，冷却仍在。冷却之后新发出的成功仍可按原规则清除

### A5 `aa4130e64c84` stream_truncated 不冷却、不换号

2026-10-26。本地合成的「上游已 200 但流在终端帧前断开」实现 `StreamTruncatedError`，结果码 `stream_truncated`，对外仍是 502。`shouldSkipCredentialCooldown` 跳过它。`isRequestInvalidError` 把它当成不可换凭证重放，因为生成已经提交到上游。Gemini、Vertex、OpenAI-compat 的截断错误带上这个标记。usage 失败体写入 `FailureStreamTruncatedCode`

不改 request-retry。号池侧流式不可重放保持原主脑。CLIProxy 这里是停止自己的换凭证，不是把失败交给号池去重放同一段已提交生成

冲突低。executor 文件不在补丁列表里。`conductor_cooldown.go` 与 A1/A4/C4/B3 叠层，放在该文件序列末尾

验收：截断流不写冷却，不自动换凭证。usage 失败码为 `stream_truncated`。号池不把这种 502 当成可安全重放的新请求

### A6 `5dbd92a0e0df` 取消流仍保留已记录的上游冷却

2026-10-10。`conductor_stream.go` 在上下文已取消时，若错误带 RetryAfter，调用 `recordCanceledUpstreamCooldown` 记下冷却。没有 RetryAfter 的普通客户端取消不记。读 bootstrap chunk 时先取已经在通道里的错误，避免取消把同时到达的上游错误丢掉

`codex_websockets_stream.go` 把断连通知挪到错误已经交给 conductor 之后，避免断连取消上下文从而丢掉冷却

补丁在该文件开头给 `ExecuteStream` 加 `managedAuth`，并在身份处理处调用 `ApplyManagedIdentity`。官方 hunk 在约 623 行的上游错误分支。两处不重叠，文本上有机会同时留下。风险在 9 月 17 日到 10 月 10 日之间该文件前部若还有别的漂移，补丁的前部 context 会失败。合入后必须重放 managed 注入，不能用官方文件盖掉补丁

验收：带 RetryAfter 的上游错误之后客户端取消，冷却仍在。普通取消不加冷却。`identity_fingerprint_mode=managed` 时 HTTP、SSE、WS、compact 的安装字段仍注入

### A7 相关簇

`21553509662e`（2026-10-24）改 `conductor_lifecycle.go` 与 `clearUnauthorizedModelStates`。凭证内容变化时把 `RefreshFailures` 归零。只有配额恢复时间已过，才清除 unauthorized 或 `invalid_grant` 造成的不可用。配额窗口仍打开时，清掉授权错误文本，状态消息改成 quota reason，冷却保留。模型级仍在窗口内的 quota 也不被重登重置。依赖 d5816 的 `isFileSnapshot` 分支和 58461 的 invalid_grant 判断

`50d5fe1831fe`（2026-10-06）增加不落盘的 `RejectedAccessToken`。刷新开始时若当前 access token 就是刚失败的那枚，且没有独立到期信息，选择器通过把到期看成 Unix 0 来避开它。刷新成功后清空该字段。`clearUnauthorizedModelStates` 在模型仍有未过期 quota 时只擦 unauthorized 错误，不 `resetModelState`。`metadata_merge` 保留刷新结果里的 rejected token。这是 215535 的前置

`bbcd5734f87d`（2026-10-12）`isQuotaSuspension` 同时承认 `quota` 和 `credential_quota`。可用客户端计算把配额冷却算进暂时不可用，模型仍留在列表里，请求得到 429 和重试时间，而不是未知模型。只动 `model_registry.go`。冲突低到中，和 A1 一起验

`db1bab011d98`（2026-10-18）`ParseClaudeRateLimitResetForError` 只在正文恰好是三条完整的 per-minute `rate_limit_error` 之一、没有任何配额状态头、且 Retry-After 唯一时，去掉未分类的 unified reset，避免几秒的 Retry-After 被拉成数小时冷却。其他输入仍走原来的保守解析。与号池「短 Retry-After 不要放大成长时间冷却」一致。不改号池的 8 秒容量上限，两边各管各的层

### B1 `f351924f42cb` 按凭证关闭 cloaking

2026-09-22。`isCodexCloakingDisabled` 的顺序是：属性 `codex_disable_cloaking`，然后 `CodexKey.disable-codex-cloaking`，然后全局 `codex.disable-codex-cloaking`。`applyCodexCloakingHeaders` 在禁用时直接返回，不再写 cloaking 用的 User-Agent 等头。管理配置列表和 watcher diff 同步该字段

补丁也改 `codex_executor_request.go`，位置在 `PrepareRequest`、`cacheHelper` 和 `applyCodexIdentityConfuseHeaders`。官方 hunk 在 `applyCodexHeadersFromSources` / `applyCodexCloakingHeaders`。文本区域分开，语义冲突高：cloaking 与 managed 都会动安装相关头。README 已定，`identity_confuse` 与 managed 同时开启时安装字段以 managed 为准，缓存映射仍跟原开关。移植后的顺序保持 fingerprint，然后 managed 覆盖安装字段。cloaking 关闭只停止 cloaking 自己的头，不能剥掉 managed 写下的 `installation_id`

`providers.md` 的 Codex 行已经写了 `disable-codex-cloaking`。本提交是把同一开关下沉到凭证，没有改九个供应商的通道划分。若管理面要暴露新属性，交叉通知 cockpit 钉点 `76fbea02ba8ab5a071205a0bfa59ef4db5cc64b4`。本项不改身份字段，也不改 cockpit

验收：managed 开着时，关闭 cloaking 不会去掉安装字段。全局 cloaking 仍开、单凭证属性为 true 时，该凭证不套 cloaking 头。补丁测试里 User-Agent 与加密历史不被 managed 改写的断言仍然成立

### B2 `293ff4ac664c` 请求标识收敛

2026-09-13，在钉点之前。钉点历史含 `codex_fingerprint.go` 与 `helps/codex_fingerprint.go`。补丁在 `applyCodexFingerprintBody` 之后调用 `ApplyManagedIdentity`。这是已有能力上的回归，不是新缺口。不要把该提交再 cherry-pick 一遍

回归：managed 开时安装字段以 managed 为准。fingerprint 的 device/session/full 在 managed 关闭时保持旧行为

### B3 `58461c8f476a` invalid_grant 退避

2026-09-25。disabled 且 last error 为 invalid_grant：`shouldRefresh` 返回 false，自动刷新循环不再排期，`refreshAuthForRequest` 直接拒绝，`NextRefreshAfter` 清空，`RefreshFailures` 归零，状态消息 `disabled (invalid grant)`。enabled 且 invalid_grant：`RefreshFailures` 递增，退避 `1m * 2^(n-1)`，超过 10 次移位仍按 10 次，封顶 30 分钟。access token 仍有效时保留现有冷却，只把下次刷新推后。识别 invalid_grant 时允许 HTTP 状态 0，避免包在无状态错误里被漏掉

冲突低，但 `conductor_refresh.go` 与 `conductor_lifecycle.go` 要先于 50d5fe 和 215535。215535 负责重登新令牌后把退避旋停

验收：disabled 加 invalid_grant 不再调用刷新。enabled 的间隔符合 1 分钟到 30 分钟。新的登录材料进来后，`RefreshFailures` 归零，凭证回到轮换，同时仍打开的 quota 窗口不被清掉

## 流式与 usage

`b12db8c3d8b6`：Gemini 流在发出终端帧（`[DONE]`、response.completed/incomplete/done、message_stop、finishReason）之后，取消不再把 usage 标成失败。尚未见到终端帧就取消或扫描失败，则 `PublishFailure`。Vertex 走同一辅助判断。文件不在补丁里。验收对着号池计费：终端后取消仍有 token 用量，提前取消不是成功耗尽

`ede07cafa64a`：interactions 翻译把 usage 留到 finish 当时或之后再发 completed，并附 `[DONE]`。提前缓存的 usage 只作为显式 `[DONE]` 的回退，不提前结束流。干净 EOF 且没有 finishReason 时，Antigravity 工具流仍补终端帧。读错误之后不把截断流翻译成成功。与 `providers.md` 的 Gemini Interactions 开关无命名冲突，属于协议补齐

`712e4970bc98`：首包前的读取改走 `nextStreamChunk`。取消把 `streamCanceledBeforeRead` 设上，随后 `completionOutcome = RequestCompletionCanceled`。通道在首包前关闭仍是 closed-before-read，不把取消报成成功 EOF。号池不要把这个取消收成一次成功耗尽

`56eee6a4c2f1`：`http2: client connection force closed via clientconn.close` 与 `http2: client conn is closed` 进入 `isConnectionLifecycleError`（不冷却），也进入 transient transport 文案（仅当 request-retry 大于 0 才有额外轮次）。官方测试 `TestManager_HTTP2ClosedConnectionRespectsRequestRetry` 对 retries 0 和 1 都跑。取「不冷却」。生成配置保持 `request-retry=0`、`max-retry-credentials=1`、`streaming.bootstrap-retries=0`。不要把 example 里的 3 和 0 抄进来，也不要为了让 HTTP/2 重试把默认改成 1

## 九供应商开关

本簇没有改 `providers.md` 里九行的通道划分。Codex 的 `disable-codex-cloaking` 从全局下沉到凭证，名字还在。Claude 的 fingerprint_profile、cloak_mode、rebuild_mid_system_message 不在这些提交的改名范围内。db1bab 只改 Claude 429 的 Retry-After 选择。Antigravity、Kimi、xAI、Gemini、Gemini Interactions、Vertex、OpenAI-compatible 的配置入口没有被这些 SHA 改掉。interactions 的 `[DONE]` 是翻译行为，不是新开关

## 在官方 auth_files 之上重放 model_states

目标：吸收 `b4ff581dafa4` 的过期冷却调和之后，钉点这两处还在

```go
entry["model_states"] = modelAvailabilityPayload(auth.ModelStates)
```

以及 `modelAvailabilityPayload` 和 `TestModelAvailabilityPayloadSeparatesModelsAndExcludesErrorDetails`

### 官方文件现在长什么样

钉点 `buildAuthFileEntryLocked(auth *coreauth.Auth)`，带 `authFileEntryMu`。`model_states` 写在 `quota` 与 `model_quotas` 之间。`status`、`unavailable`、`next_retry_after` 直接来自 auth

`b4ff581dafa4` 的函数签名已是 `(auth, quotaSupported ...map[string]struct{})`，没有互斥锁包装，没有 `model_states`。调和函数插在 `buildAuthFileEntryLocked` 之前。entry 字面量改用调和后的 status 与 unavailable，`next_retry_after` 改用调和后的时间。此提交没有分页

`v8.0.44` 的 `1f917e77` 上，列表和调和仍在 `internal/api/handlers/management/auth_files.go`，不在 `auth_files_v8.go`。`auth_files_v8.go` 是 `StartOAuthV8` 与 `ImportOAuthV8`。`auth_files_crud.go` 的删除和上传路径调用 `beginSIWCFileMutation`。这些是暂缓面

v8 的 `buildAuthFileEntryLocked(auth, quota authFileQuotaContext)` 仍调用同一套 `reconcileAuthFileCooldownState`。`quota` 与 `model_quotas` 之间多了 plugin quota 和 `quota_probe`。`model_states` 不存在。仅当 `quota.v8` 且 provider 为 codex 时才写 `siwc_*`。`ListAuthFiles` 在查询参数带 `page` 或 `page_size` 时分页，缺省仍返回 `observed_at` 与 `files`，并额外填 `cooldowns` 快照。号池模型 `extra=ignore`，多出来的 siwc 和 cooldowns 不会撑破解析。缺 `model_states` 会静默丢掉分模型冷却。分页缺省不改变「files 里是全量」这一点，所以分页本身不是 v8 专属合同。它仍然是后加的列表形状，本项不取

补丁新文件的 import 是 `github.com/router-for-me/CLIProxyAPI/v7/...`。v8 树是 `v8`。整包换到 `v8.0.44` 之后，现有补丁哈希无法原样应用

结论：可以在没有 v8 的前提下落地。落点是钉点的 `auth_files.go`，手工放入 b4ff 的调和，并留住 `model_states`。不要用 `v8.0.44` 的 `auth_files.go` 覆盖。列表没有被搬进 v8-only 文件，因此不需要按施工单第 4 步停止整个文件。需要停止的是 SIWC、`ConfigV8`、分页响应和 `cooldowns` 快照

### 冲突 hunk 预判

以钉点 `auth_files.go` 为底，打官方 `b4ff581dafa4` 的补丁，预期三处：

1. 新函数 hunk 会失败。官方 context 是无锁的 `buildAuthFileEntry(auth, quotaSupported ...)`。钉点是 `authFileEntryMu` 加上 `buildAuthFileEntry(auth)`。把三个函数手工贴到 `buildAuthFileEntryLocked` 之前。调用保持钉点签名，调和函数不需要 `quotaSupported`
2. entry 字面量 hunk 有条件能合。钉点 `model_states` 提交没有改 `status`、`status_message`、`unavailable` 那三行。把这三行换成调和结果，并在字面量前增加 `reconcileAuthFileCooldownState(auth, time.Now().UTC())`。`model_states` 那一行留在 `entry["quota"] = ...` 之后、`model_quotas` 之前
3. `next_retry_after` hunk 的前后文（`LastRefreshedAt` 判断）钉点与官方 b4ff 的旧侧一致，预期能合。改成只在调和后的时间非零时写出。不要改回 `auth.NextRetryAfter`，否则过期时间会重新暴露

若有人以后改看 v8 文件，第 2 处的插入点会被 plugin quota 块挤开。重放位置仍是 quota 赋值的下一行，不要放进 `if quota.v8` 的 SIWC 块

`AccessTokenExpirationTime` 缺失会使第 1 处贴进去之后编不过。处理见 A2 的 9812 说明。`HasUnauthorizedAuthFailure` 用 b4ff 那 6 行导出，钉点已有未导出的 `hasUnauthorizedAuthFailure`。`ModelState.Status` 钉点已有，`isModelStateBlocked` 可以用

`settings-adaptation.patch` 不改 `auth_files.go`。它改 `auth_files_fields.go`。本簇 SHA 的文件列表没有 `auth_files_fields.go`。auth_files 重放与补丁在不同文件，补丁不会因为多了 `model_states` 而拒绝。补丁会因为 `HEAD` 不是钉点而被 `apply.py` 拒绝，所以用 `git apply --check` 对补丁文件做检查

### scratch 顺序（不提交，也不改本仓 pin）

1. 两份丢弃副本。一份停在 `b3c954b63df09156ac2ac52ad038b3dc3a20748f`，干净时跑 `python account-pool/cliproxy-adapter/apply.py SOURCE_DIR`，确认补丁基线仍干净。另一份也从钉点开始，不要 checkout `v8.0.44`
2. 在第二份手工合入 `b4ff581dafa4` 的三个调和函数、entry 三字段、`next_retry_after`，以及 `conductor_cooldown.go` 的 `HasUnauthorizedAuthFailure` 导出。补上 access token 到期读取，使 `isPersistentAuthFailure` 能编译。不要带走 `quotaSupported` 签名，不要添加分页、`cooldowns` 快照、SIWC
3. 确认 `entry["model_states"] = modelAvailabilityPayload(auth.ModelStates)` 和函数本体仍在，测试仍断言 `len(failed)==3`
4. 若发现必须改 `auth_files_v8.go`、`ConfigV8` 或 SIWC 才能让列表编过，停止该文件，退回「只在钉点列表入口加 model_states」的窄补丁。按本次读到的 b4ff 与 v8 布局，b4ff 层不需要这些文件
5. 在第二份运行，工作目录是 CLIProxy 源码树：

```sh
go test ./internal/api/handlers/management -run 'TestModelAvailability|Cooldown|AuthFile' -count=1
```

6. 在第二份对本仓补丁执行 `git apply --check --whitespace=error`。失败则记下 hunk，不要改补丁哈希来迁就。第一份已经证明钉点本身能被 `apply.py` 接受
7. 补丁能检查通过之后，再在已打补丁的树上跑 README 的回归，并加上模型可用性与冷却：

```sh
go test ./internal/runtime/executor/helps ./internal/runtime/executor \
  ./internal/api/handlers/management ./internal/watcher/synthesizer ./internal/api \
  -run 'Test(Managed|ManagementManagedIdentity|PatchAuthFileFields_ClaudePolicyAttributesFollowMetadata|FileSynthesizer_ClaudePolicyAttributes|Codex.*(Identity|Fingerprint|Cache|Websocket)|ModelAvailability|Cooldown)' -count=1
go build -o cliproxy-adapted ./cmd/server
```

8. 产出留在 scratch。本仓只保留本笔记。通过与否要等用户批准 follow-up 之后才算验收，本项没有跑这些命令

语义锁不要改：分模型。`unavailable`。`reason` 只取 `upstream_error` 或 `rate_limit`。可选 `next_retry_after`。不输出错误详情。测试锁 `len(failed)==3`

## 日后若批准的移植顺序

按依赖，不按 v8.0.44 快进。每一提交合完再跑该包测试。auth_files 只走上一节的手工重放，不 `git checkout` 官方整文件

1. `b4ff581dafa4` 的调和与 `HasUnauthorizedAuthFailure` 导出，加上 `model_states` 重放，以及 access token 到期读取的窄补齐
2. `aafa4e9549f7` 凭证与模型退避隔离
3. `58461c8f476a` invalid_grant 停刷与指数退避
4. `50d5fe1831fe` `RejectedAccessToken`，刷新期间避开拒绝令牌，保留模型 quota
5. `5dbd92a0e0df` 取消流保留带 RetryAfter 的冷却。然后 `git apply --check` 补丁，确认 `codex_websockets_stream.go` 的 managed 前部还在
6. `bbcd5734f87d` registry 把 `credential_quota` 仍视为配额冷却并列出模型
7. `56eee6a4c2f1` HTTP/2 关闭不冷却。核对生成配置仍是 0/1/0
8. `35af74f92721` `ExecutionSeq` / `cooldownRecordedSeq`
9. `db1bab011d98` Claude 每分钟限流的短 Retry-After。可与 6 到 8 并行，文件不重叠
10. `d5816d5ef694` 文件快照保留运行时冷却
11. `21553509662e` 重登恢复，保留仍有效的 quota
12. `b12db8c3d8b6`、`ede07cafa64a`、`712e4970bc98` 流式 usage 与首包前取消。文件与冷却 SDK 基本分开，放在 conductor 稳定之后即可
13. `aa4130e64c84` `stream_truncated`，放在 `conductor_cooldown.go` 其他 hunk 之后
14. `f351924f42cb` 按凭证关闭 cloaking。最后重放 `settings-adaptation.patch`。managed 安装字段优先于 cloaking。若管理 API 要写出 `codex_disable_cloaking`，先通知 cockpit。`293ff4ac664c` 不移植

暂缓，即使上面全部通过也不要带：

- `auth_files_v8.go`、`ConfigV8`、SIWC、`beginSIWCFileMutation`
- v8 列表分页与 `entry["cooldowns"]`
- 整仓 rebase 到 `router-for-me/CLIProxyAPI` main 或 `v8.0.44`
- 修改 `sources.json` 钉点、补丁 sha256、`official_upstream_observed_head`
- 推镜像、改部署认证卷
- 把 CLIProxy 重试默认改离 0/1/0
- 覆盖 `helps/managed_identity*.go` 或放宽 managed 门禁
- 整包 `9812b1e76872` 的 scheduler 与 selector 行为。只要到期读取能让 b4ff 调和编译即可，其余另开批准

## 冲突文件总表

范围限于本施工单列出的 SHA，加上钉点 `model_states` 与现有补丁。v8 整包另算，不并进「现在就能合」的冲突数

| 文件 | 冲突来源 | 风险 |
| --- | --- | --- |
| `internal/api/handlers/management/auth_files.go` | 钉点 `model_states` 与 `b4ff581dafa4` 同函数。钉点有锁和单参数签名，官方 b4ff 无锁且多了 variadic。v8 又改成 `authFileQuotaContext`，并在 quota 与 model_quotas 之间插入 plugin 块 | 高。手工重放可避开 v8 |
| `internal/api/handlers/management/auth_files_v8.go` 与 `auth_files_crud.go` | OAuth v8 与 SIWC 文件锁。列表入口不在这里 | 高，暂缓面。本簇冷却不依赖它们 |
| `sdk/cliproxy/auth/types.go` | 钉点缺少 `AccessTokenExpirationTime`（`9812b1e76872`，未列入本簇）。后续还有 `RejectedAccessToken`、`RefreshFailures`、`cooldownRecordedSeq` | 高，但是窄的。先补到期读取，再按 50d5fe、58461、35af74 的顺序加字段 |
| `internal/runtime/executor/codex_executor_request.go` | 补丁的 managed 注入与 `f351924f42cb` 的 cloaking。hunk 区域分开，安装字段谁后写谁生效 | 高（语义）。文本上不要整文件覆盖 |
| `internal/runtime/executor/codex_websockets_stream.go` | 补丁前部 managed 与 `5dbd92a0e0df` 后部断连顺序 | 中高。hunk 不重叠，前部 context 漂移时补丁会失败 |
| `internal/runtime/executor/codex_executor_stream.go`、`codex_executor_execute.go`、`codex_websockets_execute.go` | 本簇 SHA 不改这些文件。风险来自补丁本身必须继续应用 | 中。相对本簇无新 hunk，整包 rebase 才升高 |
| `internal/runtime/executor/helps/managed_identity*.go` | 补丁新增。本簇不覆盖。v8 module path 会让 import 对不上 | 门禁。禁止删除或放宽 |
| `internal/api/server_management.go` | 补丁注册 managed-identity v1。本簇 18 个 SHA 都不改该文件 | 当前簇内低。跟 v8 management 路由重排则升到中高 |
| `internal/watcher/synthesizer/helpers.go` | 补丁同步 Claude 策略属性。`d5816d5ef694` 不改该文件，改 lifecycle 快照 | 文本低，语义中。两边都留 |
| `sdk/cliproxy/auth/conductor_lifecycle.go` | `50d5fe1831fe`、`d5816d5ef694`、`21553509662e` 叠在同一更新函数 | 中。必须按这个顺序 |
| `sdk/cliproxy/auth/conductor_cooldown.go` | `b4ff581dafa4` 的 6 行导出，加上 aafa4e、58461、35af74、56eee6、aa4130 | 中。按日期，aa4130 最后 |
| `internal/registry/model_registry.go` | 仅 `bbcd5734f87d` | 低到中 |
| `internal/api/handlers/management/auth_files_fields.go` | 仅补丁。本簇不改 | 低 |

核心高风险是 4 个文件：`auth_files.go`、`types.go` 的到期与后续字段、`codex_executor_request.go`、`codex_websockets_stream.go`。`server_management.go` 在本簇内不是冲突点，它是 managed v1 门禁，碰到 v8 management 才变成高风险。另有 v8 专属的 `auth_files_v8.go` 整组暂缓

## 本项没有做的事

没有改 `sources.json`、补丁、README、`providers.md`。没有 checkout 官方默认分支，没有推镜像，没有改号池 Python 调度。没有在 scratch 里跑 `go test`，所以上面的「能合上」是对 b4ff、钉点和 `v8.0.44` 文件的阅读结论，不是一次已通过的编译。用户批准 follow-up 之后，再按上一节的命令验收
