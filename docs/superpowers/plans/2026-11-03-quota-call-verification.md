# 普通 API 调用验证回写

本次补齐用户确认的第一项：额度恢复的管理验证之后，由真实普通请求完成事件回写调用证据。基线 a88de2f658，工作目录 D:/Work2api/litellm，仍在 CLIProxyAPI分支，仅提交、不推送、不部署

## 实现

LiteLLM 完成上报新增 response_completed，默认 false。Responses 必须有 completed 状态及输出，Chat Completions 必须有正常 finish_reason；流式请求还必须收到结束事件。裸 HTTP 200、流内错误、缺少结束事件或 response.incomplete 不构成调用成功证据。实际转发和现有计费、重试规则保持不变

GatewayService.finish 在原有日志写入和租约释放之后调用回写入口。身份取租约的 account_id、model、request_id，不使用请求来源卡片冒充实际选中的账号。重试、失败及非目标接口不回写；回写故障记警告，不占住租约、不再次计费

现有 account_pool_quota_verifications 表增加可空 api_call 列。行锁内核对最近管理验证、模型和请求开始时间；旧在途请求不能写入新验证，其他账号/模型不能串记录。每次新管理验证清空旧调用证据。旧版两列 INSERT 和 payload 解析保持可用，发布门禁只放行精确的新增列语句

状态接口新增 last_api_call，携带模型、请求 ID、接口、验证关联时间及完成时间。页面重开显示“普通 API 调用已验证”，未获得实际调用证据时保留“普通 API 调用仍需确认”。这是带时间的历史成功证据，不保证以后的请求永远成功

不主动发真实供应商请求，不更换卡片，不刷新 Token，不消费重置次数。HTTP Chat Completions 与 Responses 在本轮范围内，WebSocket 和 Messages 未新增回写证明

## 本地验证

命令、stdout、stderr 与退出码保存在 D:/Work2api/litellm/.local/quota-call-20261103 的 JSON 记录中

| 范围 | 结果 |
| --- | --- |
| 完成证明测试，修改前 | 4 个正例缺失证明；另有 1 个测试错误假定失败请求只尝试一次，已改为逐次检查 |
| 完成证明测试，修改后 | 12 passed |
| Manager、重置、发布回归，含隔离 PostgreSQL | 450 passed |
| LiteLLM 网关、管理接口、流解析 | 395 passed |
| HTTP 网关到 Manager 路由、PostgreSQL 及状态查询 | 4 个端点/传输组合通过；仅供应商响应为本地模拟 |
| UI 定向测试 | 9 passed |
| 源码及相关测试 TypeScript | 分别 exit 0 |
| 变更 Python Ruff、9 个核心文件严格类型检查 | exit 0 |
| UI ESLint | 0 errors、4 warnings，包含现有复杂度及新增条件/fixture 提示 |
| 隔离 Dashboard Webpack 生产构建 | exit 0，54 个静态页面生成 |

类型检查初次配置缺少仓库根目录，造成 signature 模块导入类型未知；补齐检查路径后通过。收集输出时曾因宿主 GBK 编码失败，保留 JSON 结果并改用 PYTHONUTF8 继续。首次查询错误文件路径、一次空补丁匹配失败均未改变源码，随后使用实际模块路径完成检查

复用四角色工件并保留历史：
- D:/Work2api/litellm/.local/quota-reset-20261102/transaction/MODIFIED_FILE.tar.gz
- D:/Work2api/litellm/.local/quota-reset-20261102/transaction/DIFF_FILE.patch
- D:/Work2api/litellm/.local/quota-reset-20261102/transaction/VERIFICATION.txt
- D:/Work2api/litellm/.local/quota-reset-20261102/transaction/ROLLBACK.sh

## 尚未执行

没有 push、生产部署或线上验收。上线后仍需使用第一张卡进行普通真实调用，检查页面请求 ID 与日志/账单对应，再做重启复验和真实自然周期观察。本地回写测试不是上游额度已经恢复的证据
