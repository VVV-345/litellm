# 号池路由与会话可靠性最终执行计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本项目在当前会话串行执行，不创建新分支或 worktree

**Goal:** 修复两项已复现的路由边界问题，并增强失败后的单卡额度刷新及现有会话重建可靠性

**Architecture:** 先收紧“未执行上游”的证据，再原子清理失效会话绑定。管理端用受生命周期管理的单卡刷新队列复用现有刷新能力，Router 保持现有对账机制。会话迁移继续复用 ConversationStore / Conversation.rebuild，不新增第二套历史存储或重放链路

**Tech Stack:** Python、asyncio、FastAPI、DualCache、Redis Lua、PostgreSQL、pytest

**Spec:** 本轮用户要求的四项范围，以及本文“确认事实、约束、验收标准”。这是既有流程的有界改进，不另建架构设计文档

## 确认事实

计划基于 `D:\Work2api\litellm` 的 `CLIProxyAPI分支`，提交 `57af8b79fde4468ac08a33c68e29216a2855be12`

本轮重新运行会话重建和 GatewayService 现有测试，结果为 `73 passed, 1 warning`，退出 0，相关源码哈希前后相同。重新运行补查复现，结果为 `3 failed, 1 warning`，退出 1，对应下面两个独立缺陷，不是三个缺陷

1. 已调用上游一次，后续获取租约被拒，最终仍返回 `x-account-pool-preflight: rejected`，有 tools 和无 tools 两种输入均复现
2. 慢请求读到旧绑定 A，另一请求绑定 B 后，慢请求无条件删除共享绑定，最终 C 可以占用会话

会话缓存当前为 3600 秒、单条 262144 字节，使用 key/model/response_id 隔离与加密。现有迁移会校验工具配对，并拒绝缺失历史、服务端状态工具等不安全重放

单卡 `refresh_environment()` 会读认证状态、发现模型并可能查询供应商额度，不是纯额度查询。`openai_compatible.read_account()` 忽略 refresh_quota，但仍发现模型，不能把它当成免费空操作

Router 对账周期的源码缺省为 30 秒，范围 5～60 秒；部署样例为 5 秒。没有读取生产配置，本文不把“固定 10 秒”当成已确认事实

证据：`D:\Work2api\litellm\.git\six-changes-20261019\scheme-assessment-20261020-115248\EVIDENCE.json`

## 全局约束与边界

- 用户已批准实施，完成后在 CLIProxyAPI分支 commit；按项目要求 push，不自动部署
- 实施只在 `CLIProxyAPI分支`，不另建分支或 worktree；修改前重新检查 HEAD、工作区和源码哈希，版本改变则先重新确认基线
- 每项先补回归并观察失败，再最小修复，再运行匹配测试；不顺手重构相邻模块
- 不扩大现有 tools 重放权限，不提高请求尝试次数或重试期限，不重放已经开始输出的流
- `store=false` 不保存历史；继续保留加密和调用方/model 隔离，不把会话明文写入日志
- 默认保留当前 1 小时 / 256 KB 的缓存政策。先补可靠性与可观测性，不未经容量与保留政策确认直接放大默认值
- 以仓库单 Manager 进程部署为事件队列首版边界。上线前核验实际部署；多管理进程或实例必须先验证共享去抖，不能用进程内时间表宣称全局 60 秒去抖
- 保留全局 5 分钟额度采集作为兜底，不通过压缩全局周期解决所有失败
- 本地替身测试不等于真实 Redis、多实例、供应商或线上验收

## 执行顺序

1. 收紧 preflight 标记
2. 原子清理会话绑定
3. 失败触发单卡额度刷新
4. 增强现有历史重建的可靠性
5. 联合回归、真实服务验证、候选事务与回滚验收

### Task 1：只为整个处理过程零上游调用的拒绝设置 preflight

