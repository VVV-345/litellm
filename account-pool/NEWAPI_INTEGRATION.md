# NewAPI 接入号池卡片 Key

NewAPI 继续负责下游用户、下游 Key、预算、模型权限和限流。LiteLLM 号池只向 NewAPI 提供每张卡片独立的 `cpk_...` Key，并在服务端完成账号选择、并发控制、会话粘性、冷却和故障切换

## 配置

在号池页面打开目标卡片的“卡片 Key”，创建或重置 Key。明文只显示一次，需要立即保存到 NewAPI

在 NewAPI 中创建 OpenAI 兼容渠道并填写：

- Base URL：号池弹窗显示的 LiteLLM 地址，以 `/v1` 结尾，例如 `https://llm.example.com/v1`
- API Key：号池生成的 `cpk_...`
- 模型：先请求 `GET /v1/models` 查看该卡片实际可用的模型和别名，再配置到 NewAPI

可用的公共入口包括 `GET /v1/models`、`POST /v1/chat/completions`、`POST /v1/responses`、`POST /v1/responses/compact` 和 `POST /v1/images/generations`。具体入口仍受卡片和账号策略限制。卡片 Key 不能访问 LiteLLM 管理接口，WebSocket 和压缩请求体当前不支持

如果下游客户端能提供稳定会话标识，NewAPI 可以透传 `X-Session-ID`。号池会按卡片 Key、模型和该值的哈希建立会话粘性，不保存原始会话值。未传该 Header 时仍会正常路由，只是不保证同一会话持续使用同一账号

## 验证

先确认模型目录：

```bash
curl https://llm.example.com/v1/models \
  -H "Authorization: Bearer cpk_REPLACE_ME"
```

再发送最小请求：

```bash
curl https://llm.example.com/v1/responses \
  -H "Authorization: Bearer cpk_REPLACE_ME" \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: newapi-test-session" \
  -d '{"model":"MODEL_FROM_V1_MODELS","input":"ping"}'
```

成功响应包含 `x-request-id`。排查失败时可以在号池日志页按卡片、账号、模型或请求 ID 查询实际选中的账号、状态码、错误分类、重试和切号结果

当前开发环境没有可用的 Docker Desktop、PostgreSQL 和真实 NewAPI 实例，因此上述链路已经通过单元和网关集成测试验证，仍需在部署环境完成一次真实容器、数据库和上游请求验收
