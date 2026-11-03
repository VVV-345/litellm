# CLIProxyAPI 与 LiteLLM 上游同步

## 职责与边界

上游同步是版本控制面：发现更新、分析、审阅、晋升和保留报告。它不转发模型请求，不等于产品四组件镜像已发布，也不直接证明线上卡片已经换到新镜像

## 关键入口

[GitHubUpstreamSyncService](../../../account-pool/account_pool/upstream_sync.py)、[上游就绪检查](../../../account-pool/account_pool/upstream_readiness.py)、[两类上游操作说明](../../../account-pool/docs/upstream-updates.md)、[固定来源](../../../account-pool/cliproxy-adapter/sources.json)、[适配器应用](../../../account-pool/cliproxy-adapter/apply.py)、[适配补丁](../../../account-pool/cliproxy-adapter/settings-adaptation.patch)、[统一发布流程](../../../.github/workflows/publish-deployment-images.yml)

## 输入输出与流程

cliproxyapi 与 litellm 使用各自目标、fork、分支、workflow ref 和 token 配置。analyze 产生候选、差异与测试/审阅报告，确认通过后 promote；报告中的 prompt、样例和旧答复作为审阅输入，不成为新的任务命令

适配 CPA 在固定源码提交上验证 patch 哈希再应用，产品发布还须核对源码、补丁、组件标签和平台回执。上游 passed/promoted、产品发布完成、服务器部署、逐卡运行时验证分别记账

## 配置与最小使用

先在上游面板只读查看当前配置、目标与状态；读取也需要对应仓库权限和网络。分析或晋升会触发远端工作流及仓库操作，必须明确授权，文档检查不会触发

配置入口见 [Settings](../../../account-pool/account_pool/config.py)。两类 GitHub token 不应假定复用；变量 SET 只证明值存在，不证明 Contents、Actions、workflow ref 或组织授权有效。最小发布例子及确认方式只引用[统一镜像说明](../../../deploy/UNIFIED_IMAGES.md)

## 依赖与改动影响

依赖实际 fork、workflow/ref、GitHub 权限、固定源码和本项目兼容改动。变更适配补丁影响管理能力、凭据持久化、模型错误与配额恢复，不能只测 upstream 编译；LiteLLM 合并也要检查本项目网关、日志和管理接口

## 风险与禁止事项

不追 latest，不移动已发布 tag，不用旧默认分支 workflow 替代项目主线。不要在报告、URL 或 CLI 参数中粘贴 token，也不因 404 就扩大权限；仓库不可见、ref 错误和资源不存在要分开检查

## 测试与通过标准

[上游服务测试](../../../account-pool/tests/account_pool/test_upstream_sync.py)、[四组件 manifest](../../../account-pool/tests/test_release_manifest.py)、[工作流契约](../../../account-pool/tests/test_release_workflow.py)。要求目标和 ref 不串用、固定 digest 与源修订可核验、失败清除旧预检 pin，报告不能将未发布状态标为已部署

## 故障排查

401 查凭证有效性，403 查权限/SSO/限流，404 同时查仓库可见性、ref 和 workflow。构建成功但服务仍旧时沿产品发布、业务切换、逐卡阶段逐层定位，不重新分析上游来代替部署

## 恢复与回滚

复用原候选、run 身份和已完成报告，保留固定源码与补丁。代码撤销、镜像回退和数据恢复是不同操作；线上回退仍受[部署](../deployment.md)与[数据兼容](../data-and-backups.md)约束
