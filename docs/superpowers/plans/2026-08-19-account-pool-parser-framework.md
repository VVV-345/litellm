# Account Pool Phase 2 Parser Framework Implementation Plan

## Goal

完成 `account-pool/PLAN.md` Phase 2：让渠道解析器输出统一、可持久化、可人工修正且可安全导出的套餐与按量数据，
并让通用 OpenAI 兼容渠道在无法查询计费信息时保留模型发现结果和明确兜底状态

## Boundaries

- 解析器目录只处理对应上游的请求、响应 schema 和统一结果转换
- parser worker 负责选择解析器、运行、持久化和触发快照，不负责健康或调度
- PostgreSQL 保存权威 parser run、规范化结果和人工覆盖；JSON 只是完整脱敏投影
- API Key 只存在于受控请求生命周期，不进入 parser run、JSON、日志、任务参数或浏览器存储
- 未知值使用 null 或空集合，不用零、无限或公开标价代替账户实际数据

## Delivery Sequence

### 1. Unified Contract

- 定义套餐、额度窗口、按量分组、Decimal 价格、倍率、并发和可执行计费路由
- 定义 success、partial、unsupported、认证、传输、无效响应和人工处理状态
- 定义 unresolved field、脱敏证据和结构化问题报告

状态：已由 `2791f461a8` 交付

### 2. OpenAI-Compatible Parser

- 使用已验证的 `GET /models` 结果生成模型发现
- 套餐和按量保持为空，状态为 partial，并生成专用解析器或人工补充建议
- 不生成无法由 Account Pool 控制的 billing route
- 套餐、价格和余额不得从模型名称或公开页面猜测

状态：已由 `2791f461a8` 交付

### 3. Redacted JSON Snapshots

- 投影 schema version 1、raw/effective result、模型发现和安全问题报告
- 写入 `latest.json` 与按渠道、运行 ID 分类的 history
- 原子替换目标文件，失败作为值返回，供 worker 记录和重试
- 拒绝 URL、认证头、Cookie、credential reference、Key 指纹和原始响应

状态：已由 `7f7d69fc34` 交付

### 4. Parser Registry and Selection

- 实现显式 parser、Provider+origin、OpenAI 兼容、人工模板的固定选择顺序
- 不允许携带凭证遍历探测所有厂商
- GLM 迁移到统一解析器契约
- 为 registry、能力声明和 fallback 添加契约测试

状态：已由 `55cb7ff914` 交付

### 5. PostgreSQL Parser Runs and Worker

- 添加 parser run、规范化套餐/按量数据、问题报告和执行状态表
- worker 在事务中保存结果，提交后导出 JSON；导出失败记录事件并重试
- 通过幂等运行 ID 防止重复任务产生冲突结果
- 解析失败不得直接改变渠道健康状态

状态：已实现。Worker 核心和仓储已完成，管理 API 与常驻任务入口在第 8 步接入

### 6. Manual Overrides

- 覆盖层按字段记录值、原因、操作者、来源版本和时间
- 重新解析只更新 raw result，不覆盖人工值
- effective result 由 raw result 与有效覆盖确定性合成
- 导入 JSON 只能创建受控导入或覆盖记录，不能直接替换权威表

### 7. Provider-Specific Parsers

- 实现 OpenAI 官方平台可验证的账户能力
- 按公开且稳定的官方 API 决定是否扩展 GLM 套餐、余额或价格
- 每个厂商使用独立 schemas、client、parser 和脱敏 fixtures
- 厂商没有稳定接口时明确 unsupported，不使用网页抓取冒充正式能力

### 8. API and UI

- 提供 parser run 状态、raw/effective 差异、问题报告和重新解析接口
- 支持 latest/history 预览与脱敏导出
- 支持逐字段人工修正、撤销和审计
- 渠道页展示模型发现、套餐、按量和 billing route 是否可控

## Verification Gates

- 单元测试覆盖金额精度、窗口语义、失败分类、快照原子写入和敏感信息拒绝
- PostgreSQL 测试覆盖事务、幂等、覆盖层合成与 worker 重试
- API 测试证明凭证和 URL 不进入解析响应或导出
- UI 测试证明 raw/effective 差异、问题原因和人工修正可见
- 新厂商解析器必须通过统一 contract suite 才能进入 registry

## Deferred to Later Phases

健康状态、restriction、额度扣减、冷却、成本排序和调度解释仍属于 Phase 3/4。Phase 2 只生产可信解析证据，
不根据解析结果直接决定渠道可用性或请求路由
