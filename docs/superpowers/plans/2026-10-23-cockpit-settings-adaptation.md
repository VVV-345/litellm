# Cockpit 设置适配实施计划

> 执行采用 superpowers:subagent-driven-development，逐阶段验证后 commit，不推送、不部署

**目标：** 在现有卡片和号池内复用稳定安装身份、显式身份混淆及九供应商设置，不替换账号池

**架构：** LiteLLM 继续独占鉴权、卡片范围、路由、重试与计费。CLIProxyAPI 使用固定最新 fork 源码的窄补丁，直接复用 Cockpit 安装字段改写模块，新增服务器持久化适配。现有 Manager/UI 只接入经过能力检查的选项

**技术：** Python/Pydantic、React、Go；上游身份算法与测试保留来源

**需求依据：** 本会话已批准的 1、2 和其他供应商设置适配；个人非商业自用；每阶段 commit

## 不变边界

在 `CLIProxyAPI分支` 直接提交，不创建分支或 worktree。隔离的 GitHub 源码仅作为构建输入，不改相邻已有 checkout。默认 off 和既有 device/session/full 行为不变，新增 managed 模式，未升级底层时拒绝假成功

不改已存卡片，不更新默认镜像，不触碰凭据，不推送或部署。安装身份不提供跨账号解密能力；身份混淆保持原有明确开关。只对已知安装字段做精确替换，不增加响应输出后的重试

后续才考虑整体方案：客户端经 LiteLLM 入口进入 Cockpit 账号池/请求引擎，再请求供应商。届时 Cockpit 替换重叠选卡、重试与请求逻辑，保留本网页；本次不得叠加或切换第二套路由器

## 阶段与验收

- [x] 1. 固定源码与来源：`account-pool/cliproxy-adapter/sources.json` 对照 Git HEAD，保留许可和九供应商映射，commit `f1289af7b3`
- [x] 2. 底层适配：复用 Cockpit managed_identity 模块，补充服务器持久化、HTTP/SSE/WS 接入和能力接口；先失败测试再修复；验证刷新、重启、并发、坏文件、已知字段精确改写、两开关组合；保存可重建 patch，commit
- [x] 3. Manager/UI：扩展已有 identity_fingerprint_mode 的 managed 选项，先检查运行中底层能力再下发；修复 Claude/Kimi 保存与消费不同步，验证其他供应商映射、默认值、重试归属；生成 API types，跑定向测试，commit
- [x] 4. 回归与回滚：跑现有换卡、续聊与不可重放边界测试；固定输入执行 BASELINE/MODIFIED/ROLLBACK，恢复哈希相等；记录验证范围、未做的真实供应商验收，commit

## 文件责任

`account-pool/cliproxy-adapter/` 保存版本锁、来源说明、补丁及可重复应用入口，不引入完整第二份引擎。Manager 复用 `channels/cliproxyapi/client.py` 的传输和 `settings_sync.py` 的同步入口；策略枚举在 `policies.py` 与代理模型同步。前端复用 `AccountPoolProviderPolicyFields.tsx`，不复制 Cockpit Tauri 页面

本次四角色证据固定在 `.local/cockpit-adaptation/transaction/`：`MODIFIED_FILE.tar`、`DIFF_FILE.patch`、`VERIFICATION.txt`、`ROLLBACK.sh`。补充日志延伸这些角色，不替换它们

## 已执行记录

阶段 1 提交为 `f1289af7b3`。阶段 2 的补丁已在固定提交的干净源码副本应用，17 个文件逐字节匹配开发副本；五个 Go 包的定向回归与 server 构建通过。新增真实 localhost HTTP/SSE/compact/WS 请求观测覆盖 identity_confuse 两种状态，跨进程身份保持、API-key 范围和能力接口鉴权通过。此处的 localhost 上游是测试服务，不是真实供应商

底层源码、构建工具和日志留在本地隔离目录，补丁与应用入口纳入版本库。未更新默认镜像、未部署、未推送

阶段 2 提交为 `24b49b9cb0`。阶段 3 的 Manager 六文件定向测试为 174 passed，保留一条 Starlette/httpx 弃用警告；界面集成测试为 13 passed，生产源码 tsc 通过，API 类型重新生成。ESLint 为 0 错误、15 条既有相邻代码警告，相关 Python Ruff 通过。五个 Python 源码的严格类型检查在基线与修改版均为 118 项诊断，逐条比较没有新增，不能称全量类型检查通过

额外运行授权视图契约测试时发现期望中缺少已有 spend_usd 字段；原始提交 `71a52d51eb` 的隔离副本也复现同一失败，本次未修改。换卡、续聊、配额切换和管理集成的六文件回归已完成 466 passed，保留既有 94 条警告；首次执行缺少 Manager 导入路径，修正 PYTHONPATH 后通过，没有为此改生产代码

阶段 3 提交为 `a06aa13653`。阶段 4 在一次性副本完成原版、改版、回滚的同输入策略与真实 localhost 出站检查，回滚后 38 个受影响路径的文件存在状态及原始字节哈希一致。DIFF 重建的改版与归档一致，已重新应用 GOAL，四个固定角色均已重新打开。原 policies.py 的磁盘基线是 CRLF，Git blob 为 LF，回滚以修改前记录的磁盘字节为准，不用换行归一化冒充相同哈希

结果与未覆盖项见 [本轮验证记录](2026-10-23-cockpit-settings-adaptation.validation.md)。整体替换账号池与请求引擎的路线仍只作为后续考虑，本次未实施