**Files:**
- Modify: `D:\Work2api\litellm\litellm\proxy\management_endpoints\account_pool_gateway_forwarder.py`，`forward`、`forward_candidate` 与实际发送点
- Inspect: `D:\Work2api\litellm\litellm\proxy\management_endpoints\account_pool_gateway.py`，原生执行前拒绝路径
- Test: `D:\Work2api\litellm\tests\test_litellm\proxy\management_endpoints\test_account_pool_gateway.py`

**Interfaces:** 保持对外 HTTP 协议不变，只纠正 `x-account-pool-preflight: rejected` 的成立条件。跟踪状态限定在同一个入站请求内，不能按每次重试重置

- [ ] 将现有复现 `test_final_lease_rejection_must_not_claim_zero_upstream_calls` 的两种 tools 参数及使用的现有 fixture 收入映射测试文件，不保留对 `.git` 测试文件的产品依赖
- [ ] 运行复现，确认失败原因确实是“上游一次后仍带 preflight”，不是测试初始化错误
- [ ] 在实际交给 HTTP transport 前记录本请求已经尝试上游发送；有连接错误但不能证明零发送时，按已尝试处理。最终拒绝仅在全程没有发送尝试时带标记。沿递归、同卡重试、跨卡候选和 SSE 路径保留这一事实

关键判定的目标形式：

```python
preflight = status in (429, 503) and not upstream_attempted
```

- [ ] 增补纯租约拒绝的正向用例及已输出流不重放用例，分别覆盖有 tools / 无 tools

验收断言：

```python
assert upstream_calls == 0
assert response.headers.get("x-account-pool-preflight") == "rejected"

assert upstream_calls_after_mixed_attempts == 1
assert "x-account-pool-preflight" not in mixed_response.headers
```

- [ ] 运行整个 gateway 映射测试文件，确认原生执行前安全换卡、重试预算和期限不变，再完成联合验证后集中提交

### Task 2：会话解绑使用原子 compare-and-delete

**Files:**
- Modify: `D:\Work2api\litellm\litellm\router_utils\pre_call_checks\deployment_affinity_check.py`，`_forget_pin` 及调用点
- Test: `D:\Work2api\litellm\tests\test_litellm\router_utils\pre_call_checks\test_session_id_affinity.py`
- Regression: `D:\Work2api\litellm\tests\test_litellm\router_utils\pre_call_checks\test_deployment_affinity_check.py`

**Interfaces:** `_forget_pin` 增加读到的 `expected_model_id: str`；复用 `_claim_pin` 已在使用的 `redis_cache.async_register_script`，不新增通用缓存框架

- [ ] 把现有旧读复现 `test_stale_reader_must_not_delete_another_workers_rebound_session` 收入映射测试，保持双 DualCache、双 Hook、可控制旧读返回时机的场景
- [ ] 观察当前 A/B/C 场景失败：B 被删，最终变 C
- [ ] 用一个 Redis Lua 脚本完成读、解析、比较和删除。仅当前值仍指向预期 A 时删除；值已变 B 则保留 B。兼容现有字典和旧字符串两种绑定格式
- [ ] 本地缓存采用同样的条件清理；已有新 B 不被盲删。Redis 返回新获胜绑定时同步本地及剩余 TTL，健康候选中存在 B 时继续遵循 B
- [ ] Redis 不可用时保持现有本地退化边界，不能回退为无条件共享 DELETE，也不能声称仍有跨进程一致性

验收断言：

```python
assert first_binding == {"model_id": "healthy-B"}
assert after_stale_filter == {"model_id": "healthy-B"}
assert final_binding == {"model_id": "healthy-B"}
```

- [ ] 补充仍为 A 时可删除、空键、旧字符串、TTL、同进程交错、Redis 故障用例。修改替身时让它真实执行 compare-and-delete 语义，不能让替身无条件返回期望 B
- [ ] 除替身测试外，使用真实 Redis 的两客户端运行相同受控交错，验证 Lua 原子性，再完成联合验证后集中提交

