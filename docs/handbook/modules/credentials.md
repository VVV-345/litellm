# 凭据、OAuth、受控浏览器与上号

## 职责与边界

本模块负责凭据与卡片的一对一归属、OAuth 会话和回调、认证文件导入、可选浏览器 worker 及手动刷新协调。不会凭空续期被撤销的 Token，也不以模型列表或保存成功代替推理验收

## 关键入口

[授权操作](../../../account-pool/account_pool/application/environments/authorization.py)、[认证文件操作](../../../account-pool/account_pool/application/environments/auth_files.py)、[CredentialOwnership](../../../account-pool/account_pool/credential_ownership.py)、[手动刷新](../../../account-pool/account_pool/credential_refresh.py)、[访问令牌降级](../../../account-pool/account_pool/access_token_fallback.py)、[OAuth 会话](../../../account-pool/account_pool/oauth_browser.py)、[上号任务](../../../account-pool/account_pool/onboarding/service.py)、[适配补丁](../../../account-pool/cliproxy-adapter/settings-adaptation.patch)

## 输入输出与流程

OAuth 创建一次性 state，用户复制链接在本地浏览器登录，再手动回填回调；界面不会因为生成链接就自动打开浏览器或标记成功。回调要验证 state、有效期、归属和消费状态，登录、验证码及二次验证仍由用户完成

认证文件导入和邮箱 OAuth 上号是两条任务链，按原任务/卡片 ID 重试，不能把文件卡片自动归入邮箱库存。准备好账号不等于授权、模型和配置全部验证通过。受控浏览器是可选 worker，固定 digest 和会话验证单独处理

**真实认证文件与内部身份文件必须区分。** `.codex-managed-identity` 内保存适配器的内部身份状态，不是另一张卡或可导入的账号凭据。正常已授权 CPA 卡应只枚举出唯一真实绑定文件；误枚举和真实多凭据要先分类再修复

## 配置与最小使用

在隔离测试卡中，按“生成授权链接、复制登录、回填回调、检查绑定及模型”完成最小流程；这些步骤**会产生授权状态和凭据**，必须得到账号持有者授权，文档检查不代为登录

token、密码和回调 code 不进入命令参数、日志或截图。worker 未配置时保持未配置，不为了排障自动启用。边界和更完整的流程见[受控浏览器说明](../../../account-pool/docs/controlled-oauth-browser.md)、[上号说明](../../../account-pool/ONBOARDING.md)及[归属说明](../../../account-pool/CREDENTIAL_OWNERSHIP.md)

## 可选授权自动化与 2FA

浏览器启动采用分层超时：首次授权页导航 60 秒、Compose 等待就绪 120 秒、该次 Docker 启动命令 150 秒，同步创建会话的 Manager HTTP 调用为 240 秒，给准备、诊断和清理留出余量。Manager 在 `oauth_browser.py` 集中定义启动预算，并把导航预算传给 worker；其他 Docker 命令和接口超时不变，登录自动化与会话总时限也不变

browser 健康检查有 120 秒启动宽限，导航与控制入口就绪后仍立即放行；不提前写 ready 文件。首次成功后连续 3 次探针失败即判定不健康。启动失败时先采集容器状态和浏览器阶段事件，再清理会话；两次诊断命令各限 5 秒，日志取最后 30 行，每路输出最多解析 16 KiB。Manager 只记录固定的服务、状态、退出码、阶段、耗时和错误分类，不转存原始 stderr、环境变量或授权参数。诊断失败不能阻止清理，取消启动仍终止命令并清理资源

worker 日志以 `oauth_browser_startup` 标记阶段，Manager 日志以 session ID 关联 `OAuth browser startup failed`、`OAuth browser state` 和 `OAuth browser stage`。导航超时、代理错误、TLS 错误与进程错误分开记录，不自动重新提交登录资料。镜像需配套更新 Manager、browser-worker 和同步接口所在的 LiteLLM 才完整生效；这些日志和本地模拟测试不能替代真实授权验收，不需要修改 socket proxy 权限

