# 数据、配置与备份恢复边界

## 先问恢复哪一种数据

| 对象 | 所属与主要位置 | 恢复边界 |
| --- | --- | --- |
| LiteLLM 业务数据 | 原生数据库与 [schema.prisma](../../schema.prisma) | 配套快照、目标结构和业务写入损失单独确认 |
| Manager 元数据 | 独立 PostgreSQL，[repository](../../account-pool/account_pool/repository.py)及各领域 repository | 卡片状态、配置版本、归属、租约、任务和门禁不是容器文件 |
| CPA 配置与真实凭据 | 每卡原命名数据卷，容器内 `/data` | 当前凭据可能已轮换，不能因回退镜像而回放旧卷 |
| 内部身份状态 | 适配器使用的 `.codex-managed-identity` 目录 | 不是另一张账号认证文件，不应被导入、转移或计入真实凭据数量 |
| 镜像与 compose | release-worker 备份及 Manager 单卡受保护备份 | 恢复镜像不恢复数据库或认证数据 |
| 日志、完整日志与前端缓存 | 各自存储和保留策略 | 页面刷新、删卡或镜像回退不代表日志已删除或还原 |

卷名与目录按不可变卡片 UUID 生成，显示名不参与路径或身份。实际挂载应核对 Docker 元数据；不根据页面名称推断卷归属。生产配置入口、容器和备份目录按[更新手册](../../deploy/UPDATE_RUNBOOK.md)现场确认

## 必须一起保护的秘密

保留原 `ACCOUNT_POOL_SECRET_SEED`、Manager token、业务密钥、数据库凭据及受限 Docker 登录配置的归属与权限。更换 seed 会影响已有密文或派生身份；不能重新生成一套值让服务“先启动”

秘密不能出现在示例、日志、截图、issue、命令行参数或 Git 中。备份只在权限受限位置保存，认证卷归档包含真实 Token；备份校验输出只保留路径、大小和摘要，不输出文件内容

## 备份有效与可回退是两个检查

[数据库备份实现](../../account-pool/account_pool/release_database.py)负责两库快照与恢复流程，[发布兼容检查](../../account-pool/account_pool/release_compatibility.py)负责代码中的结构、持久化配置及受审阅的单向升级边界

文件存在不等于可恢复，仍须验证大小、哈希、配套镜像身份及试还原。新增表即使旧程序能忽略，也不意味着旧程序能遵守新维护门禁；活动逐卡升级会继续阻止旧 Manager 覆盖

镜像切换失败的默认补偿不回放数据库，不恢复旧认证文件。`restore_data` 属于额外的数据库恢复操作，必须确认具体快照及数据损失范围，不能作为普通镜像回滚的隐藏步骤

## 本地检查与恢复

源码只读核对可以从仓库根目录执行：

```sh
git diff -- schema.prisma account-pool/account_pool/repository.py account-pool/account_pool/runtime_rollout_repository.py
```

预期看到本次结构变化或空差异，不能凭空差异宣称线上实际 schema 一致。真实快照、卷、权限和恢复能力需要独立授权的环境验收，迁移步骤遵守[迁移手册](../../deploy/MIGRATION.md)和[发布机制](../../deploy/RELEASES.md)

`.local` 中用于本地源代码副本验收的 `ROLLBACK.sh` 只用于其声明的源码副本。它不是生产数据库、认证卷或故障卡的恢复入口；不要用它覆盖线上运行文件来绕过维护门禁