### Task 3：额度失败事件触发单卡后台刷新

**Files:**
- Modify: `D:\Work2api\litellm\account-pool\account_pool\gateway_service.py`，`GatewayService.__init__` / `finish`
- Modify: `D:\Work2api\litellm\account-pool\account_pool\app.py`，依赖组装和 lifespan 启停
- Modify if needed: `D:\Work2api\litellm\account-pool\account_pool\service.py`，复用刷新与并发准入
- Create: `D:\Work2api\litellm\account-pool\account_pool\quota_refresh_trigger.py`，仅负责失败事件队列、合并和退避
- Test: `D:\Work2api\litellm\account-pool\tests\account_pool\test_gateway_service.py`
- Create test: `D:\Work2api\litellm\account-pool\tests\account_pool\test_quota_refresh_trigger.py`
- Regression: `D:\Work2api\litellm\account-pool\tests\account_pool\test_quota.py`、`D:\Work2api\litellm\account-pool\tests\test_account_pool.py`

**Interfaces:** `GatewayService` 接收可选 `on_quota_exhausted: Callable[[UUID], None]`。`QuotaRefreshTrigger` 接收单卡刷新回调与并发限制，提供同步、非阻塞的 `submit(card_id: UUID) -> None` 和 `run_until_cancelled(stopped: asyncio.Event) -> None`；后台调用 `EnvironmentService.refresh_quota_after_failure(environment_id)`，复用现有 `_refresh_if_needed`，在账号操作锁内重读并持久化事件去抖，Router 不增加接口

- [ ] 先为 finish 写注入回调的失败测试：明确 `quota_exhausted` 才投递。普通 rate_limit、并发满、手动禁用、网络错误、泛化 account_unavailable 均不投递
- [ ] 原因范围首版只含明确的供应商额度耗尽。认证失效沿用现有认证/健康恢复机制，不借此给所有 401/403 或 account_unavailable 发额度查询
- [ ] finish 先按原逻辑释放租约，后台投递不能等待供应商请求，日志失败不能占住租约，后台失败不能改变已返回请求结果
- [ ] 同卡合并排队及在途事件，60 秒内不重复发起；队列上限 256 个不同卡，满时记录跳过并由定时采集兜底，不阻塞请求或无限创建任务
- [ ] 固定 worker 数复用 `quota_refresh_max_concurrency`；与定时采集共享刷新并发准入和现有单卡操作锁，避免两路相加放大查询并发
- [ ] 定时刷新已经在途时，不把 `_refresh_if_needed` 因锁忙返回旧记录当成刷新成功。合并成一个待刷标记，确保失败事件之后至少取得一次新观测；遵守去抖时间，不因锁忙丢掉事件
- [ ] 单次后台操作总超时 60 秒。失败后仅在后续事件到来时允许按 60、120、240、300 秒间隔再次准入，成功重置退避；不额外建立无限自动重试循环
- [ ] worker 先检查渠道及主动查询能力。OPENAI_COMPATIBLE 和无查询实现的供应商直接跳过，不为“额度刷新”调用模型发现。已有刷新会读认证和模型，首版受限复用，不顺手拆整个服务
- [ ] 在 app lifespan 管理 worker，关停时取消并等待，收集异常，不在每次失败时裸建无人管理的 task
- [ ] 刷新失败保留最后成功额度与不可用保护；陈旧、失败或空快照不能自动把刚耗尽的卡重新判为健康
- [ ] 核验实际 Manager 进程/副本数。若多实例，共享去抖必须在现有数据库操作锁下完成持久准入及重读验证；只有互斥锁或进程内 dict 不足以通过此上线门槛

关键测试目标：

```python
assert quota_refresh_calls_for_one_card_within_60_seconds == 1
assert maximum_concurrent_provider_refreshes <= configured_limit
assert finished_request_does_not_wait_for_refresh
assert unsupported_supplier_provider_calls == 0
assert failed_refresh_preserves_last_successful_quota
```

