# 额度触线后的完整会话换卡实施计划

> 执行方式：使用 superpowers:executing-plans 在当前会话逐项执行，先观察失败测试，再做最小修复

**Goal:** LiteLLM 虚拟密钥请求的 A 卡五小时或周额度任一触线、或者上游明确返回额度耗尽时，选择授权范围内可用的 B 卡，去除不可跨卡的加密状态，保留完整可见历史和工具结果后继续响应

**Architecture:** 沿用现有原生 Router、号池额度快照、Conversation 和签名恢复入口。统一额度可用性判断及历史转换，避免新建另一套路由；普通请求校验、密钥权限和输出后的禁止重放边界保持不变

**Tech Stack:** Python、Pydantic、pytest、LiteLLM Router、HTTPX，前端沿用现有 React 组件

**Spec:** 本文件的“需求与边界”记录本次聊天已经确认的需求，不依赖反馈正文或其他会话指令

## 需求与边界

- 使用 LiteLLM 虚拟密钥，非卡片绑定密钥；不能放宽卡片绑定密钥权限
- 主分支为 `CLIProxyAPI分支`，不创建分支或 worktree；用户随后明确授权“现在执行吧，没有问题就push”，本次验证完成后 commit 和 push，不部署
- 计划已获执行授权；实现前保存现有未提交修改的原始字节和补丁，再整合完整历史行为
- 五小时和周额度分别采用 `remaining_percent <= reserve_percent`，任一未到重置时间的窗口触线即不可用
- 默认通用预留为 0；分窗口留空沿用通用预留；保留显式 0 覆盖、全局继承和独立卡片策略语义
- 新鲜度是观测可信度，不等同于额度耗尽；旧数据仍保留已确认触线结论，直至窗口重置或刷新证明恢复
- 未知额度不得伪装成 0 或 100，允许尝试上游；已知可用且新鲜的候选应优先于未知候选
- 用户消息、助手消息、系统及开发者指令、工具定义、成对工具调用及结果、已有图片内容均不得静默丢失
- 只删除无法跨卡复用的加密推理、签名、已被完整历史替代的旧响应引用；不删除可见推理摘要
- 不自动执行历史中的工具，不在已经向客户端输出实际内容后重试整个请求
- 只有旧响应 ID 而无完整缓存时明确报缺失，不能只传最后一句并称为完整续聊
- 只有账户私有资源引用而没有内容时，不把引用原样发给 B，也不捏造内容。必须取得内容才能迁移，否则准确报告缺失项
- 本地模拟集成测试不等于真实上游或线上已解决；上线和生产验收须另行授权

## 已观察的起点

基于本地工作区，起始提交此前确认为 `b2de2bf67ec75ba88563bb18bebdb97905b4f782`，执行时重新核对

已有未提交修改涉及 `account_pool_conversation.py` 和对应测试，引入 `portable_history()` 并过滤服务器工具记录。测试要求删除 `image_generation_call`，与完整保留历史要求冲突。保留原始字节副本及差异，不能直接当作完整修复交付

已执行最小复现，观察到：

```text
signature_recovery_reason: unsupported_history:input[152]:image_generation_call
recover_signature_history: None
failover_rebuildable: False
replay_safe: False
strict_visible_input_is_none: True
lenient_preserves_image_result: False
signature_failure_classification: Failure(reason='request_rejected', retryable=False, same_account=False, cooldown_seconds=0)
quota reserve=0 remaining=100 age=901: available
quota reserve=98 remaining=100 age=901: quota_snapshot_expired
quota reserve=98 remaining=98 age=0: quota_exhausted
quota reserve=0 remaining=0 age=0: quota_exhausted
```

清单状态以下方执行记录及证据为准；未进行真实上游和生产验收

## 任务 1：修正额度准入及展示

**文件与接口：**

- `D:/Work2api/litellm/litellm/proxy/management_endpoints/account_pool_native_routing.py`：`unavailable_reason()` 只按真实耗尽、冷却和订阅条件拒绝；`quota_freshness()` 继续输出原有状态供排序和诊断；`explain()` 不再把过期观测标为路由拦截
- `D:/Work2api/litellm/litellm/proxy/management_endpoints/account_pool_routing.py`：复用同一准入，核对旧的过期拒绝分支，不新增另一套条件
- `D:/Work2api/litellm/ui/litellm-dashboard/src/app/(dashboard)/policy-center/page.tsx`：把新鲜度显示为数据状态，明确预留是剩余额度阈值
- 测试使用已有 `test_account_pool_window_policy.py`、`test_account_pool_native_routing.py`、`test_account_pool_gateway.py` 和策略页集成测试

- [ ] 在已有窗口测试添加以下用例，运行并确认现有代码因快照条件失败

