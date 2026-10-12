# 上游更新审查任务

两条更新链路必须独立执行、独立审查、独立生成报告。任何一条失败都不能阻止另一条继续收集结果，也不能把两条链路的版本、提交、冲突或测试结果合并到同一份报告。

## CLIProxyAPI

- 上游仓库：`router-for-me/CLIProxyAPI`
- 当前基线：`ACCOUNT_POOL_UPSTREAM_SYNC_CURRENT_TAG`
- 审查分支：`ACCOUNT_POOL_UPSTREAM_SYNC_BRANCH`
- 工作流：`ACCOUNT_POOL_UPSTREAM_SYNC_WORKFLOW`
- 任务顺序：读取最新 release，按提交顺序生成候选，逐提交运行兼容测试，记录冲突和失败步骤，只有全部通过才允许 promote
- 保留要求：现有 OAuth、设备码、API Key、Vertex、配额读取、冷却、模型发现和请求路由行为保持兼容

## LiteLLM

- 上游仓库：`BerriAI/litellm`
- 当前基线：`ACCOUNT_POOL_LITELLM_UPSTREAM_SYNC_CURRENT_TAG`
- 审查分支：`ACCOUNT_POOL_LITELLM_UPSTREAM_SYNC_BRANCH`
- 工作流：`ACCOUNT_POOL_LITELLM_UPSTREAM_SYNC_WORKFLOW`
- 任务顺序：读取最新正式 release，以当前业务分支为基线，在兼容分支副本合并固定官方 tag，运行配置的兼容检查，记录冲突和失败；promote 复测后仅创建面向 `CLIProxyAPI分支` 的草稿 PR，不自动合并或部署
- 保留要求：现有账号池 API、卡片 Key、日志、路由、计费、认证和部署入口保持兼容；新版功能只在通过测试后进入候选

每条任务都必须产出独立的 `.codex/upstream-sync-status.json` 和 `.codex/upstream-sync-review.md`。Codex 审查时应先阅读对应仓库、基线提交、候选提交、变更文件和失败日志，再决定是否继续下一提交。报告不得包含访问令牌、refresh token、完整 Authorization Header 或完整请求体。


仓库和工作流当前能力、初始化条件及维护命令以 [上游更新维护说明](docs/upstream-updates.md) 为准。CLIProxyAPI 的工作流来自它自己的仓库，本仓库不改变其合并实现，也不把设计目标当作已执行的逐提交测试证据
