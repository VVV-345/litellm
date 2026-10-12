# 通道接口

base.py、registry.py 声明通道契约与注册；openai_compatible.py 和 cliproxyapi/ 实现协议。通道差异不要进入供应商额度纯解析函数

## 修改与更新

先从当前业务分支拉取最新代码，保留未提交修改，在隔离副本验证。官方更新按 [两类上游维护说明](../../docs/upstream-updates.md) 执行，模块边界与迁移规则见 [维护手册](../../MAINTENANCE.md)

调整本模块时核对调用方、API 字段和对应测试，不顺带重构无关 LiteLLM 原生代码。修改后运行相关测试，记录实际失败/跳过项；涉及运行服务的改动仍需部署手册的真实验收

## 验证入口

从仓库根目录执行，Python 命令使用对应模块锁文件安装的环境

```bash
pytest account-pool/tests
```
