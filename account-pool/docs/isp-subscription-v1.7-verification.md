# ISP 订阅与链式代理 v1.7 验证记录

日期：2026-10-23

## 范围

合并 PR #1 的 ISP Clash 订阅、每端口 ISP/机场前置绑定、链路探测及 API/Dashboard 配置。保留原 Clash 配置来源、机场节点、普通代理模式、手工 ISP 设置、卡片选取和续聊/重试边界。订阅链路必须有管理员明确选择的可用机场前置；缺少前置时拒绝请求，不自动切换至直连。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| Manager 代理、配置、ISP 目录、探测和 PostgreSQL FK 补偿 | 121 passed；PostgreSQL 17.6，仅绑定 `127.0.0.1:55437` 的临时实例 |
| LiteLLM 管理 API、会话、续聊、额度切换和路由 | 220 passed |
| Manager 策略、管理设置及供应商适配 | 143 passed |
| Dashboard 代理 API/集成 | 32 passed |
| Dashboard 身份设置集成 | 26 passed |
| 发布、schema 兼容与部署版本 | 200 passed；包含发布器 ReleasePair 双镜像源码检查回归 |
| 涉及 Python 文件 Ruff | All checks passed |
| Dashboard 源码 TypeScript | exit 0 |
| Dashboard Next.js webpack 生产构建 | exit 0；54 个静态页面 |

发布回归曾在修复前观察到 4 个 `AttributeError: 'ReleasePair' object has no attribute 'image_id'`。修复后四种情况均按预期：精确 ISP schema additions 接受；Prisma 变化、额外 DDL、修改 DDL 拒绝。对应实现按 current/target 两个镜像分别读取 Manager 源码和 LiteLLM `schema.prisma`。

首次 GitHub 发布门禁的 1209 项通过、1 项跳过，但发现版本回归测试仍固定期待 `v1.6`。现已改为从 `deploy/VERSION` 动态读取当前系列，并据此计算下一 patch 版本；部署版本测试文件本地复跑 17 项通过。修复已单独提交并重推后，仍需以对应 GitHub 工作流结果确认发布。

UI ESLint exit 0，但有 31 条 warning。Manager 严格类型检查从隔离基线的 65 项诊断降低到 33 项，仍未通过；不把它描述为通过。

## 未执行的验收

没有真实 ISP 或机场节点 egress、真实供应商模型调用、生产数据库、线上容器或配置验收。GitHub 镜像发布只在推送后由现有工作流执行。成功不可变版本标签由工作流在所有构建和发布检查通过后生成；本机不创建标签、不部署。

新增 schema 仅允许精确审阅的 DDL 组合。此机制不等同于对全部 Manager 镜像源码作摘要锁定。
