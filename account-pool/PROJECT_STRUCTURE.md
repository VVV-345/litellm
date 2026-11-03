# 号池管理系统项目结构

## 先说目录在哪里

号池管理系统的主要源码在：

```text
account-pool/
```

页面在：

```text
ui/litellm-dashboard/src/app/(dashboard)/account-pool/page.tsx
ui/litellm-dashboard/src/features/account-pool/
```

LiteLLM 与号池 Manager 之间的接口在：

```text
litellm/proxy/management_endpoints/account_pool_endpoints.py
litellm/proxy/management_endpoints/account_pool_reconciler.py
```

创建一个账号环境后，不会在仓库里长期保存账号文件。运行数据放在 Docker volume 中

```text
account-pool_account_pool_data
account-pool_account_pool_db
account-pool-<环境UUID>-data
```

其中：

- `account-pool_account_pool_data` 保存每个环境生成的 `compose.yaml`
- `account-pool_account_pool_db` 保存号池的 PostgreSQL 数据
- `account-pool-<环境UUID>-data` 保存该账号的 CLIProxyAPI 配置和授权文件

在 Manager 容器中，环境目录是：

```text
/var/lib/litellm-account-pool/environments/<环境UUID>/compose.yaml
```

在账号容器中，账号数据目录是：

```text
/data/config/config.yaml
/data/auths/
```

这些目录由 Docker 管理，不建议直接进入 Docker volume 修改。应该通过号池页面或 API 更新

## 整体调用流程

```text
浏览器号池页面
  -> LiteLLM /account_pool 管理接口
  -> Account Pool Manager
  -> Docker Socket Proxy
  -> 为每个账号创建独立 CLIProxyAPI 容器、网络和数据卷

模型请求
  -> LiteLLM 网关
  -> 对应账号的 CLIProxyAPI
  -> 上游模型服务
```

LiteLLM 负责用户登录和管理员权限。Manager 负责创建、更新和删除账号环境。模型请求不经过 Manager

## 主要入口与目录

只列职责目录，不逐个罗列所有实现文件。新增文件放进已有职责目录，详细修改边界和测试命令见[开发与验证](../docs/handbook/development.md)，完整使用说明见[总手册](../docs/handbook/README.md)

```text
account-pool/
  account_pool/
    app.py, api.py          服务组装及 Manager 接口入口
    batch/                 批量任务模型、持久化、执行服务
    onboarding/            账号接入模型、持久化、任务编排、API
    feedback/              反馈模型、持久化、状态编排、API、附件
    application/           环境生命周期操作及纯计算
    channels/cliproxyapi/   CPA 协议、客户端、供应商适配
    providers/usage/       供应商额度解析
    shared/                结果类型、错误脱敏及密钥基础设施
    service.py             环境操作组装及锁的持有者
    repository.py          环境持久化及数据库连接入口
  tests/
    test_account_pool.py   环境主流程
    account_pool/
      batch/               批量任务测试
      onboarding/          账号接入及接口契约测试
      feedback/            反馈服务及接口测试

litellm/proxy/management_endpoints/account_pool*.py
                           LiteLLM 鉴权、代理与路由集成
tests/test_litellm/proxy/management_endpoints/test_account_pool*.py
                           跨服务契约与集成测试
ui/litellm-dashboard/src/
  app/(dashboard)/account-pool/page.tsx
                           前端路由入口
  features/account-pool/   号池 API、hooks、组件及相邻测试
scripts/feedback.py        反馈命令行入口
docs/handbook/             使用、开发、部署及恢复说明
```

配置和入口保留在原位，`account_pool.feedback` 继续导出服务及契约，`quota`、`provider_quota` 等既有兼容入口保留。未在图中展开的模块仍在原位，后续按职责逐批整理

## 创建账号时发生什么

1. 页面调用 LiteLLM 的 `/account_pool/environments`
2. LiteLLM 检查当前用户是不是管理员
3. LiteLLM 使用 `ACCOUNT_POOL_MANAGER_TOKEN` 请求 Manager
4. Manager 生成不可变的环境 UUID
5. Manager 在 PostgreSQL 保存环境状态
6. Manager 创建 `account-pool-<UUID>-data` 数据卷
7. Manager 生成账号专用的 Compose 和 CLIProxyAPI 配置
8. Manager 启动独立 CLIProxyAPI 容器和网络
9. 用户通过 SSH 隧道完成 OAuth 授权
10. Manager 检查模型、额度和健康状态
11. LiteLLM 定时把可用账号同步为模型部署
12. 后续模型请求由 LiteLLM 直接发到对应账号容器

## 以后修改功能应该去哪里

