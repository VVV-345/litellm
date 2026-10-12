# 构建与上游兼容工作流

litellm-upstream-sync.yml 负责 LiteLLM 候选及草稿 PR；publish-deployment-images.yml 才负责业务镜像。CLIProxyAPI 的 upstream-sync.yml 在另一个仓库，不在此处复用。更改工作流需检查权限、执行 ref、不可变 artifact ID、失败结论和固定 SHA action

## 修改与更新

先从当前业务分支拉取最新代码，保留未提交修改，在隔离副本验证。官方更新按 [两类上游维护说明](../../account-pool/docs/upstream-updates.md) 执行，模块边界与迁移规则见 [维护手册](../../account-pool/MAINTENANCE.md)

调整本模块时核对调用方、API 字段和对应测试，不顺带重构无关 LiteLLM 原生代码。修改后运行相关测试，记录实际失败/跳过项；涉及运行服务的改动仍需部署手册的真实验收

## 验证入口

从仓库根目录执行，Python 命令使用对应模块锁文件安装的环境

```bash
pytest --noconftest tests/local_testing/test_litellm_upstream_runner.py -q
```
