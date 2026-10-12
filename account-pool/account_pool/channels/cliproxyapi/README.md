# CLIProxyAPI 协议

protocol.py 和 transport.py 封装管理协议及传输；provider_quota.py 与 quota_state.py 负责额度获取和缓存合并。升级 CLIProxyAPI 时重点核对 OAuth、模型、认证文件及额度字段

## 修改与更新

先从当前业务分支拉取最新代码，保留未提交修改，在隔离副本验证。官方更新按 [两类上游维护说明](../../../docs/upstream-updates.md) 执行，模块边界与迁移规则见 [维护手册](../../../MAINTENANCE.md)

调整本模块时核对调用方、API 字段和对应测试，不顺带重构无关 LiteLLM 原生代码。修改后运行相关测试，记录实际失败/跳过项；涉及运行服务的改动仍需部署手册的真实验收

## 验证入口

从仓库根目录执行，Python 命令使用对应模块锁文件安装的环境

```bash
pytest account-pool/tests
```