| 要修改的内容 | 主要位置 |
|---|---|
| 页面路由与编排 | `ui/litellm-dashboard/src/app/(dashboard)/account-pool/page.tsx` |
| 卡片、创建及设置窗口 | `ui/litellm-dashboard/src/features/account-pool/components/cards/` |
| 页面请求和返回类型 | `ui/litellm-dashboard/src/features/account-pool/api/` |
| 批量任务 | `account-pool/account_pool/batch/` |
| 账号导入与接入任务 | `account-pool/account_pool/onboarding/` |
| 反馈、附件及反馈凭证 | `account-pool/account_pool/feedback/` |
| 创建、授权、更新、删除逻辑 | `account-pool/account_pool/service.py` |
| Manager HTTP 接口 | `account-pool/account_pool/api.py` |
| LiteLLM 对外接口 | `litellm/proxy/management_endpoints/account_pool_endpoints.py` |
| 账号状态和字段 | `account-pool/account_pool/domain.py` |
| 数据库表和读写 | `account-pool/account_pool/repository.py` |
| Docker 容器、网络和卷 | `account-pool/account_pool/compose_renderer.py`、`account-pool/account_pool/compose_runtime.py` |
| CLIProxyAPI 镜像版本 | `account-pool/account_pool/config.py` |
| OAuth 和模型发现 | `account-pool/account_pool/channels/cliproxyapi/client.py` |
| 渠道注册与拒绝 | `account-pool/account_pool/channels/registry.py`、`account-pool/account_pool/channels/base.py` |
| 供应商端点和授权流 | `account-pool/account_pool/channels/cliproxyapi/suppliers/` |
| 额度和冷却规则 | `account-pool/account_pool/quota.py` |
| LiteLLM 路由同步 | `litellm/proxy/management_endpoints/account_pool_reconciler.py` |
| 部署参数 | `account-pool/.env`、`docker-compose.manager.yml` |
| Manager 测试 | `account-pool/tests/` |
| LiteLLM 接口测试 | `tests/test_litellm/proxy/management_endpoints/` |

## 两个 `.env` 的区别

仓库根目录的 `.env` 给 LiteLLM 使用，主要包括：

```text
LITELLM_MASTER_KEY
ACCOUNT_POOL_MANAGER_TOKEN
模型服务密钥
```

`account-pool/.env` 给号池 Manager 使用，主要包括：

```text
ACCOUNT_POOL_DATABASE_URL
ACCOUNT_POOL_DB_PASSWORD
ACCOUNT_POOL_MANAGER_TOKEN
ACCOUNT_POOL_SECRET_SEED
ACCOUNT_POOL_SSH_HOST
ACCOUNT_POOL_SSH_USER
```

两个文件里的 `ACCOUNT_POOL_MANAGER_TOKEN` 必须完全相同。它是 LiteLLM 与 Manager 之间的内部密码

`LITELLM_MASTER_KEY` 是 LiteLLM 管理员密码。它与 `ACCOUNT_POOL_MANAGER_TOKEN` 用途不同，不能混用

`ACCOUNT_POOL_SECRET_SEED` 用于生成每个账号自己的内部密钥。部署后不要随意更换，否则现有账号环境的密钥会改变

## Linux 部署位置

推荐把整个仓库放在一个固定目录，例如：

```text
/opt/litellm/
```

对应位置为：

```text
/opt/litellm/.env
/opt/litellm/docker-compose.yml
/opt/litellm/account-pool/.env
/opt/litellm/account-pool/docker-compose.manager.yml
```

启动顺序：

```bash
cd /opt/litellm
docker compose up -d

cd /opt/litellm/account-pool
docker compose -f docker-compose.manager.yml up -d --build
```

检查状态：

```bash
docker ps
curl http://127.0.0.1:4000/health/readiness
curl http://127.0.0.1:8091/health
```

## 更新时的注意事项

更新代码前先备份两个数据库卷和所有 `account-pool-<UUID>-data` 卷

不要删除这些目录或数据卷：

```text
account-pool_account_pool_db
account-pool_account_pool_data
account-pool-<环境UUID>-data
```

不要把 `.env`、OAuth 文件、主密钥或 Manager Token 提交到 Git

更新后先启动 LiteLLM，再启动 Account Pool Manager，最后检查两个健康接口

不要直接编辑自动生成的 `compose.yaml` 或账号数据卷。Manager 下一次操作可能覆盖这些内容

## 不属于源码的目录

下面这些是本地生成内容，不需要提交，也不应该当作号池源码维护：

```text
account-pool/.venv/
account-pool/**/__pycache__/
ui/litellm-dashboard/.next/
ui/litellm-dashboard/out/
litellm-rust/target/
.claude/worktrees/
```


## 上游更新与模块说明

两类上游的仓库、分支、配置、测试和 PR 边界见 [上游维护说明](docs/upstream-updates.md)。Manager 应用、通道、供应商、共享模块及号池前端各功能目录的 README 记录各自职责和验证入口；部署与浏览器工作器也有独立说明
