# 额度、冷却、兑换与恢复验证

## 职责与边界

额度解析和供应商差异留在 provider 适配层，生命周期与恢复编排留在服务层。额度观测、账户/模型冷却、重置兑换、验证及真实调用证据分别保存，不能互相替代

## 关键入口

[供应商解析](../../../account-pool/account_pool/providers/usage/)、[通用额度判断](../../../account-pool/account_pool/application/quota_state.py)、[额度调度](../../../account-pool/account_pool/quota_scheduler.py)、[自然恢复调度](../../../account-pool/account_pool/quota_recovery_scheduler.py)、[QuotaResetService](../../../account-pool/account_pool/quota_reset.py)、[重置 API](../../../account-pool/account_pool/quota_reset_api.py)、[操作记录](../../../account-pool/account_pool/quota_reset_repository.py)

## 输入输出与流程

上游观测包含来源、观测时间、窗口和每模型额度；解析后供页面及路由使用。未知额度不能当成 0，也不能当成已恢复；新鲜观测不能被旧缓存覆盖

兑换先 prepare 固定 request_id，显式 consume 后保存结果；超时或 uncertain 时查询原操作，不能创建新身份重试。验证使用指定模型，是否恢复路由与验证是否成功分别返回；独立模型冷却和其他阻断不能被一张卡的成功验证无条件清掉

## 配置与最小使用

管理 API 的单卡入口为 `GET /api/environments/{environment_id}/quota-reset`，只读请求但可能读取上游额度状态；`/prepare`、`/consume`、`/verify` 为 POST。consume 可能消耗重置权益，verify 可能产生真实模型请求及费用，必须明确授权

最小使用先通过页面查询当前观测和已有 request_id，再决定是否兑换或只验证。普通模型调用的完成证据独立展示，不等同于一次 reset 验证。文档检查不会请求这些接口

## 依赖与改动影响

依赖供应商 API、每卡唯一凭据、数据库操作锁和网关结算。修改窗口单位、时间戳、错误分类、恢复范围或 model_quotas 合并时，应同时验证解析、UI、准入和持久化，不能只改显示百分比

## 风险与禁止事项

不自动兑换，不因暂时网络超时把运行时判成必须升级，不把一次模型成功变成整卡解锁。管理/能力接口可用、模型列表、额度有余量都不是完整推理成功证据；no_credit、uncertain 与未运行验证不能写成已恢复

## 测试与通过标准

[解析及窗口](../../../account-pool/tests/account_pool/test_quota.py)、[兑换与恢复](../../../account-pool/tests/account_pool/test_quota_reset.py)、[API](../../../account-pool/tests/account_pool/test_quota_reset_api.py)、[自然恢复](../../../account-pool/tests/account_pool/test_quota_recovery_scheduler.py)、[PostgreSQL](../../../account-pool/tests/account_pool/test_quota_reset_postgres.py)。要求原兑换身份可续查，结果可追溯，并发状态不被覆盖，模型范围不扩大

## 故障排查

额度恢复后仍被拒绝，依次检查观测新鲜度、每模型数据、独立运行时冷却、配置 pending、卡片启停和租约。验证完成但未恢复时读取返回原因，不把 state_changed 等拒绝当成功。网络故障与能力 404 分别处理

## 恢复与回滚

重启后读取已持久化的操作与验证记录，继续首个未完成步骤。镜像回退不回滚已经消耗的上游权益，也不恢复旧 Token。数据库恢复需单独确认，边界见[数据与备份](../data-and-backups.md)

## 仅 access token 可用时

打开单卡 access-token 降级开关仅代表允许验证，不代表凭据已可用。刷新失败且尚无已验证模型时，卡片保持冷却；必须通过现有 access token 的完整推理验证，且令牌未过期、配置已同步、额度和独立冷却允许，才能恢复所验证模型的路由

