# 额度重置与周期恢复本地执行记录

后续更新：本文记录截至 a88de2f658 的结果；其中“普通 API 成功回写未接入”已由后续本地修复补齐，见 D:/Work2api/litellm/docs/superpowers/plans/2026-11-03-quota-call-verification.md。生产验收边界保持不变

## 范围与结果

TARGET 为 D:/Work2api/litellm，分支 CLIProxyAPI分支，基线 f9c0d9fa171a68b949d01cb83838dcdfa8272e76。此次只做本地代码、隔离测试及提交，没有 push、部署、连接生产、刷新线上 Token 或消耗真实重置次数。未改动既有无关的 ui/litellm-dashboard/CLAUDE.md

自动重置指上游额度周期到期。调度器不调用 consume，不自动使用重置券。真实认证撤销、人工禁用、预算、并发、代理故障及独立冷却仍可能合法阻止调用，本次不关闭这些保护

| 提交 | 实现 |
| --- | --- |
| 1072202417 | 精确允许重置表/索引新增，保留破坏性 schema 门禁；区分旧 CPA 能力缺失、管理认证和网络失败 |
| 5521f307a7 | CPA 提供有证据的模型级认证错误范围，Manager 不将其误判为整卡 Token 失效；确定的凭据失效仍阻断 |
| 7b7c344409 | recovered_record 仅清已验证模型的 rate_limit；保留其他模型限制，防止旧额度快照覆盖新验证结果 |
| 0cfbd646a8 | 自然到期后台扫描、任务合并、有界并发和失败退避；重启后重新扫描，不消费重置券 |
| fix: persist quota recovery verification and reconcile uncertain redemptions | 持久化最近验证；不明确兑换只查询不重发；兑换成功后验证所选模型；UI 区分状态恢复与普通 API 调用；补充旧额度截止时间与独立冷却边界 |

最后一项的 commit ID 可用上表的唯一提交标题从 git log 查得，避免在提交正文内写入自引用哈希

## 本地验收

完整命令、stdout、stderr、退出码保存在 D:/Work2api/litellm/.local/quota-reset-20261102 下的 execution-*.json 中

| 检查 | 实际结果 | 证据文件 |
| --- | --- | --- |
| Manager、额度、重置、发布邻接回归，含真实隔离 PostgreSQL | 569 passed，exit 0 | execution-final-python3.json |
| LiteLLM 网关和管理接口 | 382 passed，exit 0 | execution-proxy.json |
| 最终 import 整理后的通道回归 | 97 passed，exit 0 | execution-lint-regression.json |
| UI 定向集成测试 | 8 passed，exit 0 | execution-ui-final.json |
| UI 源码及相关测试 TypeScript | 分别 exit 0 | execution-tsc-source.json、execution-tsc-tests.json |
| 变更 Python 文件 Ruff | exit 0 | execution-ruff.json |
| 六个恢复核心文件严格类型检查 | 0 errors、0 warnings | execution-core-type-check.json |
| UI ESLint | 0 errors、2 warnings（复杂度、inline object） | execution-ui-lint.json |
| 正式补丁对应 CPA 定向 Go 测试及构建 | 分别 exit 0，含真实 Codex executor 对本地模拟供应商及随后正常选卡 | execution-go-final.json、execution-go-build.json |
| Dashboard 隔离生产构建 | Webpack 成功 | execution-webpack-build.txt |
| 两数据库导出、恢复、重应用与损坏备份拒绝 | 2 passed，exit 0，实际 PostgreSQL | execution-release-db-offline.json |

自然调度补充覆盖：截止前不触发，UTC 与 UTC+8 相同时刻一致，人工禁用/人工冷却/配置未完成不调度，新鲜正额度可以触发复查，旧或未来时间戳不能触发。到期仍无额度时不探测、不兑换

失败记录没有丢弃：原发布数据库集成测试因 Docker 代理 host.docker.internal:7897 不可达，无法获取 alpine:3.20，产生两项 setup error。离线复验只在临时副本中将两个空闲应用容器镜像改为本机已有 postgres:16，数据库、真实导出/恢复代码和断言保持原样。不是原 Alpine fixture 已通过

Turbopack 构建因隔离副本的 node_modules junction 越出 filesystem root 失败，改用 Webpack 后通过，不宣称两个构建器都通过。初次 Pyright 扫描范围错误，曾扫描到临时目录；扩展严格检查的 25 项诊断未做基线对照，不宣称全项目严格类型通过。只对上述六个核心文件确认严格检查通过。新增 schema 边界测试初次把 SQL 写成 bytes 常量而非字符串，导致 fixture 失败；修正 fixture 后纳入 569 项通过结果。Ruff 初次发现本轮新增 import 排序问题，修正后重测

两次独立子代理审查启动均遇到上游 invalid_request_error，没有产生审查成果；本次由主会话检查 diff，不宣称已通过独立审查

## 三态与可回滚工件

保留旧事务历史后，在相同四角色路径扩展本轮工件，基线为 f9c0d9fa17。同一输入为 model-a 与 model-b 各有独立 rate_limit，验证 model-a：原逻辑误清两个模型，修复后只清 model-a；回滚独立副本后回到原行为。该探针验证作用范围，不代表生产请求验证

- D:/Work2api/litellm/.local/quota-reset-20261102/transaction/MODIFIED_FILE.tar.gz
- D:/Work2api/litellm/.local/quota-reset-20261102/transaction/DIFF_FILE.patch
- D:/Work2api/litellm/.local/quota-reset-20261102/transaction/VERIFICATION.txt
- D:/Work2api/litellm/.local/quota-reset-20261102/transaction/ROLLBACK.sh

ROLLBACK.sh 只接受目标副本，还原文件不代表退回数据库或供应商已经消费的次数。生产回退不得覆盖已轮换的新凭据

## 未完成的生产验收与边界

本地复现证明存在模型聚合错误导致误封的代码路径，尚不能断言生产部署后的认证失败只有这一原因。没有读取部署后现场日志

目前保存每卡最近一次管理验证及来源，不是完整历史，也不把管理验证当作普通 LiteLLM 调用验证。尚未把普通数据面请求结果回写为该页面的持久化“调用已验证”状态；UI 明确显示“普通 API 调用仍需确认”。本地测试覆盖 Manager/LiteLLM 与真实 CPA executor 的各自边界，不宣称一个进程链上的生产端到端已验收

部署后仍需确认 Manager、release-worker 和既有卡片 CPA 都更新。仅在第一张卡、同模型和同代理上，先做不消耗重置次数的验证，再通过 /v1/chat/completions 与 /v1/responses 完成最小普通请求，核对选卡、终止事件和 LiteLLM 统一记账。随后受控重启复验；任一层失败即停止，不通过换卡或再消费券掩盖问题。真实自然周期尚未到达时只能报告模拟时钟验证通过

uncertain 状态不会自动重发消费 POST，也不会自动创建新兑换身份。供应商没有可核实的幂等或兑换查询结论时，保留待核对状态是有意的保护，不应以“重试恢复”为由再扣一次券
