# 成本台账

号池的“成本管理”页以人民币记录订阅、代理和服务器支出，手工录入确认付款，自动续期按用户约定金额记账。账号卡片的“成本”按钮、成本页未录入账号行的“录入”按钮都能新增订阅记录；已有费用时该按钮显示“重新录入”，带出最近一笔完整数据供修改。订阅必须关联 Manager 中已存在且不在删除中的账号，无法仅输入账号名称创建虚拟成本账号

## 录入与查看

每条费用包含名称、付款金额、付款日期、服务开始日期、服务结束日期和可选备注。计费周期直接显示为按钮，点击即可切换；订阅提供月付、季付、年付和自定义服务期，另可选择自动续期。金额必须为正数，最多两位小数；后台按整数分存储。付款日期不能晚于北京时间今天，服务首尾两天都计入成本，单条服务期和查询范围最长 3660 天跨度。第一版只接受人民币，不做汇率转换

代理和服务器分别从“录入代理费用”“录入服务器费用”添加，独立汇总，不绑定或均摊到账号。可选择月付或年付，系统按服务开始日期自动填写结束日期；修改开始日期时同步重算，手动修改结束日期后保留自定义值。资源费用的计费周期仅用于日期计算，不会自动续期。新增账号不会自动产生订阅费用，未录入时显示“未录入”，不会假定该账号是零成本

默认查看本月至今的期间成本，可切换日期和统计口径。实际付款按付款日计入全部金额（自动续期采用约定续期日）；期间成本按服务天数计算落在查询日期内的部分。譬如 1 月 31 日付款 1 元购买三天服务，1 月期间成本为 0.33 元，2 月为 0.67 元，1 月实际付款为 1 元。采用累计整数分差保留尾差，相邻期间总计等于原始金额。未来查询日期包含已预付服务成本，不代表服务已经使用

费用记录展示付款日或服务期与所选日期相交的条目，每页 20 条。账号可以按名称搜索。账号列表的最近一笔订阅与到期提醒按全部有效订阅计算，不受历史查询期间限制。账号表格不再单列期间成本或实际付款，统计口径仅影响上方汇总

## Coding Plan 价格预设（2026-11-18 核验）

已有账号的订阅录入和续费弹窗支持选择厂商、套餐与计费周期，再点击“套用套餐”。选择本身不会覆盖表单；套用名称、金额和计费周期，并按服务开始日自动填写结束日期，保留付款日期和备注。金额可改为真实实付，只有保存费用后才入账。续费仍优先预填上一笔真实费用，不自动套用官网价格

下表均为人民币月付金额；除百度明确标为活动展示价外，使用官方目录价或连续包月价。不是按量 API 报价，也不代表这些套餐支持任意网关转售或共享，具体使用范围见各厂商条款

