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
- 任务顺序：读取最新 release，按提交顺序生成候选，逐提交运行 LiteLLM 与账号池边界测试，记录冲突和失败步骤，只有全部通过才允许 promote
- 保留要求：现有账号池 API、卡片 Key、日志、路由、计费、认证和部署入口保持兼容；新版功能只在通过测试后进入候选

每条任务都必须产出独立的 `.codex/upstream-sync-status.json` 和 `.codex/upstream-sync-review.md`。Codex 审查时应先阅读对应仓库、基线提交、候选提交、变更文件和失败日志，再决定是否继续下一提交。报告不得包含访问令牌、refresh token、完整 Authorization Header 或完整请求体。
