# 授权自动化本地验收记录

本次在 `CLIProxyAPI分支` 延续 `b4172a8754`，复用 Python Manager、原有 OAuth/callback/屏幕中继、LiteLLM 管理鉴权和卡片路由；未新增独立 helper、SSH 通道或另一套 Manager。选择性复用了 [PR #4](https://github.com/VVV-345/litellm/pull/4) 中受限登录、TOTP 和可信地址判断的思路，没有合并其 macOS 控制端或删除现有 API

## 真实结果

本机独立 PostgreSQL、Manager、CPA、Linux Camoufox worker 和原生 LiteLLM 网关使用单独网络及数据库，不接触生产状态。worker 固定 SDK 0.5.9、浏览器 v158.0.1-beta.41，已实际构建并通过无网络自检。浏览器大文件使用预先下载且 SHA256 校验相同的构建输入，未更换版本或关闭校验；arm64 未在本机实测

同一份用户授权的测试账号经自动流程完成真实登录和一次性回调，认证文件保存后卡片 ready。没有人工输入账号或验证码，返回认证声明包含 OTP/MFA；这里只保留该布尔证据，未记录令牌、验证码、页面内容或逐步录屏，也未另外对该声明验签

最初默认发现的 `gpt-5.3-codex-spark` 未通过推理。没有重新登录或清除冷却，而是修复了验收重试不能换模型的问题，再用同卡已启用的 `gpt-5.6-luna` 仅重试验收，Chat 和 Responses 均通过。这不代表此订阅的所有已发现模型都可调用

随后通过正常签发的卡片 Key 请求原生 LiteLLM：Chat HTTP 200、完整文本、5.110 秒；Responses HTTP 200、completed、6.245 秒。同卡原生模型测试的 Chat/Responses 均 success，分别 9.294/7.548 秒。另用原生 curl 再次得到 stop/completed。测试栈只有这一张授权卡，未回退其他健康账号

格式整理后重建 Manager 和网关，再用同一份已保存凭据验收：Key Chat/Responses 分别 4.830/6.200 秒，卡片模型测试分别 6.539/6.551 秒，四项仍全部通过，没有再次登录。源码 UI 完成静态生产构建，并连接真实隔离网关检查 1440×960 和 390×844 两种布局、三个内层标签页及已完成任务，没有页面异常或整页横向溢出

网关测试镜像按根锁文件安装依赖并直接运行当前源码，不是生产发布镜像。测试结束关闭了隔离栈自动化，保留完成结果。未 push、未部署生产、未操作生产凭据

## 审查与重测入口

浏览器构建及离线检查命令见 [worker 说明](../browser-worker/README.md)。Manager 回归从仓库根目录运行 `python -m pytest account-pool/tests`；新控制流程使用专门的 `OAUTH_AUTOMATION_TEST_DATABASE_URL`，仅允许名称以 `/oauth_automation_test` 结尾的隔离数据库，会清理该数据库中的自动化队列表，不能指向生产

新增数据库测试覆盖跨 Python 进程领取互斥、重复任务、重启不重放、人工接管、双时限清理、取消中止本地推理、模型失败后只验收重试及秘密不回显。浏览器 Node 测试覆盖 TOTP 标准向量、受信页面/回调、错误身份和接管输入互斥。UI 使用既有组件和生成的 OpenAPI 类型，新增管理员入口，不改原有手动上号流程

真实接口重测使用同一卡片 Key 调用 `/v1/chat/completions` 和 `/v1/responses`，模型选该卡可调用的模型；管理员再调用 `/account_pool/environments/{card_id}/test`，分别提交 `mode: chat` 和 `mode: responses`。检查 HTTP、完整响应和固定 card_id，不只检查是否列出模型。不要将完整 Key、OAuth code 或请求头贴入报告

全量 Manager 回归在新功能加入前后均有两个既存失败：`test_authorization_view_contract_has_only_public_authorization_instructions` 的历史字段快照，以及 `test_policy_versions_and_supplier_scope` 的 access 降级默认字段快照。另有 7 个仪表盘分页测试缺少 QueryClientProvider，在不可变 b417 基线副本中同样失败。这些未被本次无关改动掩盖或删除；本次新增控制测试、对应浏览器测试、类型检查和其余号池 UI 回归单独记录

全量 Manager 为 1295 通过、32 跳过、上述 2 失败；原生号池接口回归为 907 通过、1 跳过、1 个同类策略字段快照失败，后者也用原始模块副本确认。其余号池 UI 为 311 通过。最终新增控制及传输测试 17 通过，Node 17 通过，新源码严格类型检查无错误或警告

预提交总门禁未全绿：本机缺少 make，检查器选择的新根虚拟环境缺 proxy 依赖，原有管理大文件未满足格式检查，全局 UI lint 预算仍超限（complexity 151/140、inline object 676/559）。没有放宽预算或顺带重格式化无关大文件。OpenAPI 已用既有完整测试环境单独生成，新增代码 lint、源文件类型检查和隔离 UI 构建另行通过；这份记录不是整站 CI 全绿的承诺

上游仍可能要求人工验证、限流或拒绝授权，不能保证每个账号都自动登录成功。本次只有一个获授权账号，未声称多真实账号、arm64 或生产发布链路均已验收
