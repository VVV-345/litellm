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

- [ ] 1. 固定源码与来源：`account-pool/cliproxy-adapter/sources.json` 对照 Git HEAD，保留许可和九供应商映射，commit
- [ ] 2. 底层适配：复用 Cockpit managed_identity 模块，补充服务器持久化、HTTP/SSE/WS 接入和能力接口；先失败测试再修复；验证刷新、重启、并发、坏文件、已知字段精确改写、两开关组合；保存可重建 patch，commit
- [ ] 3. Manager/UI：扩展已有 identity_fingerprint_mode 的 managed 选项，先检查运行中底层能力再下发；修复 Claude/Kimi 保存与消费不同步，验证其他供应商映射、默认值、重试归属；生成 API types，跑定向测试，commit
- [ ] 4. 回归与回滚：跑现有换卡、续聊与不可重放边界测试；固定输入执行 BASELINE/MODIFIED/ROLLBACK，恢复哈希相等；记录验证范围、未做的真实供应商验收，commit

## 文件责任

`account-pool/cliproxy-adapter/` 保存版本锁、来源说明、补丁及可重复应用入口，不引入完整第二份引擎。Manager 复用 `channels/cliproxyapi/client.py` 的传输和 `settings_sync.py` 的同步入口；策略枚举在 `policies.py` 与代理模型同步。前端复用 `AccountPoolProviderPolicyFields.tsx`，不复制 Cockpit Tauri 页面

本次四角色证据固定在 `.local/cockpit-adaptation/transaction/`：`MODIFIED_FILE.tar`、`DIFF_FILE.patch`、`VERIFICATION.txt`、`ROLLBACK.sh`。补充日志延伸这些角色，不替换它们
