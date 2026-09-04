# 号池多渠道与 CLIProxyAPI 多供应商设计

## 目标

号池改为两层结构：渠道负责运行反代程序，供应商负责该程序内部的账号授权、凭据识别、模型发现和额度解释

本阶段正式实现 CLIProxyAPI 渠道，并按固定版本 v7.2.146 的真实接口支持 OpenAI Codex、Anthropic Claude、Google Antigravity、Kimi 和 xAI。FreeBuff2API 只提供不可创建的占位定义，不下载源码、不构建镜像、不启动容器，也不生成 LiteLLM 路由

现有 OpenAI Codex 账号必须继续使用原来的 UUID、Compose 项目、容器别名、网络、数据卷、授权文件和 LiteLLM Deployment ID，不执行数据重写或资源重建

## 已确认的上游接口

CLIProxyAPI 固定镜像版本为 v7.2.146。供应商接入只使用该版本源码中真实存在的接口

| 供应商 | 授权方式 | 启动接口 | 凭据供应商键 | 模型排除键 |
|---|---|---|---|---|
| OpenAI Codex | 浏览器 OAuth | `/v0/management/codex-auth-url` | `codex` | `codex` |
| Anthropic Claude | 浏览器 OAuth | `/v0/management/anthropic-auth-url` | `claude` | `claude` |
| Google Antigravity | 浏览器 OAuth | `/v0/management/antigravity-auth-url` | `antigravity` | `antigravity` |
| Kimi | 设备码 | `/v0/management/kimi-auth-url` | `kimi` | `kimi` |
| xAI | 设备码 | `/v0/management/xai-auth-url` | `xai` | `xai` |

所有已实现供应商共用真实接口：

- `/v0/management/get-auth-status` 查询授权状态
- `/v0/management/oauth-callback` 提交浏览器 OAuth 回调
- `/v0/management/auth-files` 查找授权凭据和被动额度观测
- `/v0/management/auth-files/models` 获取该凭据实际可用的模型
- `/v0/management/auth-files/status` 启停凭据
- `/v0/management/oauth-excluded-models` 设置该供应商排除的模型
- `/v0/management/proxy-url` 设置出站代理
- `/v1/models` 验证 OpenAI 兼容数据面

当前代码调用的 `/v0/management/concurrency-limit` 在固定版本中不存在。本次删除该调用，并继续由 LiteLLM Deployment 的 `max_parallel_requests` 作为环境级并发限制

## 领域模型与兼容性

新增 `ChannelKind`：

```text
cliproxyapi
freebuff2api
```

新增 `SupplierKind`：

```text
openai_codex
anthropic_claude
google_antigravity
kimi
xai
```

`EnvironmentRecord`、公开的 `EnvironmentView` 和创建请求增加 `channel` 与 `supplier`

历史 JSONB 记录没有新字段时，Pydantic 默认解释为：

```text
channel = cliproxyapi
supplier = openai_codex
```

现有 `provider = openai` 字段暂时保留，含义收敛为 LiteLLM 使用的 OpenAI 兼容数据面协议。它不再承担渠道或上游供应商身份。这样旧记录和旧路由格式无需迁移

渠道和供应商只能在创建时选择，创建后不可修改。FreeBuff2API 与任何供应商组合都在请求校验阶段返回明确的“尚未实现”，不会先保存错误记录或调用 Docker

未知渠道、未知供应商，以及不属于所选渠道的供应商组合全部拒绝，不使用回退值。只有缺少新字段的历史记录使用兼容默认值

## 组件边界

新增目录：

```text
account-pool/account_pool/channels/
├── base.py
├── registry.py
├── cliproxyapi/
│   ├── channel.py
│   ├── client.py
│   ├── runtime.py
│   └── suppliers/
│       ├── base.py
│       ├── registry.py
│       ├── openai_codex.py
│       ├── anthropic_claude.py
│       ├── google_antigravity.py
│       ├── kimi.py
│       └── xai.py
└── freebuff2api/
    └── placeholder.py
```