- [ ] 验证两个不同卡均可排队、重复 finish、定时刷新在途、队列满、超时、关闭取消和退避重置
- [ ] 验证落库后 Router 下一轮对账看到新状态，不改 Router 周期；延迟按排队、去抖、供应商耗时和实际对账间隔测量，不承诺固定 10 秒
- [ ] 完成联合验证后集中提交

### Task 4：增强已有可见历史重建，不承诺服务端状态无损迁移

**Files:**
- Modify: `D:\Work2api\litellm\litellm\proxy\management_endpoints\account_pool_conversation.py`
- Inspect/reuse: `D:\Work2api\litellm\litellm\litellm_core_utils\signature_recovery.py`
- Modify only for public errors: `D:\Work2api\litellm\litellm\proxy\management_endpoints\account_pool_session.py`
- Test: `D:\Work2api\litellm\tests\test_litellm\proxy\management_endpoints\test_account_pool_conversation.py`
- Deployment verification: `D:\Work2api\litellm\deploy\docker-compose.yml`、`D:\Work2api\litellm\deploy\DEPLOY.md`

**Interfaces:** 继续由 `Conversation.start` 载入、`Conversation.complete` 保存、`Conversation.rebuild` 删除 previous_response_id 并重建 input。保留成功请求的返回协议和安全校验

- [ ] 先补缺失/过期/超限/损坏/存储不可用时的公开错误断言，不能只验证“抛出了某种 Exception”
- [ ] 给恢复不可用提供稳定的公开错误码 `account_pool_conversation_unavailable`、HTTP 503 和明确提示“无法恢复历史，请重试原账号或新建会话”。不得泄露 response_id 对应的其他 key/model 是否有记录；无关参数错误保持原样
- [ ] 增加不含会话内容的跳过/失败原因记录，区分超限、缓存不可用、不支持历史和缺失记录，避免超过 256 KB 后静默失去诊断能力。过期记录已被缓存淘汰时不伪称能区分从未保存与过期
- [ ] 核验运行态 Router.cache 实际挂接共享 Redis；部署文件没有内置 Redis 不等于线上未配置，须同时检查管理端保存的协调 Redis 配置。复用已有后端，不另存会话明文或新建会话数据库
- [ ] 验证不同 Router 实例读取同一加密记录；验证代理重启后仍能恢复。若承诺 Redis 自身重启后仍可恢复，必须核验并演练其持久化配置，不能把“用了 Redis”等同于“已经持久化”
- [ ] 保持默认 TTL 3600 秒、单条 262144 字节；在此次实施报告列出实际命中率、超限和过期影响，再决定是否另行批准延长。每轮保存累积历史会放大总容量，不只看单条上限
- [ ] 保留完整且有序配对的客户端工具调用和结果。跨卡去掉账号绑定的 encrypted_content，不转发原账号签名；可读摘要按现有规则处理，不保证隐藏推理保留
- [ ] 保持拒绝服务端 tools、不可迁移文件引用、后台状态、自动截断不明历史和 store=false 存储；缓存不全时返回明确错误，不拿残缺历史伪装恢复

目标卡首次成功的迁移用例及不可恢复用例分别验收：

```python
assert "previous_response_id" not in migrated_payload
assert migrated_payload["input"] == expected_visible_history_with_paired_tools
assert migrated_request_count == 1
assert unavailable_error_code == "account_pool_conversation_unavailable"
assert saved_records_when_store_is_false == 0
```

- [ ] 回归普通文本、客户端 function/custom tools、流完成保存、输出后失败不重放、跨 key/model 不可读和迁移后下一轮续聊，再完成联合验证后集中提交

### Task 5：联合验证与交付