Manager 的 `ACCOUNT_POOL_OAUTH_BROWSER_IMAGE` 必须使用 digest。“自动化上号”下的“2FA / OAuth 授权”保留原任务队列、指纹配置和运行设置，默认关闭。先自检当前镜像、准备指纹，再选择已有空白 Codex 卡或填写卡名、选择代理创建新卡，提交一条 `email----password----TOTPBase32` 资料。卡名留空沿用 Codex-套餐-账号；不覆盖自定义名称。手动 OAuth 与邮箱库存保留在同一授权页

授权队列只允许删除已结束、清理完成的单条记录。删除不会移除卡片、凭据、浏览器指纹或账号绑定，不能以删除代替取消任务

手动文件导入和原有浏览器授权仍保留。自动化全局只推进一个账号，和人工登录互斥；同账号重试沿用原指纹，浏览器和 CPA 使用同一代理，代理失败不直连。人工接管要先等脚本停止，交回时先关闭输入并重新确认账号。未知验证、邮箱、短信和 CAPTCHA 不会猜测或绕过，需人工处理。总时限最多 600 秒，人工等待最多 300 秒且计入总时限

任务行显示本次出口和已绑定的配置语言。语言下拉框和首次绑定的自动匹配共用 Manager 的地区配置表。日本为 `ja-JP`、巴西为 `pt-BR`，香港为 `zh-HK`、德国为 `de-DE`、乌克兰为 `uk-UA`。手选语言不会换代理，已绑定的配置重新授权也不会改写语言。使用新增语言前，Manager、界面和固定浏览器镜像需配套更新

验证页可能显示另一种语言：页面诊断优先用网页声明的语言，没有声明才用配置语言。无法确认时保留“查看画面”，不把未知验证码当成邮箱或验证器验证码

看到“上游要求邮箱验证”时，点“停止脚本并接管”。接管确认后打开同一会话，在授权页输入自己邮箱收到的码；遇到人机验证也只在授权页由本人完成。画面未能打开时可点“查看画面”重开。完成验证后可在当前页面继续授权，或在能确认账号的页面交回自动化。未收到并消费回调、保存凭据前，Chat 和 Responses 显示“未验收”，无需为等待邮箱验证码重新创建任务

凭据保存与模型推理是两个结果。只有同卡 Chat 和 Responses 都得到完整文本才标记验收通过；失败后可指定这张卡已启用的模型“仅重试验收”，不会重新登录、重放回调或覆盖凭据。进程重启不自动重发结果未知的登录和推理。取消或超时会清理临时浏览器，保留已保存凭据，不能保证已经发出的上游请求未被处理

回调落盘后，自动化独占未释放前不会向普通 Key 放行；当前任务核对账号后仅能执行自己的同卡内部探测，不能把它等同于真实公共 Key 验收。如果回调保存的邮箱与任务账号不符（包括缺少邮箱），任务失败前会在同卡锁内持久化停用卡片，阻止普通 Key 和模型测试接单；保留凭据供核查，定期状态刷新不会自动启用。核对后应重新授权，不要只把失败任务标为完成

首次自动登录提交的邮箱、密码和 2FA 密钥保存在 Manager 的加密任务记录中。同一卡片确认掉授权后，点“重新授权”会用该卡的资料排入串行队列，并在当前页显示进度；重复点击不会另开任务。有效凭据不会被覆盖。找不到保存资料时才提供手动授权链接；删除该卡所有加密任务记录后不能自动复用。上游需要验证码或额外验证时仍需接管。

资料只通过管理员接口提交并在 Manager 按环境加密，不进入任务查询、浏览器存储或查询缓存；屏幕沿用原来的短期一次性票据，不公开 VNC 端口。不要把账号文件、截图中的私密资料或验收 Key 提交进 Git。实现入口是 [oauth_automation](../../../account-pool/account_pool/oauth_automation)，构建命令见 [worker 说明](../../../account-pool/browser-worker/README.md)，实际验收范围与已知基线失败见 [2026-11-04 验收记录](../../../account-pool/docs/oauth-automation-acceptance-20261104.md)

