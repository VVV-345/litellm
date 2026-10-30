# 号池签名恢复与认证导入综合修复方案

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本项目在当前会话顺序执行，不另建分支或 worktree，不自行分派子 agent

**Goal:** 修复尚未输出时被预读字节边界挡住的安全签名恢复，并完善认证文件导入验收，不丢失工具历史、不重复执行工具

**Architecture:** 请求恢复留在 LiteLLM 网关预读及现有同卡重试链路；格式识别留在 Manager 共享解析器和对应前端入口。两个问题分别验证、分别提交，发布时核对前后端版本一致

**Tech Stack:** Python、FastAPI、pytest、TypeScript、Vitest、Docker/GitHub Actions

**Spec:** 本聊天已确认的故障和修复要求；签名诊断见 `D:/Work2api/litellm/.local/account-pool-ui-layout/signature-preflight-diagnosis/code-review.md`，导入证据见 `D:/Work2api/litellm/.local/account-pool-ui-layout/VERIFICATION.txt`

## 全局约束

- 当前分支 `CLIProxyAPI分支`，本轮起始提交 `cf8a225a0d55907446c997e8dcf97a29336fe586`
- 本轮已完成下述本地修复和分项验证；尚未推送、构建新镜像或部署
- 只改能复现的问题，先失败测试、后最小补丁，保留现有未提交文件
- 本地验证后 commit，明确收到 push 命令才推送；部署单独按更新手册执行
- 仅使用虚构凭据，不存储、转发、记录用户提供的密码、Token 或 2FA 密钥
- 不取消大小/时间上限，不强制重放工具，不把有歧义的历史当作安全历史

## 一、已完成与未完成

| 项目 | 已确认状态 | 后续 |
| --- | --- | --- |
| 仪表盘两列与后台返回优化 | 历史提交 `7347e4c58f`，本轮不重新改动 | 发布后的实际页面体验仍以现场为准 |
| CPA/Sub2API/2FA 识别 | 已有提交 `f58890e2c6` | 补齐下述实际入口验收 |
| 可选镜像架构 | 已有提交 `563692c77a` | 保留 arm64、amd64、双架构选项；不要硬编码单架构 |
| TXT 的“卡密导出”首行 | 已修复并提交 `cf8a225a0d`，尚未推送 | 发布后验收批量与单卡入口 |
| 签名错误 preflight_limit | 已修复并提交 `2fcf9b813d` | 发布后现场验收 |
| 其他导入失败被泛化成 JSON 错误 | 已修复并提交 `a4cf2b7017` | 发布后现场验收 |

签名诊断不能证明 Codex 子 agent 丢失工具，也没有证明上游最初拒绝签名的原因。现有证据是：同卡、无正文/工具输出、错误帧跨越 65536 字节时，本项目会退出可恢复阶段

## 二、任务 1：安全的流式签名恢复

**修改范围：**
- `D:/Work2api/litellm/litellm/proxy/management_endpoints/account_pool_retry.py` 的 `StreamBootstrap.capture/prepare`
- `D:/Work2api/litellm/litellm/proxy/management_endpoints/account_pool_stream.py` 的 SSE 帧状态与既有单帧限制，仅在确有需要时修改
- `D:/Work2api/litellm/litellm/proxy/management_endpoints/account_pool_gateway_forwarder.py` 的现有恢复准入与诊断，仅在确有需要时修改
- `D:/Work2api/litellm/tests/test_litellm/proxy/management_endpoints/test_account_pool_gateway.py`

**接口边界：** 保持网关公开协议、计费和卡片选择不变；复用现有签名清理、同卡一次重试、历史完整性检查，不创建第二套恢复流程

- [x] 将已有诊断中的 65535、65536、65537 字节及 17 字节/整块分块对照加入现有测试文件，先证实边界案例失败
- [x] 将 64 KiB 从“任意字节硬切断”改为有界预读预算；需要时完整识别跨边界的当前 SSE 事件。保留现有 4 MiB 单事件上限，并为延长预读保留有限的总缓冲上限和原有 deadline，不重置计时
- [x] 非正文注释/心跳不应过早终止恢复识别；不盲目丢弃 metadata。进入正常交付后保留事件顺序，既不重复也不漏发缓冲与 overflow
- [x] 仅在未交付正文/工具、历史可恢复且未重试时清理失效思考状态并同卡重试一次。第二次仍失败直接返回，已输出内容、工具副作用风险及缺失服务端历史继续禁止重试
- [x] 复验工具定义、tool_choice、配对的调用与结果、可读消息和多模态输入；不能用删除全部历史的方式恢复
- [x] 区分预读字节上限、恢复超时、已输出、历史不完整等终止原因；单帧过大仍走既有无效上游响应处理，不取消限制。诊断只含安全状态和关联信息，不新增提示词、凭据或加密思考原文日志
- [x] 定向回归通过，验证单独副本上的回滚及补丁重建，再独立 commit

执行回归时使用现有项目环境及任务专属临时目录，核心命令为：

```powershell
python -m pytest -c account-pool/pyproject.toml tests/test_litellm/proxy/management_endpoints/test_account_pool_gateway.py -k "signature or bootstrap" -q
```

验收：完整可恢复历史的 65537 字节对照不再仅因旧边界返回 preflight_limit；同一卡片最多两次上游请求；先输出工具的对照仍只有一次请求；过大/超时输入仍有明确的有界退出

## 三、任务 2：导入链路补齐，不重复实现解析器