- [ ] 在 PowerShell 配置本地价格表与导入路径，运行以下回归。命令结果以执行时真实输出为准，不能预填通过数

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$env:LITELLM_LOCAL_MODEL_COST_MAP = 'True'
$env:PYTHONPATH = 'D:\Work2api\litellm;D:\Work2api\litellm\account-pool'
& 'D:\Work2api\litellm\.venv\Scripts\python.exe' -m pytest `
  'D:\Work2api\litellm\tests\test_litellm\proxy\management_endpoints\test_account_pool_gateway.py' `
  'D:\Work2api\litellm\tests\test_litellm\proxy\management_endpoints\test_account_pool_native_routing.py' `
  'D:\Work2api\litellm\tests\test_litellm\proxy\management_endpoints\test_account_pool_conversation.py' `
  'D:\Work2api\litellm\tests\test_litellm\router_utils\pre_call_checks\test_session_id_affinity.py' `
  'D:\Work2api\litellm\tests\test_litellm\router_utils\pre_call_checks\test_deployment_affinity_check.py' `
  'D:\Work2api\litellm\account-pool\tests\account_pool\test_gateway_service.py' `
  'D:\Work2api\litellm\account-pool\tests\account_pool\test_quota_refresh_trigger.py' `
  'D:\Work2api\litellm\account-pool\tests\account_pool\test_quota.py' `
  'D:\Work2api\litellm\account-pool\tests\account_pool\test_cliproxy_supplier_client.py' `
  'D:\Work2api\litellm\account-pool\tests\test_account_pool.py' -q --tb=short
