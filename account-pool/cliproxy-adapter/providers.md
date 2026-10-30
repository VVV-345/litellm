# 九供应商设置映射

映射依据为 sources.json 固定的 CLIProxyAPI fork 和现有供应商注册表。复用已有配置入口与请求消费者，不复制 Cockpit 桌面页面，也不把 Codex 的安装身份字段套给其他供应商

| 供应商 | 当前通道和凭据 | 专属配置及处理边界 |
| --- | --- | --- |
| OpenAI-compatible | 独立 openai_compatible 通道 | API Base、请求头和 Key 留在原通道，通用卡片设置由网关执行，不写入 CLIProxyAPI |
| Codex | CLIProxyAPI OAuth | managed 安装身份、旧指纹模式、CLI-only、identity-confuse、disable-codex-cloaking |
| Claude | CLIProxyAPI OAuth | fingerprint_profile、cloak_mode、rebuild_mid_system_message；补丁修复属性热更新与重载同步 |
| Antigravity | CLIProxyAPI OAuth | antigravity.sensitive-words、签名缓存和严格校验开关，继续由原执行器与热重载消费 |
| Kimi | CLIProxyAPI device code | fingerprint_profile 只影响 Claude Messages 请求路径；设备 ID 仍由原认证流程维护 |
| xAI | CLIProxyAPI device code 或 Key | xai.inject-x-search 由现有执行器消费；直接 Key 仍走 xai-api-key |
| Gemini | CLIProxyAPI 直接 Key | gemini-api-key；通用路由、额度和重试留在网关 |
| Gemini Interactions | CLIProxyAPI 直接 Key | interactions-api-key、interactions 模型排除映射；不冒充 Gemini CLI OAuth |
| Vertex | CLIProxyAPI 凭据导入 | vertex/import；不能按 API-key 数组替换其凭据 |

所有通道保留原卡片权限、代理、额度、模型范围和隔离边界。CLIProxyAPI 的 request-retry=0、max-retry-credentials=1、streaming.bootstrap-retries=0，LiteLLM 是唯一重试所有者。现有请求输出后不可重放、工具和加密历史保护不变

Claude/Kimi 原有问题是 metadata 已保存但执行器优先读取的 Attributes 未同步。补丁使 fingerprint_profile 的 inherit、显式开启和 null 热更新及时生效；文件重载同样同步。Claude 的 rebuild_mid_system_message 从文件与 PATCH 同步 true/false，其他供应商不会收到这些属性

验证包括八家 CLIProxyAPI 注册供应商共用设置和重试契约、独立 compatible 通道既有测试、Claude/Kimi 实际 PATCH 处理与文件重载测试。真实供应商网络调用、真实额度和跨账号旧会话续聊未在本轮验收