**修改范围：** 首先检查 `D:/Work2api/litellm/account-pool/account_pool/onboarding_service.py` 的 `import_entries` 和预检错误传递，只改实证失败的入口；复用 `credential_import.py`，不新建万能格式转换器

- [x] `parse_two_factor_text` 和 `parseMailboxAccounts` 已兼容首个非空行“卡密导出”、空行和 BOM，不跳过其他坏账号行
- [x] 为坏分隔符、无效 Base32、错误供应商和无效 JSON 增加精确错误回归，保留原始安全错误，不再覆盖成只支持 JSON 的提示；继续不回显密码或密钥
- [x] 通过本地 Manager 接口与模拟下游分别验证 CPA JSON、Sub2API 单账号与整包、纯 2FA TXT、带标题 TXT；多账号包走批量，单卡仅收一个账号
- [x] 已核对固定版本 CLIProxyAPI 的上传源码要求 .json，并通过实际 HTTP 客户端配合模拟下游复现拒绝；提交 `6c4fc352e0` 在 Manager 内容校验成功后统一文件名和 MIME。真实线上下游验收仍待部署
- [x] 检查“应用认证模板”开关：现有模板只适用单账号 JSON；对 TXT/整包给出明确限制与关闭模板的指引，不静默忽略用户模板选择
- [x] 前后端格式识别保持一致；2FA 仍是加密登录资料，需要 Codex、出站代理及正常 OAuth/MFA，不自动视为授权完成
- [x] 对新增修复做失败/成功/回滚对照，通过后独立 commit，保留已经完成的标题兼容提交

```powershell
python -m pytest account-pool/tests/account_pool/test_onboarding_service.py account-pool/tests/account_pool/test_onboarding_api.py -q
cd D:/Work2api/litellm/ui/litellm-dashboard
node node_modules/vitest/vitest.mjs run src/features/account-pool/api/AccountPoolOnboardingApi.test.ts src/features/account-pool/components/onboarding/AccountPoolOnboardingPanel.integration.test.tsx src/features/account-pool/components/credentials/AccountPoolCredentialsPanel.integration.test.tsx
```

## 四、任务 3：统一发布与现场验收

- [x] 四个专项分别本地验证并 commit，不重做已有仪表盘或架构工作
- [ ] 收到明确推送指令后 push；确认 Actions 发布对应完整 SHA，不能把本地测试当成镜像或部署验证
- [ ] 本机架构历史实测为 aarch64，本次可选择 arm64 构建；保留多架构选择，实际部署前重新核对服务器架构
- [ ] 部署前读取 `D:/Work2api/litellm/deploy/UPDATE_RUNBOOK.md`，重新确认主机、运行镜像 SHA、发布状态、备份、健康与回滚路径；当前终端连接已超时，运行版本仍未重新验证
- [ ] 获得部署授权后，确保 LiteLLM 前端与 Manager 来自同一候选版本，再进行现场导入及 Codex 父子会话、工具调用与恢复验证
- [ ] 仅在真实运行环境问题消失后确认线上解决，不自动部署，不自动关闭反馈

## 五、已有标题修复的历史验证，非本轮重跑

- BASELINE：批量提示“请选择文件名不含路径的 JSON 认证文件”，单卡 HTTP 422；后端 5 失败、52 通过
- MODIFIED：批量进入 OAuth 待授权，单卡 HTTP 200；后端 57 通过，前端相关 35 通过
- ROLLBACK：独立副本恢复相同错误和 5 失败、52 通过，恢复哈希与原始一致；工作区保留修改版
- 原四角色文件继续保留，本文作为补充方案，不替换已完成结果

## 执行顺序

任务 1 签名恢复 -> 任务 2 导入错误与入口验收 -> 各自验证与 commit -> 按用户指令 push -> 按授权部署和现场验收

## 六、本轮执行记录

| 专项 | 提交 | 本地验证 |
| --- | --- | --- |
| SSE 预读与签名恢复 | `2fcf9b813d` | 网关及原生路由 363 项通过；定向 117 项通过 |
| 保留具体导入错误 | `a4cf2b7017` | 导入服务及接口 63 项通过 |
| 单卡 JSON 内容的 TXT 文件名/MIME | `6c4fc352e0` | 导入服务及接口 69 项通过；所有权、管理代理等相关回归 123 项通过 |
| 模板不适用时给出关闭指引 | 与本执行记录同一提交 | 前端 4 文件共 52 项通过；独立 6 输入对照全部通过 |

签名专项 BASELINE/ROLLBACK 均 13 失败、104 通过，MODIFIED 117 通过。导入错误专项 BASELINE/ROLLBACK 均 6 失败、57 通过，MODIFIED 63 通过。上传文件名专项 BASELINE/ROLLBACK 均 6 失败、63 通过，MODIFIED 69 通过。模板专项独立对照 BASELINE/ROLLBACK 均 3 失败、3 通过，MODIFIED 6 通过。各专项均在独立副本执行回滚、核对原始哈希并验证补丁重建

Ruff、限定前端源文件类型检查通过。ESLint 无错误，保留模板文件既有两项警告；测试环境依赖和 Vitest 既有警告保留。未执行全仓库测试、生产 Next 构建、真实 OAuth 或付费上游请求，不能据此宣称线上故障已消失

本轮只提交当前分支，不 push，不部署。后续先明确推送，再按发布手册核对镜像及真实运行版本，最后进行现场验收
