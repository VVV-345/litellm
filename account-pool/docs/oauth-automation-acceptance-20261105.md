# 授权自动化收尾验证（2026-11-05）

基线：`273b6eca166378ec5f42d6bc37e7f5dedff809b1`，工作分支：`CLIProxyAPI分支`。本次只做本地提交，不推送、不部署生产。沿用 [前次真实授权记录](oauth-automation-acceptance-20261104.md) 的隔离账号、卡片、Key、数据库与代理，没有再次登录、兑换回调或刷新凭据。

## 修复范围

- `AutomationRunner._verify`：默认模型选择跳过未到期的模型冷却；明确指定冷却模型时停止验收。冷却过期后可仅重试验收，不再次登录。上游额度、账号权限和模型限制不会因此解除。
- `AutomationWorker._infer`：失败保留 Chat/Responses 的 HTTP 状态，区分网络结果未知、格式无效、不完整文本和响应过大；不把上游原始错误正文写入任务消息。
- 修正旧契约夹具：access 降级默认开启（已保存的手动关闭值不变）、凭据刷新状态及消费字段。
- 仪表盘分页测试改用实际标签页入口，补齐 QueryClient；保留跨供应商分页、过滤和页大小断言。
- 必要的原生 UI lint 修复只复用条件结果、去掉已受保护的可选访问、为测试 mock 的大对象命名。不拆原生模块，不改变业务功能。
- 为依赖英文文案的测试显式设置语言；补齐旧 SSO、Key、完整日志类型夹具，删除 Testing Library 不支持的 `exact` 选项，没有修改全局语言或放宽类型。
- 固定 OpenAPI 快照生成器的 UTF-8/LF 输出，补充字节级回归，避免 Windows 自动转换成 CRLF 导致同步门禁误报；不手改生成文件。

## 已观察结果

| 检查 | 结果 |
| --- | --- |
| 原先失败的后端契约相关检查 | 37 项通过 |
| 自动化与原生代理管理入口回归 | 19 项通过，含冷却阻断、到期重试、不重登录和错误正文不泄露 |
| OpenAPI 快照回归与 API 类型再生成 | 7 项通过；快照与 schema.d.ts 再生成后字节哈希均等于 HEAD |
| UI 明确指定的相关回归 | 36 文件，651 项通过；不是全项目测试 |
| Python 变更源码类型 | 0 errors / 0 warnings |
| UI 源码与 28 个变更测试的独立类型检查 | 分别通过；不是用 Next build 替代测试类型检查 |
| 独立源码副本 `next build --webpack` | 通过，未覆盖工作区原有构建目录 |
| 全目录 lint 预算 | complexity 151→140，大内联对象 676→555，长条件 263→262；只下调预算、不放宽门禁 |

新 Manager、gateway 镜像已在本机原隔离栈构建和启动。固定同一卡片的 `gpt-5.6-luna`：

- 新自动化 Worker 的真实 Chat、Responses 均获得完整文本。
- 原 Key：Chat HTTP 200（3143 ms），Responses HTTP 200/completed（9288 ms）。
- 原生卡片模型测试：Chat HTTP 200/success（4606 ms），Responses HTTP 200/success（5298 ms）。

这些结果仅证明这张隔离卡、这个模型和本轮请求成功，不代表其他卡片、所有模型或将来的上游请求必然成功。前次真实登录及一次性回调结果保留；本轮只读确认已完成任务和 MFA claim，未重新验证 claim 签名，也不把它描述成新跑了一次 2FA 登录。

## 尚未通过或无法确认

1. **根目录 `make lint` / `make check` 未通过**：在一次性环境中实际执行，依赖构建退出 2。修正 Git 同名 `link` 干扰后，实际错误为 `linker link.exe not found`；机器缺少 MSVC C++ 链接工具，Rust 扩展未构建，后续全局 Python lint/类型门禁没有运行。`make check` 同样在 bootstrap 阶段停止，没有到达后续检查脚本。需要在具备 MSVC 的 Windows 环境或标准 Linux CI 再跑；不以局部检查冒充全局通过。
2. **全目录 ESLint 仍有 13 个原生历史错误**：与本次开始时相同（未使用 import、嵌套三元表达式、文件行数）。当前提交范围的 ESLint 和全目录预算需要分别核对，不能把预算通过说成全目录 ESLint 零错误。
3. **Spark 尚未验收成功**：隔离卡显示 `gpt-5.3-codex-spark` 在模型级冷却，原记录到 `2026-11-05 11:16:06 +08:00`，原因仅有 `upstream_error`。原始上游正文没有留存，不能断言是订阅权限还是上游故障；本轮没有清除冷却、绕过限制或重复登录。待正常冷却结束后，才能按同卡真实请求另行确认。

## 证据与复核

逐条命令、输入、stdout、stderr、退出码、镜像及源码哈希保存在本机 `.local/access-dashboard-buttons-20261104/remaining-fixes-20261105/` 与共用 `lifecycle-review/`，不提交账号、私有 Compose、Key 或令牌。

沿用原四角色：`MODIFIED_FILE.tar.gz`、`DIFF_FILE.patch`、`VERIFICATION.txt`、可执行 `ROLLBACK.sh`；本轮另验证同一组三个回归输入的 BASELINE 失败、MODIFIED 通过、独立 ROLLBACK 恢复失败，再核对恢复字节哈希并重新应用补丁。历史授权路由 404→200（默认关闭）→404 的证据保留，不冒充本轮新登录。
