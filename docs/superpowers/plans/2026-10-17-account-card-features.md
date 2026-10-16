# 号池卡片四项功能 Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development task-by-task. User request is the spec; do not expand into automatic conversation failover.

**Goal:** 显示出口延迟，新增协议多选与保留管控的透传，改善配置布局，修复连接测试，并支持分窗口额度比例。
**Architecture:** 复用现有探测、卡片策略、Manager 快照和 Router 管控；协议/额度设置必须实际生效。UI复用现有控件。原件与上轮成果保存不动，在既有 MODIFIED_FILE 副本开发。
**Tech Stack:** Python/Pydantic/FastAPI + React/Next.js + Vitest/pytest。
**Spec:** 用户 2026-10-17 四项要求及两个确认：出口探测延迟；按协议透传但保留鉴权、额度、日志计费。

## Global Constraints
- 不实施自动换卡重构；不推送、不部署。
- 原文错误不是根因，必须真实 Router 路径复现，不用纯 RouterDouble 证明修复。
- 不暴露凭据；透传不等于越过权限/额度/日志，也不承诺隐藏思考可跨上游移植。
- 旧设置兼容，协议未配置继承旧行为。额度新字段为空继承旧统一比例。
- 原四角色沿用，补充本轮证据；新旧测试分开报告。

## Task 1: 出口延迟显示
- [ ] 复用网关 delay/ISP probe 查询、缓存与刷新状态，避免每张卡发一次探测。
- [ ] 先测试真实数值、未知/失败/过期/换节点不显示旧延迟，再增加卡片展示。
- [ ] 精准 Vitest 和组件联动验证。

## Task 2: 协议与透传策略及配置布局
- [ ] 读取现有协议入口，增加明确多选类型与继承语义；透传只允许既有管控链路。
- [ ] API/持久化/快照/入口都验证，不仅保存字段。
- [ ] 配置弹窗使用受限高度 flex 布局，正文独立滚动、保存区可达；窄屏1列，桌面2列，复用主题/字体。
- [ ] 先测试选择持久化、入口拒绝及受控透传；再最小实现，更新生成 API 类型。

## Task 3: 连接测试
- [ ] 原有测试基线 + 真实 Router 请求路径复现当前失败。
- [ ] 定位根因后修复；返回脱敏可定位错误码与请求关联，失败不得伪造成功。
- [ ] 验证 Chat/Responses、流式/非流式、固定所选卡片、无自动回退。

## Task 4: 五小时/周额度
- [ ] 增加 quota_reserve_5h_percent / quota_reserve_weekly_percent（nullable 0..100），空值沿用原 quota_reserve_percent。
- [ ] 传递窗口身份及时长，按实际供应商窗口匹配，其他周期保留通用比例。
- [ ] Manager/Gateway/Router 使用一致判断；只调整阈值，不重做会话迁移。
- [ ] 验证两窗口不同阈值、0/100、旧配置、Codex/Claude、其他周期和未知额度。

## Final
- [ ] 精准后端/前端测试、源码检查、生成类型、可执行回滚和还原hash、补丁重构。
- [ ] 独立审阅；真实 UI 屏幕尺寸验证（能运行时）；明确未完成或环境限制。
