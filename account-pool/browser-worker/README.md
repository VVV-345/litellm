# 浏览器授权工作器

entrypoint.py 和 Dockerfile 提供受控授权浏览器运行环境；不是公共浏览器服务，不承载 Manager GitHub Token。更新时验证授权建立、结束、超时、凭据回收和浏览器镜像构建

## 修改与更新

先从当前业务分支拉取最新代码，保留未提交修改，在隔离副本验证。官方更新按 [两类上游维护说明](../docs/upstream-updates.md) 执行，模块边界与迁移规则见 [维护手册](../MAINTENANCE.md)

调整本模块时核对调用方、API 字段和对应测试，不顺带重构无关 LiteLLM 原生代码。修改后运行相关测试，记录实际失败/跳过项；涉及运行服务的改动仍需部署手册的真实验收

## 验证入口

从仓库根目录执行，Python 命令使用对应模块锁文件安装的环境

```bash
docker build -f account-pool/browser-worker/Dockerfile -t account-pool-browser-check account-pool
```