卡片的“模型测试”会在已开启降级且该模型尚未验证时，先调用受控降级验证，成功后立即同步路由，再固定原卡执行正常模型测试。普通 Key 仍走原有鉴权、卡片绑定、租约、预算和结算，不绕过准入；未验证的其他模型不会跟随解锁。也可在凭据操作中显式执行降级验证，再等待路由对账

验证要求本次读取的账户额度有效且有余量。历史模型缓存只保留尚未到期的耗尽限制；旧时间戳、来源为空或已经过去的重置窗口本身不阻止受控推理验证，也不被改写成新的额度观测。已耗尽且尚未重置的模型仍拒绝验证

CPA 验证器逐条解析执行器输出的完整 SSE 行；行尾可能没有换行符，不得把不同事件直接拼成 JSON。只有完整的成功终态及预期正文才能提交证明，中间工具调用、错误、截断和累计超限仍拒绝。验证不刷新 Token、不兑换重置权益、不写认证文件

此修复需同时更新 Manager、LiteLLM 和两张目标卡的 CPA 镜像。commit 或 push 不会更新已运行的容器；发布后需再次分别核对镜像、降级状态、固定卡模型测试及实际用户 Key 调用。不能保证已过期或被上游撤销的 access token 可用


### 自动发现降级模型

Manager 启动后在后台检查现有凭据状态与已启用模型，此后每轮完成后间隔 5 分钟再次检查。只在降级已开启、刷新失败、access token 未过期且独立限制允许时执行完整推理验证；每张卡串行，最多 4 张卡并发。未验证模型可能产生少量真实用量，已验证且仍有效的模型跳过。网络故障或单模型失败不妨碍其他模型验证

成功证明沿用原有持久化和路由对账，普通 Key 不需要先手工点模型测试。卡片与模型测试下拉框仅展示当前有效的已验证模型；失败、过期或冷却模型不展示，不删除配置中的启用列表。关闭降级、凭据替换及配置变化仍使既有证明失效；后台不会重置额度或清除人工禁用、限流冷却。没有 refresh token 但 access token 正常的原生认证路径不改动

每轮验证和路由对账需要时间，开启开关不代表模型立即可用。管理 API 仍保留显式单模型验证用于诊断；一次文本模型验证不代表其他型号、图片接口或所有协议都通过


本轮回归文件与验收边界：

- `account-pool/tests/account_pool/test_access_token_fallback.py`：自动发现、逐模型成功/失败、重复轮次跳过有效证明、独立限制和状态竞争
- `account-pool/tests/account_pool/test_gateway_service.py` 与 `tests/test_litellm/proxy/management_endpoints/test_account_pool_integration.py`：普通 Key/虚拟 Key 的原生准入、预算结算、Chat/Responses 及流式请求
- `tests/test_litellm/proxy/management_endpoints/test_account_pool_model_test.py`：固定卡模型测试、完整终态、拒绝错误和不跨卡重试
- 前端 `accountPoolDashboardSelectors.test.ts` 与 `AccountPoolModelTestDialog.integration.test.tsx`：未验证、过期、冷却模型隐藏，状态变化后禁止提交旧选项

这些本地测试使用边界替身，不等同于线上凭据仍有效。发布后仍需核对运行镜像，在不手工验证的前提下等待自动发现，检查 `/v1/models`，再用原 Key 分别调用 Chat/Responses 和固定卡模型测试。不得用管理员测试成功替代用户 Key 验收

## 查询失败与认证状态

Codex 管理额度查询和兑换遇到上游 401 时原样返回上游状态，不隐式刷新 OAuth 凭据，也不因刷新重放 POST。额度查询失败不会证明兑换成功、额度耗尽或 refresh token 失效。界面缺少有效观测时显示查询失败，并保留已有不确定兑换的请求身份供核对，不能创建新身份重复消耗

凭据刷新通过独立入口执行。网络/出口代理故障、上游拒绝和真正的认证失效要分开查，不能把所有 502 都认定为 token 已被撤销。类似的错误包装问题见 [sub2api #5748](https://github.com/Wei-Shaw/sub2api/issues/5748)
