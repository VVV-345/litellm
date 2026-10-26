# Cockpit 发布约束与交接提示词

本文件约束 LiteLLM + Cockpit 这条产品线的版本发布。服务器目录中虽有 test，但可能已承接实际网站流量；构建成功不等于部署成功，更不等于真实账号、模型和费用验收完成

## 可直接复制给其他 AI

```text
请只操作 D:\Work2api\Litellm-cockpit 的 codex/cockpit-reuse-plan 分支，沿用现有 Git 仓库和 worktree。先读取 CLAUDE.md、deploy/cockpit/README.md 和 AI_HANDOFF.md，核对 git status、当前 HEAD 和远端状态。发现其他 AI 的未提交修改或远端状态变化时，不得覆盖或强推

新产品的请求链路是 LiteLLM + Cockpit，不加回 Manager。镜像只发布到 ghcr.io/vvv-345/litellm-cockpit，一个包包含 litellm 与 cockpit 两个组件。只用 .github/workflows/publish-cockpit-images.yml，不触发旧产品工作流、不移动旧标签、不删除历史包、不设置 latest，不更换固定 Cockpit 源提交来绕过构建失败

版本从 deploy/cockpit/VERSION 读取，只统计成功的 cockpit-vX.Y[.Z] Git 标签。用户未说明版本时，使用最新成功版本的补丁位 +1；v1.0 后为 v1.0.1，v1.1 后为 v1.1.1，v2.0 后为 v2.0.1。用户明确指定新的更高系列，例如 1.1、1.2、1.3、2.0，只将 VERSION 写成相应的 v1.1、v1.2、v1.3、v2.0，不硬编码具体递增序列，不把文件每次自动加一。若用户指定已有或更低版本，先说明冲突，不能悄悄改标签

成功版本的 Git 标签使用 cockpit-v...，GHCR 使用 litellm-v... 和 cockpit-v...，不能创建裸 v... Git 标签。固定提交号镜像及 digest 保留，部署仍使用 cockpit-test-提交前10位 的 Release 清单，不能把服务器 deploy 参数换成语义版本。双架构构建、smoke、Release 发布、两份语义标签回读必须成功后才创建 Git 成功标签；失败不占成功版本号，不得伪造成功记录

只做需求所需的最小修改，先写能复现问题的测试，再修复、检查、commit。仅有明确 push 授权才推送，部署要另外取得授权，不自动操作服务器、生产登录信息、密钥、数据库、账号凭据或卷。当前服务器 runtime 是 /opt/zlia-pool/litellm-cockpit-test/runtime，旧 /opt/zlia-pool/litellm-migration/runtime 和旧源码目录不在修改范围

报告本地 commit、GitHub Actions run 链接和实际结论、版本号、GHCR 两个标签及对应 digest，并明确服务器是否部署。工作流失败需报告失败步骤、真实错误和修复后的验证，不能把 queued/in_progress 当成成功。版本标签冲突或发布中断时，先读取原 Release 和运行结果，沿用原版本完成剩余步骤，不覆盖镜像、不强推标签、不删除数据、不绕过运行包 hash、兼容指纹或备份门禁
```

## 指定下一个系列

```text
下一个发布版本明确指定为 1.2。请按 deploy/cockpit/AI_HANDOFF.md 执行，先确认当前成功版本低于 v1.2，再将 VERSION 改为 v1.2。后续未指定版本时从 v1.2.1 开始递增。这次只本地验证和 commit，未经另行授权不要推送或部署
```

## 默认发布与验证

```text
不指定新系列，请按现有最新成功版本自动补丁 +1，不重置为 v1.0。遵守 deploy/cockpit/AI_HANDOFF.md。请完成本地检查和 commit；推送或服务器部署分别等待我的明确授权。若已有对应 commit 的成功构建，复用记录，先报告它，不重复发布
```

```text
请只审查当前 Cockpit 发布：核对工作流分支与包名、双架构 build/smoke、Release 清单、Git 成功版本和 GHCR 两个语义标签的 digest 一致性。不得修改代码、标签、镜像、服务器或密钥。指出观察到的问题，区分已验证与尚未验证，不根据页面上的版本名推测部署状态
```
