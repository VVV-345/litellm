# 两类上游的更新与维护

## 2026-10-17：加密思考续聊修复的定向移植

本次以业务提交 `57895bd24a8cd378b09ca057e45fce39714eb8ba` 为基线，只移植以下官方修复及必需的共享方法，不整体升级官方分支：

| 官方 PR | 合并提交 | 接入行为 |
| --- | --- | --- |
| [#43015](https://github.com/BerriAI/litellm/pull/43015) | `e450f2d54c113a962c38e32cf3d0d29341991c3e` | 普通路由的原部署不可用且无同凭据边界候选时，移除不兼容的加密 reasoning，保留可读历史；支持运行时关闭全局加密亲和性 |
| [#43781](https://github.com/BerriAI/litellm/pull/43781) | `2ed9761921aae3062c28b861ca2fa3eb8006554e` | 针对已选部署筛除混合来源的 reasoning，保留可接受的加密内容与其他条目的可读摘要 |
| [#45393](https://github.com/BerriAI/litellm/pull/45393) | `8a176c0f0ac8c82b02b5c13dde894091a4846b0f` | fallback 按目标凭据边界清理思考，尊重目标 order；流式回退保留来源身份；过滤客户端伪造的 fallback 控制字段 |

号池兼容边界：保留 `EncryptedContentOriginUnavailableError` 及 `Conversation` 的受控恢复路径。号池缺少可用原卡时，仍由已有权限范围、换卡开关、额度状态和历史完整性校验决定是否重建，不直接放行其他健康卡片。原卡仍可用时应用官方逐项筛除逻辑。关闭全局加密亲和性不会关闭模型组单独启用的设置，也不会删除其他 Router 仍需使用的回调

本分支仍使用原有私有方法名和流式封装接口，未引入官方无关的批量重命名。未改变数据库、持久化配置、鉴权、计费、网关同卡重试次数或已输出后不再重放的号池规则。带 `previous_response_id` 的号池恢复仍要求现有可用历史缓存

回归覆盖：原部署不可用、未知来源、混合来源、同凭据边界保留、order 切换、原生与桥接流式回退、工具调用及结果保留、运行时开关、客户端控制字段过滤，以及现有号池额度/权限/计费/不可重放请求测试

这次接入不解密外站隐藏思考，也未扩展本地 `unsupported_history:input[0]:invalid_item_or_tools` 校验器的格式支持。自动化测试使用替代上游，不代表外站真实旧会话或生产服务器已经验收。提交、镜像构建、部署和真实会话验证须分别确认

上游检测、兼容合并、构建镜像和服务器部署是四个独立步骤。页面中的 `current_tag` 是管理员配置的官方基线，不是自动读取的服务器版本；调整它不会更新代码或镜像

## 仓库和分支归属

| 项目 | 官方源 | 我们的 fork | 兼容分支 | 正式接收分支 | 工作流执行 ref |
| --- | --- | --- | --- | --- | --- |
| LiteLLM | `BerriAI/litellm` 正式 release tag | `VVV-345/litellm` | `codex/litellm-upstream-sync` | `CLIProxyAPI分支` | `CLIProxyAPI分支` |
| CLIProxyAPI | `router-for-me/CLIProxyAPI` 正式 release tag | `VVV-345/CLIProxyAPI` | `codex/upstream-sync` | `main` | `main` |

`VVV-345/litellm` 中的 `litellm_internal_staging` 是自己 fork 里的分支，当前保留作旧上游参考和 GitHub 默认分支。名字相同不等于直接指向官方，也不会自动跟随官方。可以另行同步它，但兼容工作流直接 fetch 官方精确 tag，不以这个参考分支的内容作为更新来源

CLIProxyAPI 的 `codex/upstream-sync` 不是 LiteLLM 的分支。它的 Go 编译、测试、promote 及镜像发布实现由 CLIProxyAPI 仓库维护；本次仅维护 Manager 对它的配置检查、调度及展示，不修改远端 CLIProxyAPI 工作流

## 本次交付边界

本次只完善 `CLIProxyAPI分支` 的代码、工作流和说明。不创建兼容分支，不更新 `litellm_internal_staging`，不调整默认分支，不合并官方版本，也不执行服务器发布

因此 LiteLLM 页可能正常显示“配置未完成”。这是准确状态，不是检测到新版本后就可以点击上线。完成下面的单独启用步骤前，更新按钮应保持禁用

## LiteLLM 单独启用步骤

先确认业务改动已完成并得到维护者同意，再从最新 `CLIProxyAPI分支` 初始化 `codex/litellm-upstream-sync`。不要从旧 `litellm_internal_staging` 初始化，否则会丢失号池业务基线。已有兼容分支先保存其提交和修复，不强制覆盖

GitHub `workflow_dispatch` 要求工作流在默认分支注册。只把 `litellm-upstream-sync.yml` 推到业务分支不能保证注册成功。默认分支仍为 `litellm_internal_staging` 时，需要另行审阅一个仅加入工作流的提交，或者单独批准调整默认分支；本次不代做这两个选择。实际执行 ref 仍使用业务分支

在部署的 Manager 中分别配置两类 GitHub Token。查询 release、分支、工作流和报告需要读取权限；dispatch 需要目标仓库 Actions write。工作流创建 PR 还需要仓库允许 Actions 创建 PR。GET 检查不能证明写权限，真实 dispatch 返回 204 并出现相同 request_id 的 run 才是调度证据

| 配置前缀 | 默认 workflow | 默认 workflow ref |
| --- | --- | --- |
| `ACCOUNT_POOL_UPSTREAM_SYNC_` | `upstream-sync.yml` | `main` |
| `ACCOUNT_POOL_LITELLM_UPSTREAM_SYNC_` | `litellm-upstream-sync.yml` | `CLIProxyAPI分支` |

两组均有 `GITHUB_TOKEN`、`CURRENT_TAG`、`BRANCH`、`WORKFLOW`、`WORKFLOW_REF` 配置。LiteLLM 工作流固定用于上表的 fork、兼容分支及执行 ref，不支持通过改环境变量把它变成任意仓库执行器。旧 `.env` 中显式配置的 LiteLLM `WORKFLOW_REF=main` 需要改正，默认值不会覆盖它

Compose 已引用两组 Token 和必要的版本/分支字段，现有服务器的旧文件仍须逐项核对。不要输出 Token 或提交 `.env`。按 [部署操作手册](../../deploy/UPDATE_RUNBOOK.md) 验证容器实际收到的配置；仅 restart 不会刷新容器环境

## LiteLLM 更新顺序

1. 点击“检测并隔离分析”，Manager 读取官方最新正式 release，校验配置后生成 UUID，并调度业务分支上的工作流
2. `prepare` 在一次性副本读取业务 SHA、兼容分支 SHA 和精确 tag SHA，合并官方 tag。发生冲突时中止 merge，记录冲突文件，不修改业务分支
3. `validate` 在另一台临时 runner 恢复固定候选，执行锁定依赖安装、Manager 测试、网关边界测试、迁移静态检查、限定前端测试、Next 构建，以及三个 amd64 Docker 构建，不推镜像
4. `publish` 在新的可信 runner 只读取 prepare 的不可变 artifact ID 和 GitHub 任务结论，不使用 validate 产生的文件，不执行候选代码。再次核对业务基线及兼容分支租约，发布兼容报告
5. 冲突或失败后，下载 Codex 审查包，在同一兼容分支修复并重新 analyze。相同基线和 tag 的修复会保留；若业务基线或 tag 已改变，先备份、人工协调旧候选，不能让工作流静默重置
6. `passed` 且报告未过期时点击“创建更新 PR”。工作流重新检查候选与旧报告的代码差异并复测，新建面向 `CLIProxyAPI分支` 的草稿 PR，或复用已有 PR（不改变其草稿状态）。不会自动 merge；成功记录 `pr_created` 和链接，失败记录失败步骤
7. 人工检查 PR 的当前 head/base SHA、真实服务与数据库验收、迁移和回滚证据，再决定合并。兼容分支可能长期存在，但每次基线或 tag 变化都必须重新协调和验证

PR 创建后新增的报告提交只改变两个证据文件，不算已重新验证业务代码。业务或候选代码在 PR 创建后改变时应重新分析，不能沿用旧的通过结果。当前未新增强制分支保护规则，维护者必须落实这一步

`passed` 只表示配置的检查通过。没有测试数据库的 PostgreSQL 集成用例可能跳过，工作流也没有替你验证真实 OAuth、真实供应商 API、数据库迁移/恢复和 arm64 镜像。正式发布按 [部署验收与回滚](../../deploy/UPDATE_RUNBOOK.md) 完成这些门槛

## 配置及失败排查

| 状态/字段 | 含义与处理 |
| --- | --- |
| `token_missing` | 当前目标未配置 Token |
| `production_branch_missing` / `sync_branch_missing` | 分支不存在或当前凭据看不到，先核对仓库与访问范围 |
| `workflow_missing` | 执行 ref 上不存在工作流 |
| `workflow_not_registered` | GitHub 未注册或禁用了工作流，核对默认分支 |
| `workflow_target_mismatch` | LiteLLM 配置不匹配工作流固定目标 |
| `configuration_unavailable` | 请求、权限、限流或响应异常，不能当成“没有更新” |
| `report_stale` | 报告 base SHA 已不等于业务分支，阻止 promote |
| `failed` / `conflict` | 查看对应 Actions 日志及报告，不继续正式更新 |

页面按 request_id 查询最近 100 个 dispatch run，准备失败、取消、租约冲突或报告发布失败不会一直伪装成排队。GitHub 尚未列出任务时可点“查看 GitHub Actions”。“停止本页等待”只解除本页跟踪，不取消远端任务；重试前先确认原任务是否仍在执行

兼容报告保存在各自仓库兼容分支的 `.codex/upstream-sync-status.json` 和 `.codex/upstream-sync-review.md`。发布被租约拒绝时，分支上仍是旧报告，应以 request_id 对应的 Actions run 为准。不要用旧通过状态覆盖新失败

## 维护入口与本地验证

Manager 入口为 `account_pool/upstream_sync.py`、`upstream_readiness.py`、`management_api.py` 和 `config.py`；网关入口为 `account_pool_management.py` / `account_pool_management_models.py`；页面入口为 `AccountPoolUpstreamSyncPanel.tsx`。改契约时同步这三层和中英文文案，并运行 `npm run gen:api`，不要手改 `schema.d.ts`

在仓库根目录执行；Manager 和 LiteLLM 分别使用各自锁文件创建的虚拟环境，下面的 Python 要替换为对应环境解释器

```bash
python -m pytest account-pool/tests/account_pool/test_upstream_sync.py -q
python -m pytest --noconftest tests/local_testing/test_litellm_upstream_runner.py -q
python -m pytest tests/test_litellm/proxy/management_endpoints/test_account_pool_releases.py -q
cd ui/litellm-dashboard
npx vitest run --project component src/features/account-pool/components/upstream/AccountPoolUpstreamSyncPanel.test.tsx
npx vitest run --project unit src/features/account-pool/api/AccountPoolManagementApi.test.ts
npm run gen:api
npm run build
```

Runner 测试使用本地 Git 仓库和替代 GitHub CLI 回调，不连接 GitHub 或生产。部署镜像由业务分支的 `publish-deployment-images.yml` 构建；构建成功后，只有已启用的服务器自动发布器才可能继续部署。两者不能混为一次操作


## 官方工作流文件的权限边界

默认 GITHUB_TOKEN 不具有修改 `.github/workflows/` 的额外 Workflows 权限。当候选相对兼容分支改变这些文件时，发布器明确阻止 push，Actions 保留 `prepared` 产物和失败 `result.json`，标记 `workflow_file_publication`。业务分支和旧兼容报告不变，不能把候选验证通过当作已发布

这类更新需要维护者下载同一 run 的准备产物，核对 base/target/candidate SHA 和 merge diff，然后在隔离副本人工协调工作流变更。人工发布需另行批准的凭据及 Workflows 写权限，不复用或扩大 Manager 的 dispatch Token。本功能不会自动获取这类权限。保留修复后在兼容分支重新 analyze，按新的结果审查 PR
