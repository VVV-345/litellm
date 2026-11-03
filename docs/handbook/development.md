# 开发与验证

## 范围与起点

遵守[根目录规范](../../CLAUDE.md)、[Manager 规范](../../account-pool/AGENTS.md)及[前端规范](../../ui/litellm-dashboard/CLAUDE.md)。主分支为 `CLIProxyAPI分支`，除非明确要求，不新建分支或 worktree。保留用户已有改动，验证后先 commit，只有明确 push 指令才推送

以下在仓库根目录执行，只读检查不改变运行服务：

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
```

开始实现前按约定 `git pull --ff-only`，此命令会更新本地 Git 状态，不是部署。先写出假设、受影响边界和通过标准，修复先复现，结构调整不夹带默认值或 API 行为变化

## 启动与配置

Manager 的环境变量由 [Settings](../../account-pool/account_pool/config.py)读取，样例见 [Manager 环境样例](../../account-pool/.env.example)及[产品环境样例](../../deploy/.env.example)。数据库地址、Manager token、secret seed、SSH 回调配置是独立配置；不要打印真实 `.env`、完整容器环境或认证文件

开发启动示例在 `account-pool` 目录执行，**会修改本地环境并可能在数据库建表**，必须事先准备独立测试库、受控 Socket Proxy、网络和回调配置，不能指向生产库：

```sh
uv sync --locked --extra test
uv run uvicorn account_pool.app:create_app --factory --host 127.0.0.1 --port 8091
```

预期是 Manager 启动并能访问其配置的依赖，不代表 CPA 账号已授权。宿主机直跑仅适用于依赖已连通的开发环境，不得为了让命令成功而暴露 Docker Socket 或放宽网络。生产安装从[部署说明](../../deploy/DEPLOY.md)开始

前端在 `ui/litellm-dashboard` 目录按锁文件安装依赖后执行 `npm run dev`，会启动本地开发服务；脚本定义以 [package.json](../../ui/litellm-dashboard/package.json)为准。不要将开发服务器当成生产构建

## 按影响选测试

Python 命令在仓库根目录执行，使用已安装 Manager、LiteLLM 和测试依赖的解释器；以下会创建本地测试文件或测试数据，不应连接生产：

```sh
python -m pytest account-pool/tests/account_pool/test_runtime_rollout.py account-pool/tests/account_pool/test_runtime_maintenance.py account-pool/tests/account_pool/test_cliproxy_runtime_image.py -q
```

涉及数据库并发时另外使用明确隔离的 `ACCOUNT_POOL_TEST_DATABASE_URL` 运行 [PostgreSQL 测试](../../account-pool/tests/account_pool/test_runtime_rollout_postgres.py)。未配置而跳过不能写成通过。涉及 LiteLLM 接口的修改须补相应 [management_endpoints 测试](../../tests/test_litellm/proxy/management_endpoints/)，不能只测 Manager 内存实现

前端示例在 `ui/litellm-dashboard` 执行，会运行本地测试：

```sh
node node_modules/vitest/vitest.mjs run src/features/account-pool/components/credentials/AccountPoolAuthorizationPanel.integration.test.tsx
node node_modules/typescript/bin/tsc --noEmit
```

类型检查可能暴露既有失败，记录基线和新增差异，不为变绿删用例、全局忽略未知字段或放宽类型。生产构建另用 `npm run build`，会生成构建产物，不代表已发布镜像

## 通过标准与交接

记录提交、命令、输入、stdout、stderr、退出码、失败与跳过范围，区分静态检查、mock、真实 PostgreSQL、真实 Docker 和上游调用。文档修改只核对内容、链接、代码入口、CLI 语法和差异，不为写文档去部署、兑换或刷新 Token

验证通过后审查 `git diff --check` 和暂存范围，仅提交本任务文件。凭据、真实配置、数据库 dump、认证备份和 `.local` 证据不进提交。跨模块边界与既有验证经验查[维护历史](../../account-pool/MAINTENANCE.md)，故障恢复查[排查手册](troubleshooting.md)
