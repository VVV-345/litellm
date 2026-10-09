# 号池镜像自动更新实施计划

> **For agentic workers:** Use superpowers:subagent-driven-development for isolated UI and registry tasks; keep release transaction ownership in the controller

**Goal:** 管理员在号池设置中控制镜像自动更新开关和检查间隔，同镜像跳过，新镜像通过已有发布器部署

**Architecture:** release-worker 的单实例队列负责调度，SQLite 保存设置、授权和检查状态；LiteLLM 只代理固定管理接口。GitHub API 只读取开发分支及成功构建元数据，GHCR manifest 提供不可变摘要，服务器不拉源码

**Spec:** 本次对话中已确认的自动更新、设置界面及失败处理设计

## 全局约束

- 直接修改 CLIProxyAPI分支，不新建分支；完成测试后 push，不连接生产服务器
- 默认关闭；检查间隔默认 5 分钟，允许 5 至 1440 分钟；启用需要确认停机影响及已配置数据库备份
- 固定 VVV-345/litellm 的 CLIProxyAPI分支，仅接受分支当前提交的成功发布工作流
- 比较镜像 config digest，按平台 manifest digest 拉取并核对 image ID 和 commit，检查后标签变化不能替换成另一镜像
- 同版本不拉取、不建立发布任务；下载失败可以下轮重试，发布失败或手动回退后暂停自动部署
- 保留兼容性硬门槛、双库备份、回滚流程和手动确认接口；不扩大 Docker 权限
- 权限边界：浏览器不能提供仓库地址、镜像引用或发布令牌；只有管理员能修改全局授权
- 保留用户已有 tsconfig.tsbuildinfo 改动，不能将它提交

## 任务 1：持久化、授权和串行调度

修改 release_models.py、release_store.py、release_app.py、release_service.py，新增 release_auto_update.py

- [x] 先测试 GET /api/releases/auto-update 默认关闭、未鉴权 401、无确认启用 409、非法间隔 422、配置持久化
- [x] 实现 AutoUpdateSettings / AutoUpdateState / AutoUpdateView；独立 SQLite 单行表不改变业务数据库结构
- [x] 自动入队和手动入队使用同一 SQLite 写事务检查 queued/running；关闭或修改配置后，旧检查结果不能入队
- [x] 测试同镜像无 pull、不同镜像入队一次、关闭时不入队、重启保持状态、失败暂停及手动回退暂停
- [x] 数据库备份恢复旧服务后补健康验证，测试不健康不能声称恢复成功

## 任务 2：只读发布发现和固定摘要拉取

新增 release_catalog.py 及 test_release_catalog.py，修改 release_runtime.py 及对应测试

- [x] 测试分支 HEAD 未成功构建时无候选，拒绝错误分支/commit，不下载源码
- [x] 测试 Docker manifest verbose 的 amd64/arm64 选择、忽略 attestation、缺失和歧义拒绝
- [x] 返回 ReleaseCandidate(commit, images)，每个 RemoteReleaseImage 包含 service、manifest digest、image_id
- [x] 拉取使用固定仓库的 @sha256 引用；拉取后再次验证 commit/config digest

## 任务 3：管理员设置界面和代理

修改两端 release_models 同步契约、account_pool_releases.py、AccountPoolReleasesApi.ts、accountPoolQueryKeys.ts、AccountPoolSettingsOverview.tsx，新增 AccountPoolAutoUpdatePanel.tsx 及集成测试

- [x] 验证非管理员无法 GET/POST 自动更新接口；请求只转发到固定 release-worker 地址
- [x] 设置卡片显示开关、间隔、版本、阶段、最近/下次检查及错误；启用确认中断风险；保存和立即检查都有反馈
- [x] 重刷状态不覆盖未保存输入；关闭状态的立即检查不能触发部署
- [x] 重新生成 OpenAPI TypeScript，不手改 schema.d.ts

## 任务 4：验证和交付

- [x] 运行 Manager 全套、对应 LiteLLM release API、号池前端测试及定向静态检查
- [ ] 在受控临时目录执行 baseline / modified / rollback 对照，保存补丁和日志，复核既有四角色产物
- [x] 独立审查发布安全和并发边界；记录未覆盖的真实 Docker/数据库及生产验收
- [ ] 校验当前分支和用户文件哈希，仅提交本次文件，push 开发分支并核实远端 commit

## 本地验证记录

Manager 664 项通过、3 项外部服务测试跳过；号池网关 332 项通过、1 项真实数据库测试跳过；前端 31 文件 171 项通过，补齐生成契约后的发布界面 22 项再次通过。生成 OpenAPI 已更新。未操作生产服务器，未执行真实 Docker 镜像切换

后端定向类型检查剩余 5 条既有诊断，已用起点 commit 副本复核。全量前端类型检查存在既有测试诊断，新增任务记录字段的测试夹具已补齐；不宣称全项目类型检查或生产构建通过。受控事务及各次实际输出保存在既有事务目录的 auto-update-20261010 补充记录中