`ChannelRegistry` 是静态白名单。它按持久化的 `channel` 返回渠道实现，不能接受用户提供镜像、命令、端口或文件路径

CLIProxyAPI 渠道负责共享能力：HTTP 管理请求、容器运行时、OpenAI 兼容网关地址、凭据启停、代理设置和数据面探活

供应商适配器只描述真实差异：授权方式、启动接口、OAuth 回调供应商键、授权文件供应商键、模型排除键、本地回调端口和路径，以及供应商自己的额度解析器

`EnvironmentService` 继续负责状态机、幂等、并发锁和持久化，但所有渠道与供应商行为通过注册表取得，不再写一串渠道判断

Compose 执行仍由一个底层运行时负责。CLIProxyAPI 渠道先生成受信任的运行描述，再交给底层运行时创建目录、卷、Compose 项目和网络。FreeBuff2API 占位不生成运行描述

## 授权模型

授权响应增加 `flow`，并支持以下两种结构：

```text
browser_oauth:
  authorization_url
  ssh_command
  user_code = null
  expires_at

device_code:
  authorization_url
  ssh_command = null
  user_code
  expires_at
```

### 浏览器 OAuth

供应商固定回调信息如下：

| 供应商 | 浏览器本地端口 | 回调路径 |
|---|---:|---|
| OpenAI Codex | 1455 | `/auth/callback` |
| Anthropic Claude | 54545 | `/callback` |
| Google Antigravity | 51121 | `/oauth-callback` |

Manager 为三个路径复用同一套回调校验。每次授权仍生成环境绑定、一次性、有过期时间的签名 state。Manager 把授权 URL 中的上游 state 替换为自己的签名 state，收到回调后再把原始上游 state 和供应商键提交给 CLIProxyAPI

SSH 命令使用供应商的本地端口映射到 Manager 的远端回调端口，例如 Claude 使用 `-L 54545:127.0.0.1:<manager-port>`

### 设备码

Kimi 和 xAI 的 CLIProxyAPI 启动响应包含授权网址、`user_code`、上游 state 和 `expires_in`。页面必须同时显示可打开的网址和可复制的用户码，不假设网址一定已经包含用户码

设备码流程不提交 Manager OAuth 回调。CLIProxyAPI 在容器内轮询供应商，Manager 使用真实的 `/get-auth-status` 查询结果。状态变为 `ok` 后执行与 OAuth 相同的凭据读取、模型发现、数据面探活和配置收敛

重复创建或重新授权时，持久化的授权方式、网址、用户码和过期时间用于返回同一个仍有效的授权操作

## 模型、额度与路由

每个供应商只选择与自身真实供应商键匹配的授权文件，再调用该文件的模型接口。模型启用配置写入对应的 `oauth-excluded-models` 键，不能修改其他供应商的配置

Codex 保留当前额度窗口解析逻辑

Claude 的固定版本只保存 `Anthropic-Ratelimit-Unified-*` 原始观测值。这些字段没有已确认的公开稳定百分比语义，本阶段不把它们伪装成剩余百分比或重置时间。Antigravity、Kimi 和 xAI 同样不伪造额度。没有可验证额度窗口时，页面显示“尚未观测”

所有本阶段供应商通过 CLIProxyAPI 的 OpenAI 兼容 `/v1` 数据面进入 LiteLLM。Gateway 快照显式携带协议所需的 provider 值，Reconciler 不从渠道或供应商名称猜测。现有输出继续为：

```text
custom_llm_provider = openai
model = openai/<模型名>
api_base = http://cliproxy-<环境 UUID>:8317/v1
```

Deployment ID 的计算方式、`managed_by` 和 `account_pool_environment_id` 保持不变

## Docker 与 FreeBuff2API 边界

CLIProxyAPI 的以下资源名称和布局保持不变：

```text
Compose 项目和网络：account-pool-<UUID>
容器网络别名：cliproxy-<UUID>
数据卷：account-pool-<UUID>-data
Compose 服务：cli-proxy-api
配置：/data/config/config.yaml
授权目录：/data/auths
```