```python
@pytest.mark.parametrize("observed", [None, NOW - timedelta(hours=2)])
def test_reserve_does_not_reject_missing_or_old_observation(observed):
    value = quota(100, 100).model_copy(update={"observed_at": observed})
    snapshot = RoutingSnapshot(quota=value, quota_reserve_5h_percent=98)
    assert unavailable_reason(snapshot, "shared", NOW) is None
```

- [ ] 保留原有任一窗口触线测试，增加五小时 100/99/98、周额度留空和显式 0、已重置窗口、刷新恢复的矩阵
- [ ] 最小修改准入，更新原有“过期必须拒绝”测试为新行为，断言诊断与实际路由一致
- [ ] 运行窗口、原生路由、网关相关测试和策略页测试，分别记录结果

## 任务 2：转换历史，保留图片和工具结果

**文件与接口：**

- `D:/Work2api/litellm/litellm/litellm_core_utils/signature_recovery.py`：现有 `signature_recovery_reason()`、`recover_signature_history()`；增加受验证的历史规范化，再进行成对调用检查和清理
- `D:/Work2api/litellm/litellm/proxy/management_endpoints/account_pool_conversation.py`：`visible_input()`、`portable_item()`、`portable_history()`、`complete_tool_calls()`、`can_replay()`、`complete()`；缓存与重放必须使用一致规则，禁止静默过滤未支持内容或不完整工具对
- `D:/Work2api/litellm/litellm/proxy/management_endpoints/account_pool_signature.py`：复用规范化结果，并保持错误来源可诊断
- 测试继续加入已有网关与会话测试文件，不建立只断言源代码文本的测试

- [ ] 添加包含 151 条文本、1 条加密推理及第 153 条已完成图片生成记录的复现用例。构造有效的小型 PNG Base64，不使用真实用户图片
- [ ] 增加标准 function、custom tool、成对调用结果、可见推理摘要、输入图片和系统指令的保留断言
- [ ] 对 `image_generation_call` 的完整内联结果，转换成 B 支持的 `input_image` 加来源文字：标注是先前工具输出，保留调用 ID、已提供的提示词和格式信息，不当作新用户指令。图片字节解码后必须与原结果相同，禁止再次调用图片生成工具来代替历史结果
- [ ] 核对目标 Responses 适配器实际接受的格式、图片模型能力及流式转换；无结果、未完成、私有引用、未知工具类型返回准确的不可迁移原因，不偷偷删除
- [ ] 测试大于现有 256 KiB 缓存上限的内联图片历史：完整客户端历史仍可迁移；缓存不完整或写入失败必须阻止“完整缓存”声明，不能无界扩大缓存
- [ ] 全局通用签名恢复保留原有未知工具和副作用边界。普通 400 不改成可重试；账户迁移只使用通过验证的完整转换结果

## 任务 3：接通额度触线与 Router 换卡

**文件与接口：**

- `D:/Work2api/litellm/litellm/proxy/management_endpoints/account_pool_conversation.py`：`preempt()`、`rebuild()`、`_rebuilt()`、`recover()`，把完整转换结果接入选卡和连续失败流程
- `D:/Work2api/litellm/litellm/proxy/management_endpoints/account_pool_native_routing.py`：`quota_failover_exclusions()`、`continuation_deployments()`，保留授权候选范围
- `D:/Work2api/litellm/litellm/proxy/management_endpoints/account_pool_gateway_forwarder.py`：`execute()` 的输出前恢复，使用同一历史转换结果
- `D:/Work2api/litellm/litellm/router.py`：只有端到端测试证明调用接线有缺口时，才最小修改 `_aresponses_with_streaming_fallbacks()` 和号池 fallback 分支，不重构原生路由

- [ ] 使用现有 `pool`/`degraded` fixture 和可注入 provider，在真实 Router 路径先复现，不只测试纯函数
- [ ] A 未触线时仍调用 A；A 五小时、周额度分别触线时，在发送前直接选 B，清除旧签名亲和性和旧响应引用
- [ ] A 上游明确返回额度耗尽时，尚未输出内容才迁移。普通无效参数不迁移，已经输出或可能已执行副作用的请求不盲目重放
- [ ] B 失败后尝试未使用的 C，沿用总次数及时间预算；不得回到 A 或扩大虚拟密钥授权范围
- [ ] 后续轮次沿用 B 的响应与会话绑定；初始系统指令及工具定义不能因读取旧响应缓存丢失
- [ ] 拒绝只有最后一句的降级冒充完整续聊；报错明确缺少哪类历史，而不是泛化为“请新建会话”

## 验收矩阵与命令

