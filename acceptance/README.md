# 项目验收

本目录管理验收范围、执行脚本、每轮证据和遗留问题，不替代已有单元测试，也不把模拟上游结果当成生产验收

本轮报告：[验收报告](reports/2026-10-12/ACCEPTANCE_REPORT.md)，[维护问题清单](reports/2026-10-12/MAINTENANCE_ISSUES.md)

## 目录

| 文件或目录 | 用途 |
| --- | --- |
| `matrix.json` | 固定验收项、实现状态、通过标准和测试入口 |
| `run.py` | 执行选定检查，保存退出码及输出，生成报告 |
| `check_auth_files.py` | 在本地校验用户提供的认证文件，不上传或展示凭据 |
| `live_check.py` | 对指定网站执行代理检查和单文件导入验收 |
| `tests/` | 验收工具自身的测试，业务回归继续复用原测试目录 |
| `reports/` | 经脱敏审阅、可提交的验收结论与问题清单 |
| `runs/` | 每轮原始输出和机器可读结果，默认不提交 |
| `transactions/` | 修复副本、可重建补丁、验证记录及回滚工具 |

## 状态约定

实现状态与验收状态分开：实现状态为 `implemented`、`not_implemented`、`unknown`；验收状态为 `passed`、`failed`、`partial`、`not_run`、`blocked`、`excluded`

跳过用例只能算部分通过，未测试不能写成未实现。报告必须记录提交、环境、命令、退出码、证据、问题原因与后续动作。历史结果标记为历史，不计入本轮通过数。未实现必须有源码或接口证据

## 本轮边界

目标为用户指定的现有号池网站。认证文件从外部目录读取，先验证两个文件，再至多导入一个未重复文件；不修改原文件、不覆盖现有账号、不测试 OAuth 上号、不执行付费推理、不自动部署或回退生产服务

真实账号的文件名、邮箱、Token、完整认证 JSON、代理凭据和管理密钥不得写入 Git、报告或终端输出。真实接口只保存白名单摘要。原始日志留在忽略目录，提交报告前仍须人工检查

代理测速失败要区分鉴权、路由、控制器连接、节点探测、格式解析和前端展示。失败或超时不得转为零延迟；测速目标延迟不等同于模型首 Token 延迟

## 执行命令

在仓库根目录使用能导入 LiteLLM 和 Manager 的 Python 环境，当前验证环境为 Python 3.12、pytest 9.1.1、pytest-asyncio 1.4.0。第一次运行先确认已有依赖，不在验收脚本里自动安装

```powershell
python acceptance/run.py --cases harness manager gateway dashboard types --run-id RUN_ID
python acceptance/check_auth_files.py '外部认证文件目录'
```

`RUN_ID` 每次使用新值，已有记录不覆盖。选定检查含失败、阻塞或跳过时执行器退出非零，但继续记录后续检查。完整必测清单另含真实服务，局部命令通过不代表正式上线通过

真实网页验收使用 `--manual ID STATUS EVIDENCE` 登记，不把真实浏览器状态伪造成 pytest 通过：

```powershell
python acceptance/run.py --cases harness --run-id 20261012-live --manual live-import passed 'acceptance/reports/2026-10-12/import-ready-redacted.png 与 live/import-result.json'
python acceptance/run.py --cases harness --run-id 20261012-proxy --manual live-proxy failed 'acceptance/runs/20261012-live/proxy-delay.json，20 个端点均为 error/null'
```

每个 ID 必须对应 `matrix.json` 中无命令、未排除的手工项。证据文本落在原始结果目录，由报告引用。请先检查证据内容，禁止记录账号身份、Token、密钥或原始认证文件

真实 API 复测使用当前站点管理员 Token，通过进程环境变量 `ACCEPTANCE_ACCESS_TOKEN` 传入，不写命令行、文档或 Git。只接受显式 HTTPS origin，不跟随重定向

```powershell
python acceptance/live_check.py proxy --base-url https://YOUR_HOST
python acceptance/live_check.py import --base-url https://YOUR_HOST --file '单个认证文件.json'
python acceptance/live_check.py import --base-url https://YOUR_HOST --file '单个认证文件.json' --submit
```

默认只预览，`--submit` 才创建任务与卡片。提交后最多等待 120 秒；若轮询中断，先按输出的任务 ID 查看原任务，禁止无依据重复导入。脚本不删除卡片、不调用模型推理、不测试 OAuth；端到端网页上传仍需按报告的页面路径验证

## 发布门槛

本地回归、真实导入状态、真实代理探测、数据库持久化与恢复、权限隔离、计费、容量和失败恢复分别验收。必测项失败或缺少证据时不签署正式全量上线通过。OAuth 本轮明确排除，未验收前保持关闭
