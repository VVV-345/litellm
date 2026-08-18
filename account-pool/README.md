# LiteLLM Account Pool

Account Pool 是 LiteLLM 旁路运行的账号级调度服务。LiteLLM 继续负责供应商协议、加密保存 Key 和 Deployment；
Account Pool 负责渠道配置、共享并发、租约、额度快照、健康状态和模型到具体 Deployment 的选择

## 一体启动

从仓库根目录执行：

```powershell
# 首次启动前在 .env 中设置随机强令牌，两个服务会读取同一个值。
$env:ACCOUNT_POOL_INTERNAL_TOKEN = "replace-with-a-random-service-token"
docker compose up --build
```

启动后：

- 号池调度器 UI：`http://127.0.0.1:4100/`
- LiteLLM Admin UI：`http://127.0.0.1:4000/ui/`
- 号池健康检查：`http://127.0.0.1:4100/healthz`
- Redis 仅在 Compose 内网提供服务

进入 4100 调度器 UI，使用 LiteLLM 管理令牌登录，即可配置渠道、调度策略和实时路由。渠道创建仍复用
LiteLLM `/model/new` 的 Deployment 管理链路。LiteLLM 与号池必须使用相同的 `ACCOUNT_POOL_INTERNAL_TOKEN`；
Compose 会把 `.env` 中的值同步给两个服务，缺少该值时拒绝启动

`account-pool/config/accounts.yaml` 是可写的非敏感配置。API Key 通过 LiteLLM `/model/new` 写入其数据库，
不会进入该 YAML、Redis、日志或管理 API 响应

## 渠道模块

每个供应商位于独立目录：

```text
account_pool/provider_services/
├── contracts.py          # 所有渠道实现的统一协议
├── registry.py           # 模块注册和按 provider_id 分发
└── glm/
    ├── manifest.py       # 渠道标识、默认 URL 和能力声明
    ├── schemas.py        # 上游响应模型
    ├── client.py         # 官方 HTTP 请求和 URL 安全限制
    └── service.py        # 转换为号池统一结果
```

新增渠道时复制同一职责边界，并在 `app.py` 的 `ProviderServiceRegistry` 注册。公共管理层不按渠道名写分支，
各模块可以并行开发，也不会把某个供应商的余额、套餐或价格规则带到其他供应商

## GLM 官方国内平台

首个模块使用智谱官方国内开发平台：`https://open.bigmodel.cn/api/paas/v4`

| 能力 | 当前状态 | 实现 |
| --- | --- | --- |
| URL 与 Key 校验 | 支持 | `GET /models`，Bearer Key |
| 当前 Key 可见模型 | 支持 | 解析并去重官方模型列表 |
| 自定义模型名 | 支持 | UI 可输入列表外模型并回车 |
| 账户下 Key 列表 | 不支持 | 官方推理 API 无公开管理接口 |
| 余额、套餐、周/月限额 | 不支持 | 官方推理 API 无稳定公开查询接口 |
| 账户实际价格 | 不支持 | 模型接口不返回分组折扣、资源包或成交价 |

GLM 模块只允许 HTTPS、`open.bigmodel.cn` 和固定 `/api/paas/v4` 路径，且禁止重定向，避免把 Key
发送到用户输入的任意地址。校验结果只返回 Key 的 SHA-256 指纹前缀

LiteLLM 的 provider 名称仍为 `zai`，但创建 Deployment 时会显式保存国内 API Base，因此不会使用
LiteLLM 的国际默认地址 `https://api.z.ai/api/paas/v4`

## 本地开发

不使用 Compose 时，需要单独启动服务：

```powershell
$env:PYTHONPATH = "account-pool"
$env:ACCOUNT_POOL_STORE = "memory"
$env:ACCOUNT_POOL_CONFIG = "account-pool/config/accounts.yaml"
$env:ACCOUNT_POOL_LITELLM_URL = "http://127.0.0.1:4000"
$env:ACCOUNT_POOL_LITELLM_ADMIN_KEY = "your-litellm-master-key"
$env:ACCOUNT_POOL_INTERNAL_TOKEN = "your-service-token"
.\.venv\Scripts\python.exe -m uvicorn account_pool.app:app --host 127.0.0.1 --port 4100
```

所有 `/api/*` 和 `/internal/*` 接口都要求 `X-Account-Pool-Token`；未配置服务令牌时接口会拒绝服务，
不会退化为无认证访问。`/healthz` 保持无认证，供容器健康检查使用

客户端若需要账号池调度，应访问 Account Pool 的 `/v1/*` 网关；直连 LiteLLM 会绕过账号级并发和额度约束，
生产网络中应限制 LiteLLM Proxy 的直接访问
