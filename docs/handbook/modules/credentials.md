# 凭据、OAuth、受控浏览器与上号

## 职责与边界

本模块负责凭据与卡片的一对一归属、OAuth 会话和回调、认证文件导入、可选浏览器 worker 及手动刷新协调。不会凭空续期被撤销的 Token，也不以模型列表或保存成功代替推理验收

## 关键入口

[授权操作](../../../account-pool/account_pool/application/environments/authorization.py)、[认证文件操作](../../../account-pool/account_pool/application/environments/auth_files.py)、[CredentialOwnership](../../../account-pool/account_pool/credential_ownership.py)、[手动刷新](../../../account-pool/account_pool/credential_refresh.py)、[访问令牌降级](../../../account-pool/account_pool/access_token_fallback.py)、[OAuth 会话](../../../account-pool/account_pool/oauth_browser.py)、[上号任务](../../../account-pool/account_pool/onboarding_service.py)、[适配补丁](../../../account-pool/cliproxy-adapter/settings-adaptation.patch)

## 输入输出与流程

OAuth 创建一次性 state，用户复制链接在本地浏览器登录，再手动回填回调；界面不会因为生成链接就自动打开浏览器或标记成功。回调要验证 state、有效期、归属和消费状态，登录、验证码及二次验证仍由用户完成

认证文件导入和邮箱 OAuth 上号是两条任务链，按原任务/卡片 ID 重试，不能把文件卡片自动归入邮箱库存。准备好账号不等于授权、模型和配置全部验证通过。受控浏览器是可选 worker，固定 digest 和会话验证单独处理

**真实认证文件与内部身份文件必须区分。** `.codex-managed-identity` 内保存适配器的内部身份状态，不是另一张卡或可导入的账号凭据。正常已授权 CPA 卡应只枚举出唯一真实绑定文件；误枚举和真实多凭据要先分类再修复

## 配置与最小使用

在隔离测试卡中，按“生成授权链接、复制登录、回填回调、检查绑定及模型”完成最小流程；这些步骤**会产生授权状态和凭据**，必须得到账号持有者授权，文档检查不代为登录

token、密码和回调 code 不进入命令参数、日志或截图。worker 未配置时保持未配置，不为了排障自动启用。边界和更完整的流程见[受控浏览器说明](../../../account-pool/docs/controlled-oauth-browser.md)、[上号说明](../../../account-pool/ONBOARDING.md)及[归属说明](../../../account-pool/CREDENTIAL_OWNERSHIP.md)

## 依赖与改动影响

归属使用现有 seed 派生的 HMAC 指纹，生产依赖 PostgreSQL 与跨进程操作锁。授权、上传替换、刷新、删除和配置共享归属约束；改变枚举或回调会影响准入、批量上号、策略同步和 UI 缓存

## 风险与禁止事项

不要重复导入同一凭据、让多个进程并发重放旧 refresh token、把旧备份覆盖到已轮换文件，或删除内部身份文件试图“少一个认证文件”。手动刷新要求显式确认、正确版本与归属，不能作为镜像升级步骤。access-token-fallback 必须有被测模型的有效验证，不是全局忽略认证错误

## 测试与通过标准

[OAuth API](../../../account-pool/tests/account_pool/test_oauth_browser_api.py)、[会话](../../../account-pool/tests/account_pool/test_oauth_browser.py)、[归属](../../../account-pool/tests/account_pool/test_credential_ownership.py)、[刷新](../../../account-pool/tests/account_pool/test_credential_refresh.py)、[降级](../../../account-pool/tests/account_pool/test_access_token_fallback.py)、[上号](../../../account-pool/tests/account_pool/test_onboarding_service.py)。须验证一次性回调、冲突拒绝、失败不泄密和不丢轮换凭据。mock 通过不代表真实 OAuth 已完成

## 故障排查

两个文件先看是否包含内部身份目录，再看实际补丁和镜像；不要凭数量直接删除。回调失败检查 state、过期和绑定，不盲目重放 code。刷新失败区分网络、能力缺失和真实撤销，不能从旧 token 哈希未变推断它仍可续期

## 恢复与回滚

保留当前认证卷及已轮换凭据，优先恢复运行时能力和配置。真的失效需要账号持有者重新授权；迁移归属按现有删除/释放流程明确处理。回退镜像不恢复旧 Token，详见[数据边界](../data-and-backups.md)
