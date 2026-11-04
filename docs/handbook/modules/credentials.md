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

Manager 的 `ACCOUNT_POOL_OAUTH_BROWSER_IMAGE` 必须使用 digest。号池的“授权自动化”有任务队列、指纹配置、运行设置三个标签页；默认关闭，先自检当前镜像，再生成固定 Linux 指纹、选择已同步普通出口代理的空白 Codex 卡片、提交一条 `email----password----TOTPBase32` 资料，最后显式开启

手动文件导入和原有浏览器授权仍保留。自动化全局只推进一个账号，和人工登录互斥；同账号重试沿用原指纹，浏览器和 CPA 使用同一代理，代理失败不直连。人工接管要先等脚本停止，交回时先关闭输入并重新确认账号。未知验证、邮箱、短信和 CAPTCHA 不会猜测或绕过，需人工处理。总时限最多 600 秒，人工等待最多 300 秒且计入总时限

凭据保存与模型推理是两个结果。只有同卡 Chat 和 Responses 都得到完整文本才标记验收通过；失败后可指定这张卡已启用的模型“仅重试验收”，不会重新登录、重放回调或覆盖凭据。进程重启不自动重发结果未知的登录和推理。取消或超时会清理临时浏览器，保留已保存凭据，不能保证已经发出的上游请求未被处理

资料只通过管理员接口提交并在 Manager 按环境加密，不进入任务查询、浏览器存储或查询缓存；屏幕沿用原来的短期一次性票据，不公开 VNC 端口。不要把账号文件、截图中的私密资料或验收 Key 提交进 Git。实现入口是 [oauth_automation](../../../account-pool/account_pool/oauth_automation)，构建命令见 [worker 说明](../../../account-pool/browser-worker/README.md)，实际验收范围与已知基线失败见 [2026-11-04 验收记录](../../../account-pool/docs/oauth-automation-acceptance-20261104.md)

## 依赖与改动影响

归属使用现有 seed 派生的 HMAC 指纹，生产依赖 PostgreSQL 与跨进程操作锁。授权、上传替换、刷新、删除和配置共享归属约束；改变枚举或回调会影响准入、批量上号、策略同步和 UI 缓存

## 风险与禁止事项

不要重复导入同一凭据、让多个进程并发重放旧 refresh token、把旧备份覆盖到已轮换文件，或删除内部身份文件试图“少一个认证文件”。手动刷新要求显式确认、正确版本与归属，不能作为镜像升级步骤。access-token-fallback 必须有被测模型的有效验证，不是全局忽略认证错误

## 测试与通过标准

[OAuth API](../../../account-pool/tests/account_pool/test_oauth_browser_api.py)、[会话](../../../account-pool/tests/account_pool/test_oauth_browser.py)、[归属](../../../account-pool/tests/account_pool/test_credential_ownership.py)、[刷新](../../../account-pool/tests/account_pool/test_credential_refresh.py)、[降级](../../../account-pool/tests/account_pool/test_access_token_fallback.py)、[上号](../../../account-pool/tests/account_pool/onboarding/test_onboarding_service.py)。须验证一次性回调、冲突拒绝、失败不泄密和不丢轮换凭据。mock 通过不代表真实 OAuth 已完成

## 故障排查

两个文件先看是否包含内部身份目录，再看实际补丁和镜像；不要凭数量直接删除。回调失败检查 state、过期和绑定，不盲目重放 code。刷新失败区分网络、能力缺失和真实撤销，不能从旧 token 哈希未变推断它仍可续期

## 恢复与回滚

保留当前认证卷及已轮换凭据，优先恢复运行时能力和配置。真的失效需要账号持有者重新授权；迁移归属按现有删除/释放流程明确处理。回退镜像不恢复旧 Token，详见[数据边界](../data-and-backups.md)

## 刷新失败与 access 降级的验收边界

新策略默认允许 access 降级，已保存的手动关闭值仍为关闭，不批量改写旧卡。未绑定凭据时只保存策略意图，绑定后同步需通过运行时能力检查。刷新仍使用现有单卡协调器，锁内重读凭据，轮换成功但保存失败时只重试保存，不重复兑换旧 refresh token。上游拒绝首次即分类为需要重新授权，不承诺撤销凭据能被刷新成功

access 降级只接受同卡同模型的完整推理验证，手动关闭、归属冲突、配置未同步、冷却、耗尽额度和访问令牌到期仍阻止使用。纯 access 的 CPA 和 Sub2API 文件可导入并保留独占归属。缺少 refresh、刷新被拒绝或刷新结果未知时统一验证现有 access。额度查询失败或过期观测不等于认证失效，未到期的真实耗尽仍拦截；有效同模型证明按凭据、代理、配置代际和到期时间复用，普通模型测试仍走该卡的正常 Chat/Responses 路由，不以管理接口成功代替普通调用。模型测试计时包含前置验证和路由同步，冷启动比已有证明多一次验证请求

设计核对了 [CLIProxyAPI 并发刷新讨论](https://github.com/router-for-me/CLIProxyAPI/issues/3783) 和 [sub2api OAuth 刷新协调器](https://github.com/Wei-Shaw/sub2api/blob/4a4a83ee5ec86b19d3a2dd9b6471de5128963bec/backend/internal/service/oauth_refresh_api.go)，沿用已有锁和重读机制，没有另外添加一套刷新调度。单进程协调不代表同一 refresh token 在其他服务中并发刷新也安全

刷新 POST 发出后若响应丢失、正文不完整、成功正文无有效访问令牌或返回 5xx，刷新结果按未知处理，停止使用旧 refresh 重试并持久化暂停状态；重新启动也不能自动重放。仅明确的连接建立失败可退避重试。未知结果不是 access 已失效，有效 access 仍可按模型验证使用。无法从丢失的响应找回新 refresh，后续续期可能仍需重新授权

卡片 Responses 模型测试使用 LiteLLM 的 `litellm_metadata` 保存内部日志身份，不将它写入供应商 `metadata` 请求字段，普通 Chat/Responses 请求与模型测试使用相同卡片限制