```

- [ ] 执行修改文件的 lint/类型检查和仓库规定的预算 gate，保留完整日志，不借本次任务修无关存量问题
- [ ] 真实 Redis 两客户端验证原子解绑、跨 Router 历史共享及重启恢复；真实 PostgreSQL 验证租约释放及额度落库/对账。未获得真实服务环境时清楚报告未验收，不把替身通过代替它
- [ ] 测试环境使用真实供应商进行一次可恢复续聊和单卡刷新 smoke，记录客户端请求、脱敏响应及卡片选择；不为测试故意大量消耗额度，不自动部署生产
- [ ] 每项候选修改前保存原始哈希与匹配 BASELINE；在候选副本运行 MODIFIED；在另一副本执行便携 ROLLBACK.sh，再以同输入运行 ROLLBACK 并比对恢复哈希
- [ ] 沿用既有四角色身份、保留历史记录并增加本次事务证据。旧六项包不是当前代码基线，实施时不能用旧回滚脚本覆盖当前工程；本次 rollback 必须嵌入本次修改前的真实基线
- [ ] 重新打开修改包、patch、验证账本和回滚脚本，核对绝对路径、哈希、字面 stdout/stderr、退出码。只提交本任务文件；各项验收后按项目规定提交/推送，不自动发布

## 最终完成标准

两项安全边界的三个原失败场景通过；原生执行前有 tools 换卡仍能工作；已执行上游或开始输出后不伪称 preflight。并发旧读保留新获胜 B。额度事件不阻塞响应、不形成刷新风暴、不误恢复坏卡。可恢复会话保留完整可见历史，不可恢复会话给出明确错误。每一项均有相应回归、真实服务验收范围及已执行的候选回滚证据

## 本轮历史角色核对

四角色沿用下面的绝对路径。旧字节保存在本轮 `historical-roles/`；旧回滚没有作用于工作区，本轮脚本只恢复本轮基线的独立副本

- `D:\Work2api\litellm\.git\six-changes-20261019\MODIFIED_FILE.zip`
- `D:\Work2api\litellm\.git\six-changes-20261019\DIFF_FILE.patch`
- `D:\Work2api\litellm\.git\six-changes-20261019\VERIFICATION.txt`
- `D:\Work2api\litellm\.git\six-changes-20261019\ROLLBACK.sh`

历史 BASELINE：ConnectionError 后调度不恢复，refresh_calls=1，虚拟预加载 18000ms。历史 MODIFIED：恢复，refresh_calls=2，1750ms。历史 ROLLBACK：恢复基线，refresh_calls=1，18000ms。三条历史行为命令均退出 0；本轮仅重读，不是新的供应商或浏览器实测


## 2026-10-20 执行增补

用户追加：全局模型测试问候语 `model_test_greetings`。常用设置每行一条，去除首尾空白和空行，最多 50 条、单条 1000 字符。模型测试每次只随机选一条，聊天/Responses 及流式共用；空值使用既有问候语。读取设置失败明确返回错误，不悄悄忽略已保存内容。旧客户端未提交字段时保留配置

已实现：preflight 仅整个请求未尝试上游时标记；会话解绑 Redis Lua compare-delete 保留新 owner 与 TTL；额度耗尽在释放实际账号租约后入队；有界 256 卡、受控并发、60 秒去抖、失败 60/120/240/300 秒退避、取消清理；事件尝试期限在现有账号操作锁内持久化，unsupported 供应商不查额度；定时周期与 Router 对账周期不改

历史增强保留 3600 秒、256 KB、加密、key/model 隔离、tools 配对和重放限制；仅确需换卡但历史不可用时返回 HTTP 503/account_pool_conversation_unavailable，增加不含正文的缓存诊断。公开错误经过真实 Proxy 异常转换与响应 handler 验证，未修改所有接口的全局错误契约

已执行验证记录位于 `.git/six-changes-20261019/execute-20261020-121133/commands.jsonl`。后端分组 306、325、144 项通过，Manager 设置补组 273 项通过（有重叠，不能相加）；设置组件 14 项通过，前端生产源码 tsc 通过。生成 API schema 同时补齐基线已有 reconciler endpoint 的类型漂移，未手改生成文件

最终复查补修：保存 `model_test_greetings` 仅更新本地设置，不触发所有卡片的运行配置同步。先观察到保存返回 502 的失败用例，再修复并跑设置文件 18 项通过。额度队列满时补充单次告警，同一次满载期间不逐请求刷屏；先观察缺失告警失败，再验证队列 4 项通过

追加兼容检查复现了旧客户端直连 Manager `/api/settings` 时遗漏问候语会清空配置。保存和预览共用遗漏字段保留规则，显式 `[]` 仍清空；设置、运行配置与模型测试补组 55 项通过，含两项新回归。独立审查任务因模型服务 503 未完成，本轮不把它报告为独立审查通过；该提示已由本地红绿测试确认并修复

最新联合回归 `final-rescan-functional`：775 项通过，退出 0；`final-rescan-ui`：14 项通过，退出 0；前端生产源码 tsc 退出 0。修改的 Python 文件 Ruff 通过；UI ESLint 退出 0，有 6 条存量警告。使用同一 Python 解释器比较相关 Python 源码严格类型检查，基线 308、候选 307 条存量诊断，无新增诊断；新增额度队列模块单独检查为 0 错误、0 警告。这不是全仓严格类型检查通过

限制：Docker daemon 不可用，真实 Redis/PostgreSQL/供应商未验收；共享锁测试为内存替身，不能声称跨进程与 Redis 持久化已经实测。没有延长历史缓存或增加历史数据库，没有发布或部署。全量前端 tsc 存在其他测试类型错误；相关生产源码检查单独通过。Python 严格检查基线也有存量错误，保留基线/候选 JSON 差异报告，不借本任务重构整个亲和性模块

全仓预算 gate 未执行：其基线检查可能创建 Git worktree，与本轮不建 worktree 的约束冲突；已执行的相关文件检查不能替代这一项，也没有宣称 Next.js 生产构建完成

候选、差异、回滚和逐条执行证据沿用四角色路径，本次归档前保留旧角色完整字节。本轮 rollback 只在独立 zip 副本执行，不覆盖项目工作区
