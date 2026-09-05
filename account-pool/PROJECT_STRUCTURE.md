# 号池管理系统项目结构

## 先说目录在哪里

号池管理系统的主要源码在：

```text
account-pool/
```

页面在：

```text
ui/litellm-dashboard/src/app/(dashboard)/account-pool/
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

## 完整结构

```text
litellm/
├── .env                                      本机 LiteLLM 配置和密钥，不提交 Git
├── docker-compose.yml                        LiteLLM、主数据库、Prometheus 和共享网络
├── docker-compose.override.yml               使用本地构建的 litellm:local 镜像
├── prometheus.yml                            Prometheus 采集配置
│
├── account-pool/                             号池 Manager 主目录
│   ├── .env                                  Manager 配置和密钥，不提交 Git
│   ├── .env.example                          Manager 配置模板
│   ├── AGENTS.md                             号池开发规则和安全边界
│   ├── README.md                             号池架构、部署和使用说明
│   ├── PROJECT_STRUCTURE.md                  本文档
│   ├── PLAN.md                               早期实现计划和需求记录
│   ├── Dockerfile                            Manager 镜像构建文件
│   ├── docker-compose.manager.yml            Manager、号池数据库和 Socket Proxy
│   ├── pyproject.toml                        Python 包和依赖配置
│   ├── uv.lock                               锁定的 Python 依赖版本
│   │
│   ├── account_pool/                         Manager 后端源码
│   │   ├── __init__.py                       Python 包入口
│   │   ├── app.py                            创建 FastAPI 应用并组装所有服务
│   │   ├── api.py                            Manager API、健康检查和 OAuth 回调
│   │   ├── service.py                        创建、授权、更新、删除和恢复流程
│   │   ├── domain.py                         环境状态、请求和数据模型
│   │   ├── contracts.py                      对外响应结构
│   │   ├── ports.py                          存储、Docker 和上游客户端接口
│   │   ├── repository.py                     PostgreSQL 表和数据读写
│   │   ├── config.py                         环境变量读取和安全校验
│   │   ├── secrets.py                        按环境派生管理密钥和网关密钥
│   │   ├── compose_renderer.py               生成账号的 Compose 和 CLIProxyAPI 配置
│   │   ├── compose_runtime.py                执行 Docker、网络和数据卷操作
│   │   ├── compose.py                        Compose 功能的统一导出入口
│   │   ├── channels/                         渠道抽象层
│   │   │   ├── base.py                       渠道定义和拒绝语义
│   │   │   ├── registry.py                   渠道注册表（CLIProxyAPI、FreeBuff2API 均正式）
│   │   │   ├── cliproxyapi/                  CLIProxyAPI 渠道实现
│   │   │   │   ├── channel.py                渠道组合根（运行时、客户端、供应商）
│   │   │   │   ├── client.py                 CLIProxyAPI 管理协议客户端
│   │   │   │   ├── runtime.py                Docker 渲染与执行封装
│   │   │   │   └── suppliers/                五个供应商的静态契约
│   │   │   │       ├── base.py               SupplierDefinition 数据结构
│   │   │   │       ├── registry.py           供应商注册表
│   │   │   │       ├── openai_codex.py       Codex（浏览器授权，回调 1455）
│   │   │   │       ├── anthropic_claude.py   Claude（浏览器授权，回调 54545）
│   │   │   │       ├── google_antigravity.py Antigravity（浏览器授权，回调 51121）
│   │   │   │       ├── kimi.py               Kimi（设备码授权）
│   │   │   │       └── xai.py                xAI（设备码授权）
│   │   │   └── freebuff2api/                 FreeBuff2API 渠道实现
│   │   │       ├── channel.py                渠道组合根与授权状态编解码
│   │   │       ├── client.py                 codebuff CLI 授权客户端与容器管理
│   │   │       └── suppliers.py              FreeBuff 供应商静态契约
│   │   ├── cliproxy.py                       旧调用方的兼容导出层
│   │   ├── quota.py                          解析额度和冷却时间
│   │   ├── cleanup.py                        删除环境时记录清理进度
│   │   ├── error_safety.py                   隐藏敏感错误信息
│   │   └── result.py                         成功和失败结果类型
│   │
│   └── tests/                                Manager 测试
│       ├── conftest.py                       测试公共配置
│       ├── test_account_pool.py              创建、授权、更新和删除主流程
│       └── account_pool/
│           ├── test_cleanup.py               清理流程测试
│           ├── test_compose_renderer.py      Compose 生成测试
│           ├── test_contracts.py             接口结构测试
│           ├── test_error_safety.py          敏感信息保护测试
│           ├── test_quota.py                 额度解析测试
│           └── test_result.py                结果类型测试
│
├── litellm/proxy/management_endpoints/
│   ├── account_pool_endpoints.py             Dashboard 到 Manager 的代理接口
│   └── account_pool_reconciler.py            把可用账号同步成 LiteLLM 模型部署
│
├── tests/test_litellm/proxy/management_endpoints/
│   ├── test_account_pool_endpoints.py        LiteLLM 号池接口测试
│   └── test_account_pool_reconciler.py       模型部署同步测试
│
└── ui/litellm-dashboard/src/app/(dashboard)/account-pool/
    ├── page.tsx                              号池页面入口
    ├── AccountPoolCard.tsx                   单个账号卡片
    ├── AccountPoolCreateDialog.tsx           创建账号窗口
    ├── AccountPoolConfigDialog.tsx           修改账号配置窗口
    ├── AccountPoolApi.ts                     调用 LiteLLM 号池接口
    ├── AccountPoolTypes.ts                   前端数据类型
    ├── AccountPoolPermissions.ts             页面权限判断
    ├── AccountPoolValidation.ts              表单校验
    ├── AccountPoolFormatters.ts              状态、额度和时间显示
    ├── AccountPoolFormatters.test.ts          显示格式测试
    ├── accountPoolSelectors.ts               页面数据筛选
    ├── useAccountPoolQuery.ts                 查询账号列表
    └── useAccountPoolMutations.ts             创建、修改、授权和删除操作
```

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
| 页面布局、按钮、卡片 | `ui/litellm-dashboard/src/app/(dashboard)/account-pool/` |
| 创建账号窗口 | `AccountPoolCreateDialog.tsx` |
| 账号设置窗口 | `AccountPoolConfigDialog.tsx` |
| 页面请求和返回类型 | `AccountPoolApi.ts`、`AccountPoolTypes.ts` |
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
