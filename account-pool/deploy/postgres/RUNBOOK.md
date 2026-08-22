<!-- 本文件说明 Account Pool PostgreSQL 加密备份、校验和恢复演练流程。 -->

# PostgreSQL 备份恢复手册

该工具备份整个 LiteLLM PostgreSQL 数据库，因为渠道目录、Deployment、审计和调度恢复数据位于同一数据库。归档可能
包含加密凭证和业务数据，因此 `pg_dump` 输出直接进入 AES-256-GCM 流式加密，不产生常规明文备份文件

## 前置条件

- 执行环境安装与数据库主版本兼容的 `pg_dump` 和 `pg_restore`
- `DATABASE_URL` 指向备份源或恢复目标，密码不会放入子进程命令行
- `ACCOUNT_POOL_BACKUP_KEY` 是 URL-safe Base64 编码的 32 字节随机密钥
- `ACCOUNT_POOL_BACKUP_KEY_ID` 是可公开记录的密钥版本，例如 `backup-2026-q3`
- 归档目录位于受控持久化存储，并有独立的异地复制和访问控制

生成新密钥：

```powershell
$env:ACCOUNT_POOL_BACKUP_KEY = python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip('='))"
$env:ACCOUNT_POOL_BACKUP_KEY_ID = "backup-2026-q3"
```

密钥必须进入部署密钥管理系统，不得与归档存放在一起，也不得提交到仓库

## 创建备份

```powershell
$env:DATABASE_URL = "postgresql://user:password@db-host:5432/litellm"
account-pool-postgres backup --path D:\secure-backups\litellm
```

成功时输出 `created`、归档目录和清单。归档目录包含：

- `database.dump.aesgcm`：加密后的 PostgreSQL custom-format dump
- `manifest.json`：归档 ID、时间、密钥版本、认证参数、文件大小及明文和密文 SHA-256

工具不输出 `pg_dump` 标准错误正文，避免连接信息进入日志。失败只返回稳定错误码

## 定期校验

校验不需要数据库连接，但需要对应版本密钥：

```powershell
account-pool-postgres verify --path D:\secure-backups\litellm\postgres-20260822T120000Z-1234abcd
```

校验会验证清单、密文摘要、AES-GCM 认证标签、解密后的明文摘要和文件大小，不保留解密文件

## 恢复演练

先创建空的演练数据库，并把 `DATABASE_URL` 切换到该数据库。恢复命令会执行 `pg_restore --clean --if-exists`，会删除
目标数据库中与归档对象同名的现有对象，不应直接对生产数据库执行

```powershell
$env:DATABASE_URL = "postgresql://user:password@db-host:5432/litellm_restore_drill"
account-pool-postgres restore `
  --path D:\secure-backups\litellm\postgres-20260822T120000Z-1234abcd `
  --confirm postgres-20260822T120000Z-1234abcd
```

恢复前依次执行密文与明文完整性校验、`pg_restore --list` 格式校验，只有归档 ID 确认完全匹配才会连接目标数据库。
恢复期间产生的临时明文文件使用仅当前用户权限创建，并在成功或失败后删除

演练完成后应检查迁移表、Account Pool 渠道数量、解析运行数量、路由策略、审计事件以及 LiteLLM Deployment 数量，
再启动一套隔离的 Account Pool 和 LiteLLM 实例执行只读查询。演练数据库验证完毕后由运维按环境流程销毁
