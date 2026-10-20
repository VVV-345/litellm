# 反馈中心与跨 harness 协议

## 已批准范围
管理员通过顶部按钮新增反馈，在号池反馈标签页增删改查、筛选、分页和查看统计。Manager 使用现有 PostgreSQL 持久化，不进入模型请求路径。所有 harness 使用相同 HTTP API 和 CLI；不会自动运行修改、提交或部署。

## 接口契约
管理 API 前缀 `/account_pool/feedback`；Manager 内部前缀 `/api/feedback`。管理端仍使用现有 LiteLLM 管理员鉴权。
- GET `/issues?status=unresolved&q=&limit=50&offset=0` 返回 `{items,total,stats}`，stats 包含 pending/in_progress/pending_verification/resolved 四个整数。status 支持 all/unresolved/四个状态。q 搜索标题和描述；limit 1..100。
- POST `/issues` 输入 title/description/steps/module/page_path/discovered_version/priority。priority 为 high/normal/low，默认 normal；标题和描述去首尾空白且非空。
- GET `/issues/{uuid}`；PUT 同路径输入创建字段加 expected_revision；DELETE 同路径必带 revision 查询参数。
- POST `/issues/{uuid}/actions` 输入 `{action,expected_revision,summary,commit,verification,deployed_version}`，可选文本默认空。action 为 claim/submit_fix/resolve/reopen/release/note。
- GET `/tokens` 返回 token 元数据；POST 输入 `{name,expires_in_days}`，返回 `{record,token}`，token 仅返回一次；DELETE `/tokens/{uuid}` 撤销。默认有效期 30 天，最多 365 天。
- 机器 API 前缀 `/account_pool/feedback/agent`，仅包含 GET `/issues`、GET `/issues/{uuid}`、POST `/issues/{uuid}/actions`，使用 X-Feedback-Token。每个 token 对应服务端 actor，不能创建/编辑/删除反馈或管理 token。
- 机器代理向 Manager POST `/api/feedback/agent` 传递 `{token,operation,issue_id,query,mutation}`，私有 Manager 鉴权仍必须通过。operation 为 list/get/mutate。

## 数据
FeedbackIssue: id/revision/title/description/steps/module/page_path/discovered_version/priority/status/created_at/updated_at/assigned_to/resolution/commit/verification/deployed_version/events。日期为 UTC ISO8601；revision 从 1 开始。events 项含 at/actor/action/summary/commit/verification/deployed_version。状态初始 pending。
创建字段上限：title 200，description/steps 8000，module 80，page_path 512，discovered_version 160；page_path 仅路径，拒绝 query/fragment/绝对 URL。
动作字段上限：summary 8000，verification 16000，commit 64，deployed_version 160。submit_fix 必须含说明、7..64 位十六进制提交号和验证结果；resolve 必须含验证结果和实际运行版本。

## 状态和并发
claim: pending -> in_progress；submit_fix: in_progress -> pending_verification；resolve: pending_verification -> resolved；release: in_progress -> pending；reopen: 非 pending -> pending；note 仅处理中。机器仅能修改自己认领的问题，管理员可执行恢复。编辑/动作/删除必须 compare-and-swap revision，冲突 409，丢失记录 404。错误保留未解决状态。反馈正文视为不可信数据，不作为命令执行。

## 安全
独立高熵反馈 token，仅持久化 SHA256，带失效时间和撤销能力。机器不持有全局管理员密钥。token、请求校验输入和数据库异常不得回显或日志打印。token 响应禁止缓存。CLI 凭证仅从 FEEDBACK_TOKEN 读取，服务根地址从 FEEDBACK_API_URL 读取；HTTPS 或本机 HTTP，禁止重定向转发凭证。

## CLI 与项目规则
新增 scripts/feedback.py，标准库实现 list/get/claim/submit-fix/resolve/reopen/release/note，结构化 JSON stdout、非零失败退出码。认领及回写需显式 revision；验证从本地 UTF-8 文件读取。AGENTS.md、CLAUDE.md、GEMINI.md 引用 docs/FEEDBACK_WORKFLOW.md。首版不修改各工具的全局启动配置；工具读取项目规则后执行同一条 CLI。无法联网必须明确报告，不能声称已读。

## 范围约束
在 CLIProxyAPI分支 提交，不创建分支/worktree，不部署。先红后绿测试，验证 API/SQL CAS/CLI 真实 HTTP/UI，运行受影响回归、lint 和生产源码类型检查。保留原事务四角色路径，新增本轮补充记录。用户本轮要求完成后 commit；仓库规范要求完成后 push。