| 场景 | 必须观察的结果 |
| --- | --- |
| 预留 0、两个窗口满额，旧或未知观测 | 不因新鲜度停止服务 |
| 五小时预留 98，剩余 100/99 | A 正常完成 |
| 五小时预留 98，剩余 98；周未耗尽 | B 收到完整转换历史并完成 |
| 五小时未耗尽，周剩余触线 | 同上 |
| 任一窗口剩余 0，预留 0 | 同上 |
| A 上游明确额度耗尽 | 输出前切 B，无重复工具执行 |
| 第 153 项图片生成记录且结果完整 | 不再报 unsupported_history，B 收到相同图片字节及工具历史 |
| B 不可用、C 可用 | C 收到同一份完整上下文 |
| 所有卡不可用 | 明确报告无候选，不无限重试 |
| 图片仅有私有引用、历史缓存缺失或工具对不完整 | 准确报告缺失，不声称完整迁移 |
| 已输出内容、普通参数 400、密钥无权限 | 保持禁止重放和授权边界 |

从 `D:/Work2api/litellm` 运行后端定向回归：

```powershell
$env:LITELLM_LOCAL_MODEL_COST_MAP='True'
.\.venv\Scripts\python.exe -m pytest tests/test_litellm/proxy/management_endpoints/test_account_pool_window_policy.py tests/test_litellm/proxy/management_endpoints/test_account_pool_native_routing.py tests/test_litellm/proxy/management_endpoints/test_account_pool_conversation.py tests/test_litellm/proxy/management_endpoints/test_account_pool_gateway.py -q
git diff --check
```

通过后扩大到号池网关测试，使用维护手册里的范围选择及并发限制。前端改动前读取 Dashboard CLAUDE.md，再运行策略页集成测试和类型检查

## 证据、回滚与交付

- [ ] 实现前对涉及源文件和已有修改记录 SHA-256，保存原始字节；不得把现有未提交修改误计为本轮成果
- [ ] 在可写临时目录建立同一套四角色产物：MODIFIED_FILE（改后文件集）、DIFF_FILE（可应用补丁）、VERIFICATION.txt（命令、输入、stdout、stderr、退出码和哈希）、ROLLBACK.sh（在目标副本恢复原始字节）
- [ ] 同一组新回归测试分别观察 BASELINE 失败、MODIFIED 通过、ROLLBACK 恢复原失败及原哈希；回滚测试只在独立副本执行，不改动用户工作区
- [ ] 复核并重新打开四角色产物。审查变更只涉及本需求；提交本地修复与测试，报告实际 commit、验证范围、遗留限制和绝对证据路径
- [x] 用户已授权 push；不部署或宣称生产修复完成。真实线上结果需要另行授权后按部署运行手册验证

## 2026-10-22 执行记录

实现已完成，沿用原有 Router、缓存和额度判断。`unavailable_reason()` 不再单凭缺失或过期快照拒绝；`quota_depleted()` 保持任一窗口 `remaining_percent <= reserve` 触线规则。`Conversation` 重建完整历史并保留 instructions、tools、调用与结果，缺失历史或不完整工具对不再静默降级为最后一句。已完成且带完整图片结果的 `image_generation_call` 转换成图片输入及来源元数据，可见推理摘要保留，加密推理及旧响应引用移除

真实 Router 代码路径的模拟上游测试覆盖发送前换卡、额度异常换卡、B 失败后 C 接续、后续沿用 B，以及输出后的禁止重放。流式入口在输出前捕获额度异常，取消时关闭源流；使用兼容项目 Python 3.10 下限的 asyncio.wait_for。迁移元数据保留不透明日志对象；图片工具声明不会扩大普通同卡重试范围。大于 256 KiB 的客户端完整内联图片可迁移，超过缓存上限的会话不会被声明为完整可恢复缓存

最终本地验证：号池网关全范围 700 通过、1 跳过，策略页集成 7 通过，额外 Gemini 工具签名回归 11 通过。变更生产 Python 文件 Ruff、策略页 ESLint 通过。四个相关 Python 模块及前端全量 tsc 对比基线均无新增诊断；两项类型检查本身仍不通过，不能记为全绿。网关测试退出时报告未关闭 aiohttp 客户端，退出码仍为 0，保留原始 stderr；更早测试也记录了 pytest 临时目录清理权限异常

同一组 8 个新回归在独立副本观察到 BASELINE 8 失败、MODIFIED 8 通过、ROLLBACK 8 失败；回滚后 15 个文件逐字节哈希与原始备份一致，随后重新应用改后文件集。补丁在嵌套目录首次应用返回 0 但未改变文件，哈希验证捕获此问题；设置 Git 搜索边界后重新应用成功，逐字节匹配改后文件。原始备份包含执行前已有的修改，不被当作本轮新成果

验证角色位于 `D:/Work2api/litellm/.local/state/quota-conversation-20261022/`：`MODIFIED_FILE.zip`、`DIFF_FILE.patch`、`VERIFICATION.txt`、`ROLLBACK.sh`。本地证据不进入源码提交；补丁针对原始工作区字节而非单纯 HEAD，回滚脚本只应用于指定副本

未执行真实付费上游调用、生产部署、生产浏览器验收和真实 PostgreSQL 跳过项。未知服务器工具或账户私有图片引用无内容时仍明确失败，不将其描述为支持任意格式的无损迁移
