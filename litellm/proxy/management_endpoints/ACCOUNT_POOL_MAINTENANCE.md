# 号池网关接入维护

account_pool_management.py 负责管理员接口代理，account_pool_management_models.py 与 Manager 响应模型保持兼容。account_pool_gateway.py 及相关 routing、accounting、observability 模块负责数据面，不能因上游更新控制面的改动改变卡片鉴权、路由、重试和计费

本次上游状态新增字段为兼容性追加，旧报告仍可读取。request_id 查询参数经 UUID 校验后转发；两类上游保持独立路径。响应类型变化后从 Dashboard 执行 npm run gen:api，运行 tests/test_litellm/proxy/management_endpoints 下相关 test_account_pool 测试

参阅 [号池维护手册](../../../account-pool/MAINTENANCE.md) 和 [两类上游说明](../../../account-pool/docs/upstream-updates.md)。此目录的其他 LiteLLM 原生模块不属于本次维护范围
