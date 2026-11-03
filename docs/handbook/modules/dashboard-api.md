# Dashboard、管理 API 与原生 LiteLLM 集成

## 职责与边界

页面装配、API 请求、查询缓存和业务控件分层。号池复用原生 LiteLLM 管理鉴权、HTTP 客户端、路由、计价、日志和 UI 设计，不复制一套原生网关，也不把所有供应商能力描述成 CPA 卡片能力

## 关键入口

[页面装配](../../../ui/litellm-dashboard/src/app/%28dashboard%29/account-pool/page.tsx)、[功能模块](../../../ui/litellm-dashboard/src/features/account-pool/)、[API 客户端](../../../ui/litellm-dashboard/src/features/account-pool/api/AccountPoolApi.ts)、[查询键](../../../ui/litellm-dashboard/src/features/account-pool/hooks/accountPoolQueryKeys.ts)、[LiteLLM 管理接口](../../../litellm/proxy/management_endpoints/account_pool_endpoints.py)、[Manager API](../../../account-pool/account_pool/api.py)、[Manager 装配](../../../account-pool/account_pool/app.py)

## 输入输出与流程

用户按既有权限进入号池，UI 经 LiteLLM 管理 API 转发到 Manager；内部 Manager token 不暴露给浏览器作为通用凭据。表单显式保存后展示成功/失败并失效对应查询；前端乐观显示不能替代后台任务完成

卡片、认证、额度、代理、上号、插件、反馈、上游和发布拥有各自状态。Key、日志、路由和运行设置页面引用功能组件，不反向依赖号池 page.tsx；字段调整还要同步管理协议和生成类型

## 配置与最小使用

前端启动与环境见[开发说明](../development.md)。最小只读检查是在页面查看卡片、版本和任务，并核对 API 的实际响应、用户身份及加载状态；不点击兑换、删除、发布或刷新凭据来验证文档

本地 UI 测试在 `ui/litellm-dashboard` 目录执行，预期退出 0；它使用本机 fixture，不等于线上操作通过：

```sh
node node_modules/vitest/vitest.mjs run src/features/account-pool/components/credentials/AccountPoolAuthorizationPanel.integration.test.tsx
```

## 依赖与改动影响

依赖 Manager 契约、LiteLLM 权限与管理代理、OpenAPI 类型和共享查询键。更改保存后刷新范围时，要验证切页返回、切换用户和退出登录，避免旧缓存或跨用户串数据。源类型、测试类型、生产构建与浏览器实际交互分开记录

## 风险与禁止事项

不在 UI 打印 token，不在模块局部创建一套重复客户端或控件。不因前端包装无调用方就删除服务端 API，不依赖已撤销的 `src/lib/http/api-modules` 目录。生成链接、点击保存或拿到 200 不等于 OAuth、配置或发布已完成

## 测试与通过标准

[功能测试目录](../../../ui/litellm-dashboard/src/features/account-pool/)、[前端脚本](../../../ui/litellm-dashboard/package.json)及 [LiteLLM 接口测试](../../../tests/test_litellm/proxy/management_endpoints/)。应覆盖权限、加载/失败状态、显式保存、查询失效、手动回调和重复渲染；不能用截图代替契约测试，也不能用 mock 替代真实账号验收

## 故障排查

页面与后台不一致先查当前用户、API 返回和 query key，再查任务及配置版本。只在页面有问题时定位缓存或组件；接口已返回 pending/error 时转查对应后台模块。浏览器或本地代理自身错误不直接归因于模型供应商

## 恢复与回滚

保留接口字段和持久化契约，在本分支做最小修复及独立提交。前端回滚不改变后台任务、数据库或认证卷，版本不兼容时停止发布，不靠缓存清空或改文案伪装恢复
