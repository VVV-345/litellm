# 号池模块实施计划

> **给执行代理：** 实施本计划时，必须按任务逐项执行，优先使用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans`。每个步骤都要先补充会失败的测试，再实现最小改动，最后运行对应验证。号池模块的 Python、TypeScript、Compose、Dockerfile 和测试文件均需要遵守本计划的文件头与必要中文注释要求

**目标：** 在 LiteLLM 服务器上为每个账号创建独立、可恢复、可删除的 CLIProxyAPI Compose 环境，通过 LiteLLM 网关统一路由 OpenAI 请求，并提供账号授权、配置、额度、冷却、启停和模型管理

**架构：** 保留现有 LiteLLM + Account Pool Manager 双层控制面。LiteLLM 负责浏览器 API 鉴权、对外网关和 Deployment 对账，Account Pool Manager 负责账号环境元数据、OAuth、Compose 生命周期和 CLIProxyAPI 配置；推理流量由 LiteLLM 直接访问每个环境的 CLIProxyAPI，不经过 Manager。每个账号使用不可变 UUID 作为目录、Compose project、网络、容器 DNS 身份和 Deployment 身份，显示名称只用于界面

**技术栈：** Python、FastAPI、Pydantic、PostgreSQL、Docker Compose、CLIProxyAPI、LiteLLM Proxy、Next.js、React、TypeScript、React Query、现有 Dashboard Design System

**依据：** `account-pool/AGENTS.md`、`account-pool/README.md`、当前工作树中的 Account Pool Manager、LiteLLM 管理端点、网关对账器和 Dashboard 页面

## 全局约束

- 当前一期只支持 OpenAI Codex OAuth 和 OpenAI 兼容推理接口，Provider 适配必须保留可扩展边界，但不提前实现其他 Provider
- 每个账号必须在服务器上创建独立 Compose project、容器、网络、运行目录和认证目录
- CLIProxyAPI 不发布宿主机业务端口；LiteLLM 和 Manager 通过 Docker 网络访问它
- 默认出口为服务器直连；用户选择代理 Profile 后，CLIProxyAPI 才使用该 Profile
- 配置页的并发数表示单个账号环境所有启用模型共享的总并发上限
- 额度耗尽后自动冷却；冷却时间由上游重置信息决定；到期后必须通过健康检查才恢复
- 人工关闭和人工冷却优先于自动恢复，二者不能被后台刷新悄悄解除
- OAuth state 必须绑定环境、具备过期时间、一次性消费并防止重放
- Manager 不得把 OAuth state、认证文件名、OAuth 凭据或 Manager token 返回给浏览器或写入普通日志
- 所有由用户输入影响的名称、模型、并发数、Proxy Profile ID、UUID 和 URL 都必须经过类型和边界校验
- 不允许使用 shell 字符串拼接执行 Docker 命令；使用参数数组调用进程
- Account Pool 模块中的代码文件需要文件头职责说明；只有复杂状态机、安全边界、补偿逻辑和非显然的 Docker 行为添加必要中文注释
- 遵守项目现有 Python 120 字符行宽、强类型、不可变数据优先和测试目录映射规则
- 不能把当前生成的 `ui/litellm-dashboard/tsconfig.tsbuildinfo` 等构建产物作为功能改动纳入实现提交

## 一、当前实现盘点

### 已有且应保留的能力

- Manager 独立服务、PostgreSQL JSONB 环境元数据和 Proxy Profile 目录
- `provisioning`、`awaiting_authorization`、`validating`、`ready`、`cooling_down`、`disabled`、`error`、`deleting` 生命周期枚举
- UUID 派生的环境目录、Compose project、网络名、容器名和 Deployment ID
- CLIProxyAPI 无宿主机端口、只读根文件系统、去除 capabilities、`no-new-privileges` 和受限 tmpfs
- Manager 与 LiteLLM 容器接入账号网络，以及 Manager 重启后的网络恢复
- Codex OAuth 启动、SSH 隧道命令、callback 转发、授权状态轮询、授权文件检测、模型发现和数据面健康检查
- CLIProxyAPI 的认证开关、上游代理、模型排除、模型发现和 `/v1/models` 健康检查
- 被动额度窗口解析、自动冷却、人工冷却和到期健康检查恢复基础逻辑
- LiteLLM 管理端点鉴权、Manager Bearer token、Deployment 创建/更新/清理和后台对账
- Dashboard 卡片、创建授权对话框、配置对话框、启停、模型选择、额度展示和代理 Profile 选择
- 现有 Manager 单元测试和 LiteLLM reconciler 单元测试

### 必须补齐的主要缺口

- 删除环境 API、删除状态机、资源清理和幂等重试
- 授权过期、授权失败和环境错误后的重新授权入口
- Provision 失败清理、更新失败补偿和可恢复的期望状态对账
- 一次性 OAuth callback state、重放保护和 callback 后主动验证/对账
- Docker Socket 降权方案，不能让 Manager 直接拥有完整宿主 Docker 控制权
- 账号网络隔离文档与实际出口语义的一致性
- 单账号跨模型共享总并发闸门
- 前端直接访问权限与只读角色一致，禁止无效生命周期状态下操作
- Proxy Profile 错误、空值、协议和可用性校验
- 管理端点、FastAPI、数据库、Docker、授权回调、并发和安全集成测试
- 当前配置保存可能覆盖并发编辑，需增加版本条件更新或明确冲突响应

## 二、目标状态机与数据流

### 生命周期状态

保留现有状态，并明确下列可达迁移：

```text
不存在
  -> provisioning