恢复、启停和删除均根据记录中持久化的渠道分派。历史记录默认进入 CLIProxyAPI 实现，因此现有环境仍可恢复和删除

FreeBuff2API 仅包含渠道元数据和不可用原因。它没有镜像、Compose 模板、运行命令、凭据模型或网络目标。前端将其显示为“暂未实现”并禁用创建，后端再次独立拒绝请求，防止绕过页面调用

## API 与页面

LiteLLM 和 Manager 的创建请求接受 `channel` 与 `supplier`。公开环境响应返回这两个字段，Dashboard 卡片展示“渠道 / 供应商”

创建窗口先选择渠道，再显示该渠道真实可用的供应商。CLIProxyAPI 可选择五个供应商，FreeBuff2API 可见但不可用

浏览器 OAuth 创建成功后显示 SSH 命令和授权链接。设备码创建成功后显示授权链接和用户码，不显示 SSH 命令

Dashboard 的 OpenAPI 类型通过 `npm run gen:api` 重新生成，不手改 `schema.d.ts`

## 错误与安全

所有供应商配置来自代码内静态注册表。API 不接受镜像、命令、内部地址、回调端口、回调路径、供应商接口路径或凭据筛选键

供应商和授权方式不匹配时直接返回校验错误。FreeBuff2API 占位请求在任何持久化和 Docker 操作前失败

OAuth state 继续绑定环境并防重放。回调路径不同不会绕过签名、过期和一次性消费检查

设备码只把用户需要输入的短码返回页面，不返回访问令牌、刷新令牌或授权文件内容。日志和公开响应继续禁止包含管理密钥、网关密钥及完整 CLIProxyAPI 配置

## 测试与完成标准

Manager 测试覆盖：

- 历史 JSON 记录自动映射到 CLIProxyAPI 和 OpenAI Codex
- 五种供应商使用各自真实启动接口、凭据键和模型排除键
- 三种浏览器 OAuth 使用正确本地端口和回调路径
- Kimi 与 xAI 返回设备码并仅通过状态轮询完成
- 未知组合和 FreeBuff2API 在 Docker 前被拒绝
- CLIProxyAPI 的 Compose、网络、别名和数据卷保持原样
- 配置更新不再请求不存在的并发接口
- 不同供应商不会读取或修改其他供应商凭据

LiteLLM 测试覆盖请求转发、严格响应校验、Gateway provider 字段，以及现有 Deployment ID 和 OpenAI 兼容路由不变

Dashboard 测试覆盖渠道与供应商选择、FreeBuff2API 禁用、浏览器 OAuth 引导和设备码引导

完成标准是现有 Codex 回归测试继续通过，新增供应商的接口请求与固定版本源码一致，旧环境无需迁移即可继续运行和删除，并且 FreeBuff2API 全程不会触发构建或运行行为

## 上游依据

- [CLIProxyAPI v7.2.146](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v7.2.146)
- [固定版本管理路由](https://github.com/router-for-me/CLIProxyAPI/blob/d31b15916d15b550bbf388fd6da4a47d4d864109/internal/api/server_management.go)
- [固定版本供应商授权实现](https://github.com/router-for-me/CLIProxyAPI/blob/d31b15916d15b550bbf388fd6da4a47d4d864109/internal/api/handlers/management/auth_files_provider_oauth.go)
- [固定版本凭据与模型接口](https://github.com/router-for-me/CLIProxyAPI/blob/d31b15916d15b550bbf388fd6da4a47d4d864109/internal/api/handlers/management/auth_files.go)
- [固定版本模型注册实现](https://github.com/router-for-me/CLIProxyAPI/blob/d31b15916d15b550bbf388fd6da4a47d4d864109/sdk/cliproxy/service_models.go)
- [固定版本额度观测实现](https://github.com/router-for-me/CLIProxyAPI/blob/d31b15916d15b550bbf388fd6da4a47d4d864109/sdk/cliproxy/auth/quota_signals.go)
