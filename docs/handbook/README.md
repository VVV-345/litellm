# 使用与维护总手册

本手册是本分支的统一入口，按真实职责说明号池扩展及其与 LiteLLM 的交界，不为每个源码文件建立一份说明，也不代替上游 LiteLLM 的全部供应商文档

源码基点为 `9133852197`。其中的逐卡升级机制已完成本地回归，尚未在生产发布验收；源码存在、commit、镜像发布、业务部署、逐卡换镜像和真实模型调用是分别确认的结果

## 先按任务找入口

| 任务 | 手册 |
| --- | --- |
| 开发环境、修改范围和验证方法 | [开发与验证](development.md) |
| 安装、四组件镜像、逐卡升级、同版本续跑和失败恢复 | [部署升级与回滚](deployment.md) |
| 两库、配置、镜像、认证卷分别如何保护 | [数据与备份边界](data-and-backups.md) |
| 已报错但不清楚属于哪一层 | [跨模块故障排查](troubleshooting.md) |

## 按职责查模块

| 模块 | 负责 | 不负责 |
| --- | --- | --- |
| [卡片与环境](modules/environments.md) | 不可变 ID、生命周期、配置对账、创建与清理 | 以页面状态替代容器实况 |
| [凭据、OAuth 与上号](modules/credentials.md) | 唯一归属、手动回调、可选受控浏览器、导入及刷新协调 | 自动修复已被上游撤销的令牌 |
| [请求分配与结算](modules/routing.md) | 鉴权范围、模型准入、租约、粘性、限流与安全重试 | 在 Manager 转发推理正文 |
| [额度、冷却与恢复](modules/quota.md) | 供应商解析、调度、兑换身份、验证及调用证据 | 看到 0% 就无条件重置或解锁整卡 |
| [出站代理与 ISP](modules/proxies.md) | Clash 网关、代理目录、可选前置、订阅和探测 | 把入站路由与出站代理混用 |
| [设置、策略与插件](modules/settings-plugins.md) | 默认值、命名配置、单卡策略、运行时同步、插件清单 | 用全局保存覆盖单卡独立设置 |
| [日志与共享反馈](modules/observability-feedback.md) | 请求归属、错误及完整日志、反馈 token 和 revision | 自动执行反馈正文或自动部署、关单 |
| [两类上游同步](modules/upstream-sync.md) | 分析、审阅、晋升、固定源码及适配补丁 | 直接更换线上每张卡的容器 |
| [Dashboard 与 API 集成](modules/dashboard-api.md) | 页面装配、管理鉴权、接口契约与缓存 | 复制一套原生 LiteLLM 鉴权、计价或控件 |

## 三条链路分开看

```text
控制面：Dashboard -> LiteLLM 管理接口 -> Manager -> Compose / CPA 管理接口
数据面：调用方 -> LiteLLM 鉴权与网关 -> CPA 或兼容上游 -> 模型供应商
准入面：LiteLLM 网关 <-> Manager 租约、模型限制、结算与调用证据
发布面：release-worker -> 配套预检、备份与业务切换 -> Manager 显式逐卡阶段
```

卡片状态、配置版本、容器进程、实际镜像、唯一真实凭据和推理结果都要分别确认。网站健康并不能证明两张已有卡已换新镜像；第一张升级失败时，新流程会停止后续卡片

## 权威详解与旧入口

生产操作以[统一更新手册](../../deploy/UPDATE_RUNBOOK.md)为执行门禁，[发布机制](../../deploy/RELEASES.md)、[统一镜像](../../deploy/UNIFIED_IMAGES.md)、[迁移手册](../../deploy/MIGRATION.md)提供对应细节

既有[维护历史与目录图](../../account-pool/MAINTENANCE.md)、[凭据独占说明](../../account-pool/CREDENTIAL_OWNERSHIP.md)、[上号说明](../../account-pool/ONBOARDING.md)、[上游维护流程](../../account-pool/docs/upstream-updates.md)和[共享反馈工作流](../FEEDBACK_WORKFLOW.md)保留原地址，避免破坏现有引用。带日期的测试数量与线上状态都是历史记录，不作为当前版本的保证

新增使用和维护说明集中放在这里；代码目录的 README 只提供导航。行为变更应同步修改所属章节和测试，不在多处维护同一操作流程
