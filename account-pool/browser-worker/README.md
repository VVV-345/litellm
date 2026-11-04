# 浏览器授权工作器

entrypoint.py 和 Dockerfile 提供受控授权浏览器运行环境；不是公共浏览器服务，不承载 Manager GitHub Token。更新时验证授权建立、结束、超时、凭据回收和浏览器镜像构建

## 修改与更新

先从当前业务分支拉取最新代码，保留未提交修改，在隔离副本验证。官方更新按 [两类上游维护说明](../docs/upstream-updates.md) 执行，模块边界与迁移规则见 [维护手册](../MAINTENANCE.md)

调整本模块时核对调用方、API 字段和对应测试，不顺带重构无关 LiteLLM 原生代码。修改后运行相关测试，记录实际失败/跳过项；涉及运行服务的改动仍需部署手册的真实验收

## 验证入口

从仓库根目录执行，Python 命令使用对应模块锁文件安装的环境

```bash
docker build -f account-pool/browser-worker/Dockerfile -t account-pool-browser-check account-pool
docker run --rm --network none account-pool-browser-check self-check
docker run --rm --network none account-pool-browser-check generate-profile en-US
node --test account-pool/browser-worker/automation/*.test.mjs
```

镜像预装固定版本 Camoufox SDK、Linux 浏览器、字体、Xvfb 和 noVNC，不在登录时下载资源。构建需要访问固定的镜像源、PyPI、npm 和 GitHub release；代理不可达或下载校验失败时应停止，不跳过校验。SDK 指纹生成也需要显示服务，入口会在离线自检和指纹生成时启动 Xvfb

## 授权自动化

配置、2FA、接管与重试流程统一见[凭据手册](../../docs/handbook/modules/credentials.md#可选授权自动化与-2fa)，实际验收范围与已知基线失败见 [2026-11-04 验收记录](../docs/oauth-automation-acceptance-20261104.md)