验收默认跳过仍在冷却的模型；指定冷却模型时停止，不用验收请求解除上游限制。失败消息区分 HTTP 状态、网络结果未知和响应不完整，不包含上游原始正文。后续回归、真实调用结果及未完成的全局门禁见 [2026-11-05 收尾记录](../../../account-pool/docs/oauth-automation-acceptance-20261105.md)

## 依赖与改动影响

归属使用现有 seed 派生的 HMAC 指纹，生产依赖 PostgreSQL 与跨进程操作锁。授权、上传替换、刷新、删除和配置共享归属约束；改变枚举或回调会影响准入、批量上号、策略同步和 UI 缓存

## 风险与禁止事项

不要重复导入同一凭据、让多个进程并发重放旧 refresh token、把旧备份覆盖到已轮换文件，或删除内部身份文件试图“少一个认证文件”。手动刷新要求显式确认、正确版本与归属，不能作为镜像升级步骤。access-token-fallback 必须有被测模型的有效验证，不是全局忽略认证错误

## 测试与通过标准

服务端授权浏览器的回调中继内存限制为 256 MiB，浏览器和出站中继的原有资源及隔离设置不变。该限制由 Manager 生成 Compose，需要更新 Manager 才对新会话生效；只替换浏览器 worker 不会修改旧 Manager 生成的限制。镜像自检是离线组件检查，不代表控制网络或实际授权通过。正式验收还需检查配置的控制网络、Manager 与网关连接、浏览器启动、回调及同卡推理，不能用自检成功代替。

[OAuth API](../../../account-pool/tests/account_pool/test_oauth_browser_api.py)、[会话](../../../account-pool/tests/account_pool/test_oauth_browser.py)、[归属](../../../account-pool/tests/account_pool/test_credential_ownership.py)、[刷新](../../../account-pool/tests/account_pool/test_credential_refresh.py)、[降级](../../../account-pool/tests/account_pool/test_access_token_fallback.py)、[上号](../../../account-pool/tests/account_pool/onboarding/test_onboarding_service.py)。须验证一次性回调、冲突拒绝、失败不泄密和不丢轮换凭据。mock 通过不代表真实 OAuth 已完成

## 故障排查

两个文件先看是否包含内部身份目录，再看实际补丁和镜像；不要凭数量直接删除。回调失败检查 state、过期和绑定，不盲目重放 code。刷新失败区分网络、能力缺失和真实撤销，不能从旧 token 哈希未变推断它仍可续期

## 恢复与回滚

保留当前认证卷及已轮换凭据，优先恢复运行时能力和配置。真的失效需要账号持有者重新授权；迁移归属按现有删除/释放流程明确处理。回退镜像不恢复旧 Token，详见[数据边界](../data-and-backups.md)

## 刷新失败与 access 降级的验收边界

新策略默认允许 access 降级，已保存的手动关闭值仍为关闭，不批量改写旧卡。未绑定凭据时只保存策略意图，绑定后同步需通过运行时能力检查。刷新仍使用现有单卡协调器，锁内重读凭据，轮换成功但保存失败时只重试保存，不重复兑换旧 refresh token。上游拒绝首次即分类为需要重新授权，不承诺撤销凭据能被刷新成功

同一份刷新凭据已被拒绝时，后续模型请求即使改变卡片的运行状态，也不再为这份凭据排期刷新。凭据面板显示需要重新授权，不显示下次刷新时间。这不等同于账号封禁，也不撤销仍有效的同模型访问验证。

access 降级只接受同卡同模型的完整推理验证，手动关闭、归属冲突、配置未同步、冷却、耗尽额度和访问令牌到期仍阻止使用。纯 access 的 CPA 和 Sub2API 文件可导入并保留独占归属。缺少 refresh、刷新被拒绝或刷新结果未知时统一验证现有 access。额度查询失败或过期观测不等于认证失效，未到期的真实耗尽仍拦截；有效同模型证明按凭据、代理、配置代际和到期时间复用，普通模型测试仍走该卡的正常 Chat/Responses 路由，不以管理接口成功代替普通调用。模型测试计时包含前置验证和路由同步，冷启动比已有证明多一次验证请求