| 厂商 / 官方来源 | 当前月付套餐与参考价 | 价格口径与限制 |
| --- | --- | --- |
| [智谱](https://bigmodel.cn/glm-coding) | Lite 118；Pro 538；Max 1078 | 连续包月，包季、包年折扣不当成月付价 |
| [MiniMax](https://www.minimax.cn/m-plan) | Go 49；Explore 119；Build 469 | 当前 M Plan，使用新套餐名 |
| [Kimi](https://www.kimi.com/help/membership/membership-pricing) | Plus 99；Pro 199；Max 699；Ultra 1399 | 新套餐 Go 49 不含 Code，因此不作为 Coding 预设 |
| [阿里云 Token Plan](https://help.aliyun.com/zh/model-studio/token-plan-personal-overview) | Lite 39；Essential 79；Standard 139；Pro 312 | Lite、Essential 新客价 19.50、39.50 截止 2026-11-30，未自动套用 |
| [阿里云旧 Coding Plan](https://help.aliyun.com/zh/model-studio/coding-plan) | Pro 200 | Lite 已停售且停止续费；Pro 首月 39.90 需核对资格 |
| [火山方舟](https://www.volcengine.com/activity/codingplan) | Lite、Pro，金额待核实 | 本次官方页面价格接口持续显示“价格查询中”，不沿用网络旧价。套用名称会清空原金额，要求手填 |
| [腾讯云](https://cloud.tencent.com/document/product/1772/128947) | Lite 40；Pro 200 | 官方原价，已结束的首购价不作为预设 |
| [百度千帆](https://cloud.baidu.com/product/qianfan_home/token_plan_personal.html) | Mini 4.90；Lite 19.90；Standard 49.90；Pro 99.90；Max 299.90 | 采用积分制当前展示价，Mini、Lite、Pro、Max 带限时或首月促销，不推断续费原价 |
| [小米 MiMo](https://mimo.mi.com/docs/zh-CN/tokenplan/Token%20Plan/subscription) | Lite 39；Standard 99；Pro 329；Max 659 | 使用官方人民币价，不从美元换算 |
| [华为云](https://support.huaweicloud.com/price-maas/price-maas-0035.html) | Lite 59；Standard 149；Pro 399；Max 799 | 个人版常规价格，每日限量发售 |
| [讯飞星辰](https://static.xfyun.cn/doc/spark/CodingPlan.html) | 高效版 199；速通版 999 | 无忧版、专业版已下线。速通版首月 699 为限时活动 |

已核实的年付预设按一次付款总额保存：MiniMax Go / Explore / Build 为 490 / 1190 / 4690；Kimi Plus / Pro / Max / Ultra 为 948 / 1908 / 6708 / 13428；MiMo Lite / Standard / Pro / Max 为 408 / 1044 / 3468 / 6948。讯飞季付高效版 538、速通版 2997，限时速通版首季 2697 仅作活动说明

预设数据与来源位于 `codingPlanPresets.ts`，核验日期展示在弹窗内。它是人工核验的静态快照，不会自动跟随官网调价、变更已有账单或为账号生成费用。未加入未核实金额、已下线套餐与历史套餐别名；有历史订单时仍可手动输入

## 服务日期与自动续期

月付、季付、年付分别按 1、3、12 个日历月预填结束日期。结束日取下一周期开始日的前一天；目标月没有相同日期时先对齐该月最后一天，再减一天，例如 2026-11-18 月付结束日为 2026-12-17，2026-01-31 月付结束日为 2026-02-27。服务开始日期变化时会重新计算；手改结束日期后保留人工值，重新选择周期或套用套餐可恢复计算。应按真实订单核对日期

“到期后自动续期”默认关闭，需要先选择月付、季付或年付。开启表示授权台账按本次金额持续记账，服务到期后的次日自动生成下一期，付款日采用续期开始日，不调用供应商扣款接口。自动记录显示“自动续期生成（按约定金额记账）”，实际付款与期间成本均包含这些记录，不代表系统已经核验厂商扣款成功。促销价格到期前应关闭续期，再按真实新价格录入

Manager 启动后及每 60 秒检查北京时间日期，停机跨期后按顺序补记到当前周期，不预先生成未来周期。事务锁与每期唯一父记录保证多进程及重复执行不会重复入账；每轮最多补记 1200 期，剩余下轮继续。账号行的“关闭续期”停止后续生成，已有记录保留。作废某期会关闭该期及后续链的续期，已删除或正在删除的账号不会再生成费用。手工新增同账号订阅会停止旧记录链，采用新记录的续期选择，避免两条链并行

续期设置保存在独立的 `account_pool_cost_renewals` 表。原费用 JSON 不写入新字段，新版可以读取旧费用，旧版仍可以读取费用正文但不会执行续期。结构兼容门禁只接受新增表的精确前向变更，禁止直接反向回退或夹带其他变更；升级前仍需备份两库并验证恢复

## 续费、纠错和历史

“重新录入”保留原付款日、服务期、金额、备注和自动续期设置。保存时在同一事务中将旧记录作废并创建更正记录，旧记录及操作者保留，汇总只计算更正后的费用。重复保存沿用录入编号，不会重复记账；原记录已更正、作废或生成下一期时拒绝旧页面覆盖，需刷新后重新操作

点击“续费”预填最近一笔名称、金额和服务结束后的次日，核对实际服务日期后保存新的付款记录。它不会修改旧费用或向厂商扣款。允许实际存在的重叠服务，但会累加成本，录入时需避免重复登记。界面重试同一失败请求沿用唯一编号，后台并发重复请求也只产生一条记录；修改表单后保存会使用新编号

金额或日期误录时填写原因作废，再录入正确记录。作废费用从汇总中排除，原记录、创建人、作废人、时间和原因保留在数据库中，页面显示作废原因。作废不代表退款。删除号池账号后，已录入费用仍计入历史汇总，显示“历史账号”，不能再为其新增费用

到期提示以北京时间当前日期检查有效订阅，展示未来 7 天到期（含今天）和已到期数量。连续的已付续费会延长覆盖日期；存在断档的未来订阅不会隐藏当前过期提醒。只有未来服务记录时显示“服务尚未开始”。已删除账号不参与提醒。提醒只在页面展示，不发送外部通知、不自动停用账号，也不改变网关路由、额度或预算

## 实现与数据边界

Dashboard 复用现有鉴权、控件、请求客户端和账号池查询缓存，入口在 `src/features/account-pool/components/costs/`。三个管理接口是 `GET /account_pool/costs`、`POST /account_pool/costs` 和 `POST /account_pool/costs/{id}/void`。LiteLLM 只允许代理管理员调用，并从认证身份生成审计操作者；Manager 私网接口继续要求服务鉴权

Manager 的 `cost_models.py`、`costs.py`、`cost_repository.py` 和 `cost_api.py` 分别负责契约、计算、持久化和私网接口。LiteLLM 代理端声明对应契约，前端类型通过 `npm run gen:api` 生成。成本报表目前读取完整台账后计算，适合人工录入的第一版规模；记录量显著增加时需要数据库范围查询及服务端分页

数据位于 Manager 数据库的 `account_pool_cost_records`，不是 LiteLLM 模型估算费用表。新增表不回写已有账号，也不设置会级联删除费用的账号外键。创建订阅时事务锁定真实账号行，删除与录入的先后顺序由数据库保证

发布兼容检查只放行这一条精确的新表定义。反向移除、字段变化或夹带其他表仍被拒绝；发布和回滚继续遵守[统一更新手册](../../../deploy/UPDATE_RUNBOOK.md)，升级前须备份 Manager 数据库，不能因为台账是新表而跳过备份或替换镜像门禁

本台账不统计 API 模型估算费用、每百万 Token 成本、收入、利润或自动预算控制

## 本次验证范围（2026-11-18）

实现基于 `08aa3a1640`，代码在 `CLIProxyAPI分支`，未推送或部署。定向后端回归使用 `PYTHONPATH=account-pool LITELLM_LOCAL_MODEL_COST_MAP=True .venv/bin/python -m pytest`，范围为三个 `test_cost*.py`、代理 `test_account_pool_costs.py` 和 `test_release_runtime.py`；数据库测试连接独立的临时 PostgreSQL 16 实例，验证并发幂等、真实账号存在性、作废审计和删除历史保留，123 项通过

Dashboard 使用显式路径运行 `npx vitest run src/features/account-pool/components/costs src/features/account-pool/hooks/useAccountPoolMutations src/features/account-pool/hooks/accountPoolQueryKeys src/features/account-pool/preloadModules`，共 21 项通过，其中表单与报表测试替换网络边界，保留真实组件。`npx tsc --noEmit --incremental false` 检查源码与测试类型通过。新 Manager 和代理成本模块的 basedpyright 严格检查通过，相关 Python 文件 Ruff 检查通过，新增前端文件无 ESLint 告警或错误，OpenAPI 类型已重新生成

完整 `make check` 未通过：GitHub 基线拉取发生网络超时，且全仓库已有 142 项 complexity 告警超过 140 的预算。在临时源码目录对起始提交运行相同 ESLint，结果同样为 142 项，本次没有增加该计数。没有提高预算或降低门禁

在临时源码目录、Node 24.19.0 下执行 `next build --webpack`，起始提交和最终源码均在既有 `public/assets/logos/zai.svg` 导入处失败。未修改该图标，不能将完整生产构建记为通过。未进行浏览器端到端验收或真实供应商调用；此功能不调用推理供应商，现有本地实例保持原版本

## 套餐预设验证（2026-11-18）

新增 10 家厂商、11 组产品、47 个计费周期选项，其中火山方舟两档金额待核实。Node 24.19.0 下定向执行 `vitest run src/features/account-pool/components/costs`，3 个文件共 19 项通过；`tsc --noEmit --incremental false` 源码及测试类型检查通过，4 个修改/新增前端文件的 ESLint 通过

在独立临时目录执行 `next build --webpack`，完整静态生产构建通过。表单测试验证年付总额、手改实付金额、既有账号归属、选择不写入、套用保留日期和备注、未核实报价清空旧金额及切换厂商清空套餐选择。本次只改前端预设与文档，未修改后端契约或数据库结构，未重跑全仓库 `make check`。本地部署和浏览器实际验收另记于运行目录的 `coding-presets-deployment-verification.json`

## 自动续期验证（2026-11-18）

在 `CLIProxyAPI分支` 对本次源码执行定向后端回归，包含成本计算、接口鉴权、发布兼容和真实独立 PostgreSQL 持久化，共 131 项通过。数据库验证覆盖到期前不生成、跨两期补记、跨实例并发幂等、默认不续期、关闭链路、作废停止、删除账号停止和原费用 JSON 不含新字段。Dashboard 成本模块 3 个文件共 28 项通过，覆盖月付预填、开始日期变化、手动结束日期覆盖、默认关闭及开启字段提交、关闭续期入口。`tsc --noEmit --incremental false` 检查源码与测试通过，改动前端 ESLint、相关 Python Ruff、6 个成本模块 basedpyright 严格检查通过

独立临时源码目录使用 Node 24.19.0 执行 `next build --webpack`，生产静态构建通过。从当前本地 Manager 容器提取源码核对，精确新增续期表的前向兼容通过，反向移除被拒绝。本轮未重跑全仓库 `make check`，未调用真实模型供应商。实际本地部署、备份恢复和浏览器结果记录在运行目录的 `cost-renewal-deployment-verification.json`


## 2026-11-18 重新录入修复

账号行无有效费用时显示“录入”，已有费用时显示“重新录入”，表单带出最近一笔完整数据。账号表格移除期间成本列。新增管理员更正接口 `POST /account_pool/costs/{record_id}/replace`，Manager 在同一事务中创建新记录并作废旧记录，共用现有续期锁，费用 JSON、表结构和启动配置不变

本轮定向后端回归命令覆盖 `test_cost_repository.py`、`test_cost_api.py`、`test_costs.py`、`test_release_runtime.py` 和代理 `test_account_pool_costs.py`，共 135 项通过，包含隔离 PostgreSQL。前端 `vitest run src/features/account-pool/components/costs` 共 29 项通过。源码与测试的 `tsc --noEmit --incremental false`、变更文件 ESLint、三个变更后端模块的 basedpyright 严格检查及 Ruff 通过；隔离目录以 Node 24.19.0 完成 `next build --webpack`。全仓检查未在本轮重跑，以上不代表生产服务器或真实供应商请求验收


同日补充代理、服务器的月付和年付选择，复用服务日期计算及已有 `cycle_months` 字段，保留自定义服务期和手动结束日期，不新增资源自动续期。成本模块前端回归 31 项通过，源码与测试 TypeScript、变更前端 ESLint、隔离 webpack 生产构建通过；本项无后端或接口契约变更

同日将计费周期改为直接可见的按钮，单击切换并显示选中状态。成本模块前端回归 31 项、源码与测试 `tsc --noEmit --incremental false`、变更文件 ESLint、Node 24.19.0 隔离目录 `next build --webpack` 均通过；验证包括单击月付和年付不提交费用、日期联动与手动覆盖、套餐套用后的选中状态。本项仅修改前端交互，未重跑后端及全仓库检查；开始前的 `git pull --ff-only upstream CLIProxyAPI分支` 因 GitHub 连接超时失败，修改基于本地 `0c0dd5c0659`
