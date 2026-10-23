# 九供应商设置映射

依据为 `account_pool/channels/cliproxyapi/suppliers/registry.py`、`AccountPolicy`、`settings_sync.py` 和 `sources.json` 固定的 CLIProxyAPI commit `b3c954b63df09156ac2ac52ad038b3dc3a20748f`。本表中的 `provider_settings` 只表示单卡供应商专属策略字段；不代表通用卡片路由、额度、重试、传输设置或凭据接入。

## 共用设置

九类供应商都通过 LiteLLM 网关执行卡片授权范围、模型、额度、错误分类、换卡、续聊和请求重试。卡片策略保存于 `account_pool_policies`；通用路由、模型、quota、retry、timeout、client、image、WebSocket 与日志能力不随本表的 `provider_settings` 状态改变。CLIProxyAPI 同步的 `request-retry=0`、`max-retry-credentials=1`、`streaming.bootstrap-retries=0` 保持不变。

`gateway` 表示此卡有供应商专属策略字段且已有应用链；这些字段可以由 LiteLLM 管理后同步到 CLIProxyAPI auth/config，也可以直接由 LiteLLM 网关执行。它不代表真实供应商已接受请求。`unsupported` 表示当前没有此类单卡策略字段；不影响独立通道配置、API Key 写入或凭据导入。

## 逐供应商能力

| 供应商 | 专属策略字段与默认值 | 保存位置与执行 owner | 状态和证据 |
| --- | --- | --- | --- |
| OpenAI-compatible | 无 `AccountPolicy` 专属字段。Base URL、API Key 和模型配置属于独立直连通道。 | 通道凭据与目标配置由 `openai_compatible` 处理；`_validate_destination` 负责阻止不安全目标；不写入 CLIProxyAPI。 | `unsupported` 仅表示没有单卡专属策略字段。`test_openai_compatible.py` 验证目标校验与直连行为。 |
| Codex | `identity_fingerprint_mode=off`；`cli_only=false`；`allow_app_server=false`；`allow_app_server_clients=()`；`responses_compact_enabled=false`；`identity_confuse=false`；`disable_codex_cloaking=false`。 | `account_pool_policies`。安装身份、CLI-only 和 app-server 标志同步到 auth fields；身份混淆与 cloaking 写入 CLIProxyAPI config YAML。`allow_app_server_clients` 与 Responses Compact 由 LiteLLM 卡片网关检查；全局 WebSocket 认证由 LiteLLM 设置同步为 CLIProxyAPI `ws-auth`。 | `gateway`。`test_apply_codex_policy_syncs_auth_file_metadata_and_yaml_settings`、account-pool routing / gateway tests 和 `test_settings_and_policy_preserve_single_retry_owner`。managed identity 另由 capability gate 控制。 |
| Claude | `fingerprint_profile=inherit`；`cloak_mode=auto`；`rebuild_mid_system_message=false`。 | `account_pool_policies`；三字段经 `patch_auth_file_fields` 写入 CLIProxyAPI auth file，由固定版本 Claude executor 消费。项目补丁修复 `Attributes` 热更新与文件重载同步。 | `gateway`。Python `test_apply_policy_syncs_only_auth_file_metadata` 验证保存到 auth fields；固定源有执行逻辑。本轮没有 Go toolchain，未重跑 Go tests；未做真实 Claude 请求。 |
| Kimi | `fingerprint_profile=inherit`。 | `account_pool_policies`；字段写入 auth file。固定源中该指纹只让 Kimi 的 Anthropic Messages 路径采用 Claude Code CLI profile，不改变 Kimi 原生 API 请求。Kimi `X-Msh-Device-Id` 是独立既有认证状态：优先 `auth.Metadata["device_id"]`，再读 `KimiTokenStorage.DeviceID`；device flow 的 UUID 经 metadata 保存。缺失时旧默认取本机 Kimi CLI `device_id` 文件或 `cli-proxy-api-device`。不得把此设备 ID 改成 Cockpit 安装身份。 | `gateway`。Python auth-field mapping test 与固定源 `TestKimiExecutor_ClaudeMessagesWithAndWithoutClaudeCodeCLIFingerprint` 可对应；Go tests 本轮未运行，真实 Kimi 上游请求未验证。 |
| xAI | `inject_x_search=false`。 | `account_pool_policies`；写入 CLIProxyAPI config YAML `xai.inject-x-search`，由原 xAI executor 消费。直接 Key 仍使用 CLIProxyAPI xAI API-key 入口。 | `gateway`。`test_apply_policy_syncs_yaml_settings_without_an_auth_file` 与 `test_write_direct_api_key_uses_supplier_array_contract`。 |
| Antigravity | `sensitive_words=()`；`signature_cache_enabled=true`；`signature_bypass_strict=false`。 | `account_pool_policies`；写入 CLIProxyAPI `antigravity` config 和对应签名开关，由既有 executor / reload 路径消费。 | `gateway`。`test_apply_policy_syncs_yaml_settings_without_an_auth_file` 和供应商注册表测试；真实上游请求未验证。 |
| Gemini | 无 `AccountPolicy` 专属字段；API Key 是独立凭据。 | 通过 CLIProxyAPI Gemini direct API-key 入口写入凭据；通用模型、额度和路由仍由号池网关处理。 | `unsupported` 仅表示无单卡供应商策略字段。`test_write_direct_api_key_uses_supplier_array_contract` 与供应商注册表测试。 |
| Gemini Interactions | 无 `AccountPolicy` 专属字段；API Key 和 `interactions` 模型排除映射独立处理。 | 通过 CLIProxyAPI `gemini-interactions` direct API-key 入口；不伪装成 Gemini CLI OAuth。 | `unsupported` 仅表示无单卡供应商策略字段。注册表与 direct-key / global model-exclusion tests。 |
| Vertex | 无 `AccountPolicy` 专属字段；凭据类型为服务账号文件。 | 通过 Vertex `import` 文件与 location 字段接入；不得按 API Key 数组替换。 | `unsupported` 仅表示无单卡供应商策略字段。`test_import_vertex_credential_uses_file_and_location_fields`。 |

## 尚未实测

固定源源码可核实配置消费与 Kimi header 构造，但本机没有 Go toolchain，不能新跑 sidecar Go tests；本轮也没有真实供应商 API 请求、额度消耗、跨账号密文续聊或 Docker / PostgreSQL 环境验收。因此本表证明的是项目设置链与固定源代码边界，不宣称真实上游行为已通过。