设计核对了 [CLIProxyAPI 并发刷新讨论](https://github.com/router-for-me/CLIProxyAPI/issues/3783) 和 [sub2api OAuth 刷新协调器](https://github.com/Wei-Shaw/sub2api/blob/4a4a83ee5ec86b19d3a2dd9b6471de5128963bec/backend/internal/service/oauth_refresh_api.go)，沿用已有锁和重读机制，没有另外添加一套刷新调度。单进程协调不代表同一 refresh token 在其他服务中并发刷新也安全

刷新 POST 发出后若响应丢失、正文不完整、成功正文无有效访问令牌或返回 5xx，刷新结果按未知处理，停止使用旧 refresh 重试并持久化暂停状态；重新启动也不能自动重放。仅明确的连接建立失败可退避重试。未知结果不是 access 已失效，有效 access 仍可按模型验证使用。无法从丢失的响应找回新 refresh，后续续期可能仍需重新授权

Codex 刷新会先在运行时状态存储写入绑定当前凭据指纹的 `pending`，写入失败或存储未配置时不发送刷新请求。进程在回包前退出，重启将残留 `pending` 视为 `unknown_outcome`，停止手动与自动重放。并发停用或额度错误不妨碍保存防重放标记，也不会被刷新结果覆盖。此保护依赖保留当前认证卷及运行时状态；不能恢复丢失的新 refresh，也不能防止其他服务使用凭据副本

Codex 归属检查同时登记 access 与 refresh 的不可逆指纹；即使缺少邮箱或账号信息，同一 access 的纯 access 文件与完整文件也不能分别绑定到两张卡。已有卡片在凭据状态核验时补充指纹，冲突时停止接单，不自动挑选保留卡片

卡片 Responses 模型测试使用 LiteLLM 的 `litellm_metadata` 保存内部日志身份，不将它写入供应商 `metadata` 请求字段，普通 Chat/Responses 请求与模型测试使用相同卡片限制


### 发布浏览器登录脚本

在「自动化上号 → 2FA / OAuth 授权 → 运行设置 → 浏览器登录脚本」可以发布单个 `login.mjs`，也可以上传一个目录并选择其中任意 `.mjs` 或 `.js` 入口。入口必须导出 `automateLogin(page, credential, options)` 和 `verifyResumableAccount(page, credential, options)`，可用镜像中的默认脚本作为起点。目录内的相对辅助脚本和数据资源会一并保留，不要求固定的文件清单，不自动安装依赖。上限为 64 个文件、解码后总计 64 KiB，只允许相对路径

首次启用需要配套更新 LiteLLM、Manager 和按 digest 配置的 browser-worker，并由部署人员仅在 Manager 配置 `ACCOUNT_POOL_OAUTH_LOGIN_SCRIPT_PUBLISH_KEY`（至少 32 字符、与管理员 Key 和 Manager Token 不同）。上传和切回内置脚本都要用发布密钥。它等同于发布能接触登录资料的受信代码，不应交给普通管理账号，也不要把凭据放进脚本。现有浏览器隔离不是对恶意脚本的安全沙箱

发布后仅影响下一次启动的自动授权，运行中的任务、手动授权不变。脚本包保存在 Manager 数据卷，下次在只读 Worker 的临时目录展开。Manager 先确认 Worker 加载了相同 SHA‑256 的脚本，再发送登录资料。脚本加载失败或 Worker 未确认时任务停止，不静默降级、不重发登录资料；在设置页切回内置脚本即可恢复。首次更新后日常上传不需重建镜像或重启服务

### 查看和清理 Manager 日志

代理管理员可从侧栏「可观测性 → Manager 日志」按完整浏览器会话 ID 或卡片 ID 查询。日志只展示步骤、状态、固定原因代码、时间、必要的 HTTP 状态和耗时，不保存原始容器日志、认证资料或授权链接。旧 Worker 可继续运行，但更新后才会提供更细的页面阶段；这页不补录旧容器日志

「清理旧日志」可删除早于指定天数的 Manager 浏览器事件。范围是所有卡片，不受当前筛选限制，也不删除模型请求日志。需要二次确认，操作后显示实际删除条数。原有全局自动留存不变
