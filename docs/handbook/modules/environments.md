# 卡片与隔离环境

## 职责与边界

Manager 管理卡片元数据、生命周期、配置对账及 Compose 操作。CLIProxyAPI 卡片使用独立项目、网络和原数据卷；兼容上游卡片不能机械套用 CPA 容器维护命令。显示名可变，卡片 UUID 不变

## 关键入口

[服务装配](../../../account-pool/account_pool/app.py)、[EnvironmentService](../../../account-pool/account_pool/service.py)、[领域记录](../../../account-pool/account_pool/domain.py)、[环境操作](../../../account-pool/account_pool/application/environments/)、[ComposeRuntime](../../../account-pool/account_pool/compose_runtime.py)、[兼容上游](../../../account-pool/account_pool/application/)

## 输入输出与流程

创建请求、供应商、授权方式、代理和并发配置进入服务，输出卡片记录及授权状态。创建、授权、模型发现、配置验证和 ready 是不同阶段；删除也有持久化清理进度，失败后按原 ID 重试

| 观测 | 含义 | 不足以证明 |
| --- | --- | --- |
| `enabled` | 用户启停意愿 | 容器已启动或请求可用 |
| `status` | Manager 生命周期判断 | 镜像已更新或上游认证有效 |
| `configuration_pending` | 配置同步未完成 | 改一处标记即可恢复 |
| desired / observed 配置版本 | 期望和实际确认的配置版本 | 两者不同仍可安全维护 |
| Docker running / image / Mounts | 实际进程、镜像和挂载 | 原路由真实模型调用成功 |

手动停用优先于自动恢复；冷却时间过去后仍需要相应健康验证。不能用 disabled 再 enabled 的切换代替对原 ERROR 的恢复验证

## 配置与最小使用

启动依赖与环境样例见[开发手册](../development.md)。最小使用从 Dashboard 的号池创建一张测试卡开始，完成授权并核对模型、配置版本及状态；这是**有状态操作**，须使用独立测试数据，本文档检查不会执行

只读代码检查可在仓库根目录执行 `git diff -- account-pool/account_pool/domain.py account-pool/account_pool/service.py`，预期只显示本次修改范围，不会启动容器

## 依赖与改动影响

生命周期依赖仓库、凭据归属、策略、代理和运行时。状态或默认值变化会影响网关准入、UI、批量任务及恢复判断，应同时检查管理接口和原路由调用方，不能只修改卡片显示

## 风险与禁止事项

不得根据显示名拼路径，不开放每卡宿主端口，不把 Docker Socket 挂到公共 LiteLLM 服务。删除可能清理认证卷，不能用删卡重建作为普通升级办法。升级门禁只挡新租约，不改变用户启停或额度

## 测试与通过标准

[环境及服务测试](../../../account-pool/tests/test_account_pool.py)、[清理](../../../account-pool/tests/account_pool/test_cleanup.py)、[Compose](../../../account-pool/tests/account_pool/test_compose_renderer.py)、[批量任务](../../../account-pool/tests/account_pool/batch/test_batch_service.py)。通过标准是状态转换、补偿和重试身份保持一致，独立网络和卷归属正确，用户停用不被自动覆盖

## 故障排查

pending 或 ERROR 时，先核对期望/实际版本及配置错误，再查容器镜像、挂载和管理接口。容器 running 但卡片不可用时继续查凭据、模型、额度和准入，不把卡片强写成 ready。创建或删除失败先读取已有进度，避免产生第二个环境

## 恢复与回滚

仅恢复本次失败步骤，复用原 ID、锁和清理进度。运行时升级按[逐卡流程](../deployment.md)，原状态保持；数据库和认证数据的恢复按[数据边界](../data-and-backups.md)单独确认