provisioning
  -> awaiting_authorization   Compose 启动成功且 OAuth 流程已创建
  -> error                    创建或启动失败，等待人工重试或删除
awaiting_authorization
  -> validating               callback 或授权状态检测发现凭据
  -> error                    授权过期、拒绝或认证文件无效
validating
  -> ready                    模型发现和数据面健康检查通过
  -> error                    验证失败
ready
  -> cooling_down             上游报告耗尽、不可用或 next_retry_after
  -> disabled                 用户人工关闭
  -> deleting                 用户确认删除
cooling_down
  -> ready                    冷却到期且健康检查通过，且未被人工冷却/关闭
  -> error                    重试健康检查或配置读取失败，保留恢复信息
  -> deleting                 用户确认删除
disabled
  -> ready                    用户重新启用且健康检查通过
  -> cooling_down             用户启用时仍有有效自动冷却
  -> deleting                 用户确认删除
error
  -> provisioning              用户重试环境修复
  -> awaiting_authorization    用户重新授权且 Compose 仍然可用
  -> deleting                  用户确认删除
deleting
  -> deleted                   所有路由、容器、网络、目录和元数据清理完成
  -> deleting                  清理失败，保存清理进度并可重复执行
```

`manual_disabled`、`manual_cooldown_until`、`automatic_cooldown_until`、`desired_configuration_version` 和 `observed_configuration_version` 必须彼此独立保存。后台恢复只允许解除自动冷却，不能解除人工状态

### 创建与授权数据流

1. Dashboard 调用 LiteLLM `POST /account_pool/environments`
2. LiteLLM 校验 proxy-admin 权限，并使用内部 token 调用 Manager
3. Manager 生成 UUID 和持久化的 `provisioning` 记录，防止显示名称参与资源身份
4. Manager 创建隔离目录、认证目录、Compose 配置和必要 secret
5. Manager 使用参数数组执行 Compose up，不发布 CLIProxyAPI 宿主端口
6. Manager 创建 OAuth state，返回一次性授权信息、SSH 命令和过期时间
7. 用户执行 SSH 隧道并完成授权
8. callback 校验 state、原子标记已消费并触发该环境验证
9. Manager 读取认证文件、发现模型、调用健康检查并持久化 `ready` 或 `error`
10. LiteLLM 对账器移除不可路由环境，为每个已启用模型创建 Deployment

### 更新与路由数据流

1. 配置更新先校验环境版本、字段边界和状态是否允许修改
2. Manager 生成完整期望配置快照，按稳定顺序应用 CLIProxyAPI 配置
3. CLIProxyAPI 全部更新成功后，使用条件版本更新写回 PostgreSQL
4. 数据库保存失败时，保留期望配置和补偿状态，后台继续 reconcile，而不是返回“已完成”
5. LiteLLM 只为 `ready` 且启用、未冷却、健康检查通过的模型保留 Deployment
6. 任何环境状态变化都触发一次尽快对账，后台周期对账作为兜底

## 三、实施任务

### 任务 1：建立状态与持久化的一致性边界

**目标：** 让环境拥有可恢复的期望状态、版本和操作进度，替代仅依赖进程内锁的更新模型

**文件：**

- 修改：`account-pool/account_pool/domain.py`
- 修改：`account-pool/account_pool/repository.py`
- 修改：`account-pool/account_pool/service.py`
- 修改：`account-pool/account_pool/api.py`
- 测试：`account-pool/tests/test_account_pool.py`

**接口约定：**

- Repository 增加 `version: int`、`desired_state`、`operation_id` 和必要的清理进度字段
- 增加条件保存接口：`update_environment(environment_id: UUID, expected_version: int, environment: Environment) -> Environment`
- 版本不匹配必须返回明确的冲突值或既有公共异常，不允许静默覆盖
- Service 的更新入口必须读取一次快照、构造新值、条件保存，不得重绑定或原地修改共享对象

**步骤：**

- [ ] 编写并运行版本冲突测试：两个更新使用同一版本时，第一个成功，第二个返回冲突且不能覆盖第一个更新
- [ ] 编写并运行 Manager 重启后仍能依据 `desired_state` 继续操作的持久化测试
- [ ] 编写并运行同一个 `operation_id` 重复提交只产生一个逻辑操作的测试
- [ ] 实现数据库版本字段、条件更新和操作状态持久化
- [ ] 将现有进程内锁保留为性能优化，但不再把它作为跨进程一致性保证
- [ ] 运行 `account-pool/tests/test_account_pool.py` 中相关测试及静态类型检查
- [ ] 提交一个只包含状态与持久化改动的 Conventional Commit

### 任务 2：补齐 Compose 资源生命周期和删除

**目标：** 支持用户删除账号环境，并安全清理容器、网络、认证文件、环境目录、数据库记录和 LiteLLM 路由

**文件：**

- 修改：`account-pool/account_pool/compose.py`
- 修改：`account-pool/account_pool/service.py`
- 修改：`account-pool/account_pool/api.py`
- 修改：`account-pool/account_pool/ports.py`
- 修改：`litellm/proxy/management_endpoints/account_pool_endpoints.py`
- 修改：`litellm/proxy/management_endpoints/account_pool_reconciler.py`
- 修改：`ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolApi.ts`
- 修改：`ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolCard.tsx`
- 修改：`ui/litellm-dashboard/src/app/(dashboard)/account-pool/page.tsx`
- 测试：`account-pool/tests/test_account_pool.py`
- 测试：`tests/test_litellm/proxy/management_endpoints/test_account_pool_reconciler.py`

**接口约定：**

- Manager 增加 `DELETE /environments/{environment_id}`，重复删除已不存在环境必须返回幂等成功或稳定的 not-found 结果，并在计划中统一一种公共契约
- LiteLLM 增加同路径 DELETE 代理端点，删除前先让该环境不再进入网关快照
- Service 增加 `delete_environment(environment_id, operation_id)`，每一步依据持久进度可重复执行
- Compose runtime 增加 `remove(project_name, environment_directory)` 的超时、失败返回和重复执行语义

**步骤：**

- [ ] 编写删除 happy path 测试，验证先摘除路由，再执行 Compose down，最后删除目录和元数据
- [ ] 编写 Compose down 失败后仍保留 `deleting` 状态、再次调用会继续清理的测试
- [ ] 编写删除重复请求不会重复创建或破坏其他环境的测试
- [ ] 编写删除残留 Deployment 的 reconciler 测试
- [ ] 实现 Manager 删除 API、Service 删除流程和持久化清理进度
- [ ] 实现 LiteLLM 删除代理和前端删除确认交互
- [ ] 前端删除按钮只在用户明确确认后执行，删除期间禁用重复操作并刷新列表
- [ ] 运行对应单元测试和 TypeScript 检查
- [ ] 提交删除生命周期改动

### 任务 3：补齐授权过期、重新授权和 callback 重放保护

**目标：** 授权失败或过期的环境可以从页面恢复，不需要人工登录服务器删除目录

**文件：**

- 修改：`account-pool/account_pool/domain.py`
- 修改：`account-pool/account_pool/service.py`
- 修改：`account-pool/account_pool/api.py`
- 修改：`account-pool/account_pool/cliproxy.py`
- 修改：`ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolApi.ts`
- 修改：`ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolCard.tsx`
- 修改：`ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolCreateDialog.tsx`
- 修改：`ui/litellm-dashboard/src/app/(dashboard)/account-pool/page.tsx`
- 测试：`account-pool/tests/test_account_pool.py`

**接口约定：**

- Manager 增加 `POST /environments/{environment_id}/authorize`，返回新的授权 URL、SSH 命令和过期时间
- callback state 记录 `consumed_at`，使用数据库条件更新确保只有一个请求消费成功
- 已消费、过期、环境 ID 不匹配或签名不匹配的 state 必须被拒绝，不能再次转发
- 重新授权不得删除可复用的 Compose 环境，除非检测到资源不可恢复

**步骤：**

- [ ] 编写 state 首次消费成功、第二次消费失败的测试
- [ ] 编写过期 state、错误环境 state 和未知 state 的测试
- [ ] 编写 error/awaiting_authorization 环境重新授权返回新凭据流程的测试
- [ ] 实现 callback state 原子消费和重新授权 Service/API
- [ ] 实现卡片上的“重新授权”入口，以及授权信息再次展示对话框
- [ ] 授权成功后立即触发验证和 LiteLLM 对账，不只依赖 30 秒后台循环
- [ ] 运行授权相关测试并确认认证内容、state 和 token 不出现在异常消息中
- [ ] 提交授权恢复改动

### 任务 4：实现可补偿的 CLIProxyAPI 配置对账

**目标：** 处理 CLIProxyAPI 局部更新、数据库保存失败、Manager 重启和 Docker 暂时不可用，最终使实际配置回到数据库期望配置

**文件：**

- 修改：`account-pool/account_pool/service.py`
- 修改：`account-pool/account_pool/cliproxy.py`
- 修改：`account-pool/account_pool/repository.py`
- 修改：`account-pool/account_pool/app.py`
- 测试：`account-pool/tests/test_account_pool.py`

**接口约定：**

- CLIProxyAPI adapter 提供确定性的完整配置应用方法，不让 Service 直接拼接 HTTP payload
- 每次配置操作记录期望版本和最后错误，成功后记录 observed version
- 失败操作不得把环境报告为 ready；后台可重复调用同一个 reconcile
- 代理 URL、模型排除列表、认证开关和并发配置的写入顺序必须固定且测试可验证

**步骤：**

- [ ] 编写配置步骤中途失败的测试，验证错误状态保存、后续重试从安全步骤重新开始
- [ ] 编写数据库保存失败的测试，验证后台 reconcile 可以重新读取期望配置并完成同步
- [ ] 编写 Manager 重启后恢复待处理配置的测试
- [ ] 将 update 流程重构为“构造期望快照 -> 应用实际配置 -> 条件持久化 -> 标记完成/补偿”
- [ ] 为所有 CLIProxyAPI 异常统一脱敏，日志只记录环境 UUID、操作 ID 和错误类别
- [ ] 运行完整 Account Pool 单元测试
- [ ] 提交配置对账改动

### 任务 5：收敛 Docker Socket 权限和网络出口说明

**目标：** 保留服务器上按账号运行 CLIProxyAPI 的部署方式，同时降低 Manager 被攻破后的宿主风险，并使文档准确描述网络能力

**文件：**

- 修改：`account-pool/docker-compose.manager.yml`
- 修改：`account-pool/Dockerfile`
- 修改：`account-pool/account_pool/compose.py`
- 修改：`account-pool/account_pool/config.py`
- 修改：`account-pool/README.md`
- 修改：`account-pool/AGENTS.md`
- 修改：`docker-compose.yml`
- 测试：`account-pool/tests/test_account_pool.py`

**部署决策：**

- 推荐在 Manager 与 Docker Engine 之间增加受限 Docker Socket Proxy，只允许 Compose 所需的容器、网络和卷操作；Manager 不再直接挂载原始 `/var/run/docker.sock`
- 如果部署环境暂时无法提供 Socket Proxy，必须在部署文档明确这是阻断生产上线的安全前置条件，而不是把原始 socket 视为完成方案
- 账号网络不能设置为完全 `internal`，因为默认服务器直连需要访问 OpenAI；文档准确写成“账号间网络隔离、无宿主机业务端口、保留上游出站访问”
- 所有 Compose 资源必须继续以 UUID 派生，不能使用可变显示名称

**步骤：**

- [ ] 编写 Compose 输出测试，验证无 `ports`、网络名不受显示名影响、只允许 Manager 和 LiteLLM 加入账号网络
- [ ] 编写配置校验测试，拒绝无效 SSH host、回调地址和代理 URL
- [ ] 增加 Socket Proxy 服务配置、只读/allowlist API 配置和 Manager 连接配置
- [ ] 移除 Manager 对原始 Docker Socket 的直接依赖，或在无法替换时明确阻断条件并不宣称安全完成
- [ ] 为容器补充固定非 root 用户、资源限制和日志大小限制；若 CLIProxyAPI 镜像不支持非 root，记录兼容性检查结果并保留最小必要权限
- [ ] 修正 README、AGENTS 和 Compose 文件头说明，使部署拓扑与实际一致
- [ ] 运行 Compose 生成测试和配置测试
- [ ] 提交容器安全与文档改动

### 任务 6：实现单账号跨模型总并发

**目标：** 使配置页的并发数真正限制整个账号环境，而不是每个模型 Deployment 分别拥有同一个额度

**文件：**

- 修改：`account-pool/account_pool/domain.py`
- 修改：`account-pool/account_pool/service.py`
- 修改：`litellm/proxy/management_endpoints/account_pool_reconciler.py`
- 修改：`litellm/proxy/proxy_server.py`（仅在现有网关接入点需要注册共享限制时修改）
- 修改：`ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolConfigDialog.tsx`
- 修改：`ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolFormatters.ts`
- 测试：`account-pool/tests/test_account_pool.py`
- 测试：`tests/test_litellm/proxy/management_endpoints/test_account_pool_reconciler.py`

**接口约定：**

- `concurrency_limit` 保持为环境级配置
- Reconciler 为同一环境所有 Deployment 注入同一个可识别的 `account_pool_environment_id`
- 共享闸门必须按环境 ID 聚合，而不是按模型名聚合；不同模型同时请求时总活动请求数不得超过配置值
- 若 LiteLLM 现有限流器无法提供跨 Deployment 共享 key，增加最小的可注入共享限流依赖，不把计数器塞入页面或 Manager HTTP 请求

**步骤：**

- [ ] 编写两个模型同时请求的测试，配置上限为 2 时第三个请求必须被拒绝或排队，且两个模型合计不能超过 2
- [ ] 编写请求异常、超时和取消后计数释放测试
- [ ] 编写环境禁用或冷却时共享闸门不再接收新请求的测试
- [ ] 实现环境级共享并发 key 和网关侧限流接入
- [ ] 在配置页明确显示“环境总并发”，避免用户误解为每模型并发
- [ ] 运行 reconciler 与限流测试
- [ ] 提交总并发改动

### 任务 7：完善额度、冷却和代理 Profile 规则

**目标：** 保持“耗尽后冷却”的一期策略，同时让周/月窗口、自动恢复、人工覆盖和代理选择在界面与后端保持一致

**文件：**

- 修改：`account-pool/account_pool/cliproxy.py`
- 修改：`account-pool/account_pool/service.py`
- 修改：`account-pool/account_pool/domain.py`
- 修改：`account-pool/account_pool/repository.py`
- 修改：`account-pool/account_pool/api.py`
- 修改：`ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolCard.tsx`
- 修改：`ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolConfigDialog.tsx`
- 修改：`ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolFormatters.ts`
- 测试：`account-pool/tests/test_account_pool.py`

**规则：**

- 上游返回 `disabled`、`unavailable` 或 `next_retry_after` 时进入自动冷却
- 只展示百分比不足以解释状态，界面必须显示受限窗口名称、剩余额度和预计恢复时间
- 自动冷却到期只允许通过健康检查和额度刷新恢复
- 人工冷却和人工关闭不会被自动刷新解除
- 默认代理模式为服务器直连；代理模式必须要求有效 Profile ID
- Profile 列表加载失败、列表为空、选择的 Profile 已删除和 URL 协议不安全都要显示稳定的前端错误，不依赖 422 才反馈

**步骤：**

- [ ] 编写周窗口和月窗口并存时选择最受限窗口的测试，断言窗口名称和 reset 时间正确
- [ ] 编写自动冷却到期但健康检查失败的测试，状态不能直接恢复 ready
- [ ] 编写人工冷却优先级和人工关闭优先级测试
- [ ] 编写代理 Profile 缺失、不可用和无效协议的校验测试
- [ ] 实现 cooldown 展示、稳定的 Profile 加载状态和保存前校验
- [ ] 明确说明额度是当前上游被动观测值，不新增未经 CLIProxyAPI 支持的主动额度预测
- [ ] 运行额度、冷却和配置测试
- [ ] 提交额度与代理配置改动

### 任务 8：修复前端权限和生命周期交互

**目标：** 让页面权限、按钮可用性和后端状态一致，避免只读用户直达页面看到管理控件或在无效状态发请求

**文件：**

- 修改：`ui/litellm-dashboard/src/app/(dashboard)/account-pool/page.tsx`
- 修改：`ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolCard.tsx`
- 修改：`ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolConfigDialog.tsx`
- 修改：`ui/litellm-dashboard/src/app/(dashboard)/account-pool/AccountPoolCreateDialog.tsx`
- 修改：`ui/litellm-dashboard/src/utils/roles.ts`
- 修改：`ui/litellm-dashboard/src/components/leftnav.tsx`（仅在共享权限表达式需要统一时修改）
- 测试：新增或扩展该 Dashboard 路由对应的测试文件，遵守现有前端测试目录规范

**规则：**

- `proxy_admin_viewer` 不能通过直接输入 `/account-pool` 看到或触发创建、更新、删除、启停请求
- 管理员页面必须同时检查 proxy-admin 和非 view-only 条件
- `provisioning`、`validating`、`deleting` 和未完成授权状态下禁用不适用操作
- 单击卡片应有明确行为；如果保留双击快捷方式，必须提供可发现的配置按钮，不使用误导性的整卡 pointer affordance
- 保存成功但网关对账失败时，界面要区分“Manager 已保存、网关同步待完成”和“保存失败”，并刷新数据

**步骤：**

- [ ] 编写只读角色直达页面测试，断言不出现管理控件且不发管理请求
- [ ] 编写各生命周期状态的按钮可用性测试
- [ ] 编写 Manager 保存成功、LiteLLM reconcile 失败时的提示与刷新测试
- [ ] 修复权限判断、状态按钮、授权恢复入口和删除交互
- [ ] 取消固定周期造成的无意义同步提示闪烁，只在用户操作或状态变化时提示
- [ ] 增加分页、搜索、状态过滤和稳定排序前，先确认 API 是否采用游标分页；一期至少避免无限制地渲染无界列表
- [ ] 运行前端 lint、typecheck 和相关测试
- [ ] 提交前端权限与交互改动

### 任务 9：补齐集成、安全和部署验证

**目标：** 证明系统不仅局部单测通过，而且真实部署边界、鉴权、授权和资源生命周期符合要求

**文件：**

- 修改：`account-pool/tests/test_account_pool.py`
- 修改或新增：`account-pool/tests/test_api.py`
- 修改或新增：`account-pool/tests/test_repository.py`
- 修改或新增：`account-pool/tests/test_compose_integration.py`
- 修改或新增：`tests/test_litellm/proxy/management_endpoints/test_account_pool_endpoints.py`
- 修改：`tests/test_litellm/proxy/management_endpoints/test_account_pool_reconciler.py`
- 修改：`account-pool/README.md`

**必须覆盖的场景：**

- Manager Bearer token 缺失、错误、长度不足和时序比较
- LiteLLM 非 proxy-admin、view-only、过期 token 和跨环境 ID 访问
- OAuth state 未知、过期、重复 callback、错误环境和 callback 后立即验证
- Provision 中 Compose up 失败、部分资源创建、重试和清理
- 删除中 Docker down 失败、目录删除失败、重试和最终一致
- 两个 Manager 实例同时更新同一环境的版本冲突
- PostgreSQL 保存成功但 CLIProxyAPI 更新失败，以及反向失败
- 环境无宿主端口、网络仅允许预期服务接入、容器间不能通过显示名串访
- Secret、OAuth token、Proxy URL 密码不出现在 API 响应和日志
- 同一环境多个模型共享总并发
- 自动冷却、reset 到期恢复、人工冷却和人工关闭
- LiteLLM Deployment 添加、更新、清理、禁用和环境删除后的最终对账

**步骤：**

- [ ] 先增加 API 和 repository 测试，使未实现的行为明确失败
- [ ] 实现测试所需的依赖注入 fake，不通过 monkeypatch 类属性制造隐式全局状态
- [ ] 在具备 Docker 和 PostgreSQL 的 CI/本地环境运行受控集成测试；没有依赖时运行清晰标记的单元测试，并在 README 写明未执行的集成层
- [ ] 运行 `git diff --check`、Account Pool 目标测试、LiteLLM 目标测试和 Dashboard typecheck/lint
- [ ] 检查所有新增/修改的 `account-pool` 代码文件是否有职责文件头和必要中文注释
- [ ] 提交验证与文档改动

## 四、推荐实施顺序

按以下顺序推进，不先扩展 Provider 或主动额度预测：

1. 任务 1，状态与版本一致性
2. 任务 2，删除和资源清理
3. 任务 3，重新授权和 callback 防重放
4. 任务 4，配置对账和失败补偿
5. 任务 5，Docker Socket 与网络安全
6. 任务 6，账号级总并发
7. 任务 7，额度、冷却和代理 Profile
8. 任务 8，前端权限和生命周期交互
9. 任务 9，集成、安全和部署验证

任务 2、3、4 是上线前不可省略的生命周期闭环。任务 5 是生产服务器部署前的安全门槛。任务 6 解决已确认的产品语义。任务 7 和 8 完成用户可见功能。任务 9 作为每个阶段的最终验收，不应等到发布后才补

## 五、明确暂不实现的范围

- 除 OpenAI Codex OAuth 以外的 Provider 授权适配
- 基于历史调用量的主动额度预测、预算预留和智能调度
- Kubernetes、多节点调度、Celery、Temporal 等任务平台
- 在 Account Pool 内创建和管理代理 Profile 本身；本模块只消费既有 Profile 目录
- 直接向浏览器暴露 CLIProxyAPI 管理端口
- 让 Manager 转发推理请求
- 通过显示名称、邮箱或用户输入决定容器、目录、网络和 Deployment 身份

## 六、最终验收标准

功能验收：

- 管理员可以从 LiteLLM “号池”页面创建账号环境
- 服务器为每个账号创建独立 Compose project、网络、容器、配置目录和 OAuth 认证目录
- CLIProxyAPI 不对宿主机发布业务端口，LiteLLM 可以通过内部网络路由到 ready 环境
- 用户可以通过页面返回的 SSH 命令完成 OAuth 授权，授权过期后可以重新授权
- 用户可以修改名称、环境总并发、开关、人工冷却、代理选择和启用模型
- 额度耗尽后环境自动冷却，reset 到期并健康检查通过后恢复
- 删除环境会摘除路由并清理所有容器、网络、认证文件、目录、元数据和 Deployment
- Manager 或 Docker 暂时失败后，重试和后台 reconcile 可以恢复，不需要手工清理数据库

安全验收：

- view-only 用户直达 `/account-pool` 不会看到或触发管理操作
- Manager 不直接持有未收敛权限的原始 Docker Socket，或部署文档明确阻断生产上线
- OAuth state 一次性消费，重复 callback 不会再次授权
- API、日志和错误响应不泄露 OAuth token、认证文件内容、Manager token 或代理密码
- 所有环境资源身份由 UUID 派生，显示名称修改不会导致跨环境访问
- 账号间网络不能通过显示名或共享 alias 互相路由

工程验收：

- Account Pool 模块新增代码均有中文文件头和必要中文注释
- 每个新增行为都有能够在实现缺失或逻辑突变时失败的测试
- `git diff --check` 无输出，目标测试、类型检查和 lint 结果已记录
- README 的网络、凭据、部署权限和额度语义与实际实现一致
- 计划执行产生的提交只包含对应任务文件，不混入构建产物或用户已有无关修改
