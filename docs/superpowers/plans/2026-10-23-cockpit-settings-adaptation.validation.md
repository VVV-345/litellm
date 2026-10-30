# Cockpit 设置适配本地验证记录

本轮在 `CLIProxyAPI分支` 执行，起点为 `71a52d51eb526121136132d8491dee508d53800d`。来源固定提交见 `account-pool/cliproxy-adapter/sources.json`，没有再次拉取移动版本，也没有更换默认容器镜像

源码与来源提交为 `f1289af7b3`，底层补丁提交为 `24b49b9cb0`，Manager/UI 提交为 `a06aa13653`。本记录作为最后一个本地验证阶段提交，不推送、不部署

## 实际改动

CodexPolicy 与代理镜像模型新增 `identity_fingerprint_mode=managed`，默认 off 和旧 device/session/full 模式不变。`identity_confuse` 沿用已有显式设置，安装字段在同时开启时以托管档案为准，不增加另一套重试

底层直接复用 Cockpit 的 managed_identity.go，新增认证卷持久化及 HTTP/SSE/compact/WS/通用入口接入。Manager 先检查鉴权能力接口，未升级的底层不能假成功。原有卡片不自动迁移或开启功能

Claude/Kimi 的 fingerprint_profile 在 PATCH 和文件重载时同步到执行器属性，Claude 的 rebuild_mid_system_message 同步 true/false。其余供应商复用已有真实设置入口，九供应商边界见 `account-pool/cliproxy-adapter/providers.md`，没有把 Codex 安装身份冒充成其他供应商功能

## 本轮命令和结果

完整命令、输入、逐字输出、退出状态和哈希位于下面固定的 VERIFICATION.txt；早先的失败日志仍保留，不把工具链错误当作测试 RED

| 范围 | 入口 | 观察结果 |
| --- | --- | --- |
| 补丁可重复应用 | `python account-pool/cliproxy-adapter/apply.py SOURCE_DIR` | 干净固定提交副本应用成功，17 个 Go 文件字节匹配；重复应用明确拒绝 |
| Go 定向回归 | README 中的五包 `go test` 命令 | exit 0，包含安装身份、旧模式、Claude/Kimi 属性同步与能力接口鉴权 |
| Go server 构建 | `go build -o ../cliproxy-adapted.exe ./cmd/server` | exit 0，87,452,160 字节，SHA-256 `71f15fb545c23d15af4e1de392a0704b8a347184e857ec441873b5c228d2e96c` |
| Manager | policies、cliproxy_supplier_client、management、settings、openai_compatible、channel_registry 六个测试文件 | 174 passed，1 条框架弃用警告 |
| 网关边界 | gateway、session、conversation、quota_failover、integration、management 六个测试文件 | 466 passed，94 条既有框架警告 |
| UI | RuntimePolicyDialog.integration.test.tsx | 13 passed |
| API schema | `npm run gen:api` | exit 0，类型由脚本生成 |
| 生产源码类型 | source-only tsconfig 的 `tsc --noEmit --incremental false` | exit 0，不代表 Next build 或完整测试类型项目 |
| Python / UI lint | 相关源文件和新增测试的 Ruff、触及 UI 文件的 ESLint | Ruff 通过，ESLint 0 错误、15 条未改动相邻代码警告 |
| Python 严格类型 | 相同五个源码文件的 basedpyright 基线与改版比较 | 均为 118 项诊断，新增 0，不称类型检查全绿；apply.py 独立检查为 0 错误 |

网关第一次运行未带 Manager 导入路径，465 passed、1 项 ModuleNotFoundError；补充 `PYTHONPATH=D:\Work2api\litellm\account-pool` 后完整重跑为 466 passed，未修改生产导入逻辑

授权视图契约测试期望缺少原有 spend_usd 字段，原版 `71a52d51eb` 与改版均失败。本次不修改这个无关测试，不宣称完整仓库测试全绿

## 同输入副本事务

输入与探针固定在 `D:/Work2api/litellm/.local/cockpit-adaptation/transaction/`。BASELINE、MODIFIED、ROLLBACK 使用相同策略探针命令、同一个 INPUT.json，以及同一 Go 测试命令 `go test ./internal/runtime/executor -run ^TestTransactionManagedIdentityProbe$ -count=1 -v`

| 副本 | 策略模型 | 真实 localhost 上游观察 |
| --- | --- | --- |
| BASELINE | managed 被拒绝，exit 2；默认 off，旧枚举可用 | 传入安装标识和 Cookie 保留，未产生托管账号隔离；会话和密文内容未变，Go exit 0 |
| MODIFIED | managed 与 identity_confuse=true 可保存，exit 0；默认 off 未变 | 安装字段使用持久身份，Cookie 移除；刷新后稳定，不同账号隔离；会话和密文内容未变，Go exit 0 |
| ROLLBACK | 与 BASELINE 相同，exit 2 | 与 BASELINE 相同，Go exit 0；恢复后的原始字节哈希一致 |

回滚只作用于单独的改版副本，未对主工作区执行。ROLLBACK.sh 内嵌基线，接受带 project/ 和 cliproxyapi/ 的目标副本，需可用的 Python 3，可通过 PYTHON 环境变量指定解释器。它通过 bash 语法检查、可执行检查和真实执行

38 个受影响路径包含 21 个项目文件与 17 个 CPA 文件。原版与回滚范围摘要均为 `b50d305b2da3859f57bee05cf824fe4b599a359576e0acec7376a751b7dcdb9b`，改版摘要为 `4ac1faffc9bd4b50a7ecdd708a6ab10d3166153c0e45c6ab9346701b8115152c`。补丁在一次性原版副本重建成功，字节匹配改版，随后重新生成成品归档

四个固定角色均已重新打开，归档是受影响文件的覆盖包，不是完整仓库副本；配合原项目和固定 CPA 源码使用。补充日志不替换这些角色

```text
MODIFIED_FILE: D:/Work2api/litellm/.local/cockpit-adaptation/transaction/MODIFIED_FILE.tar
DIFF_FILE: D:/Work2api/litellm/.local/cockpit-adaptation/transaction/DIFF_FILE.patch
VERIFICATION: D:/Work2api/litellm/.local/cockpit-adaptation/transaction/VERIFICATION.txt
ROLLBACK: D:/Work2api/litellm/.local/cockpit-adaptation/transaction/ROLLBACK.sh
```

独立静态审查未发现高置信阻断问题，确认全部六个新增 Go 文件已打入补丁。审查者未独立运行测试，不能代替上述命令结果

## 未覆盖和当前状态

没有线上部署、推送、真实供应商付费调用、真实跨账号旧密文续聊或生产浏览器验收。没有运行整个仓库测试、完整 Go 测试、Next production build 或竞态检测。保留其他来源的未跟踪文件

代码可构建且本轮范围内回归完成，但线上功能尚未生效。选择 managed 前需要后续单独升级对应 CLIProxyAPI 底层。安装身份不能跨账号解密，也不承诺消除风控

将来才考虑 LiteLLM 保留统一入口、Cockpit 替换重叠账号池与请求引擎的整体方案，本轮未合并或叠加第二套引擎
