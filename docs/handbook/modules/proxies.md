# 出站代理、ISP 与订阅

## 职责与边界

号池入站决定请求使用哪张卡，出站代理决定该卡怎样连接上游，二者字段和授权分开。Manager 管理 Clash 控制器、网关监听器、代理目录及 ISP 绑定，不因此开放控制器、Docker 或每卡端口到公网

## 关键入口

[ProxyGatewayService](../../../account-pool/account_pool/proxy_gateways.py)、[Clash 客户端](../../../account-pool/account_pool/clash.py)、[代理目录](../../../account-pool/account_pool/proxy_catalog_service.py)、[目录持久化](../../../account-pool/account_pool/proxy_catalog_repository.py)、[ISP 订阅](../../../account-pool/account_pool/isp_subscription.py)、[ISP 探测](../../../account-pool/account_pool/isp_probe.py)、[单卡配置同步](../../../account-pool/account_pool/application/environments/configuration.py)

## 输入输出与流程

代理配置、目录节点和网关绑定形成实际监听器与出站链；卡片保存代理配置后由配置对账确认生效。全局 proxy-url 与账号级 proxy_url 都可能影响出站，不能只改全局值便认定单卡已经切换

ISP 前置节点可不配置，不配置时可按 ISP 线路直连；已经配置但失效/消失时仍阻断，不能静默绕过。订阅 ISP 和手工 ISP 都要保留各自配置，探测结果与正常模型推理分别确认

## 配置与最小使用

先核对 [Settings 中的 Clash 与网关字段](../../../account-pool/account_pool/config.py)。在测试配置中通过代理面板选择网关、ISP 和可选前置，再显式保存并查看监听器与探测结果；这是**修改网络状态的操作**，文档检查不会执行

当前代理配置入口支持 YAML 文本或订阅 URL，不能假定有本地上传控件。具体输入、URL 安全限制和验证方式沿用[更新手册](../../../deploy/UPDATE_RUNBOOK.md)的代理配置章节，不能将带密码的 URL 发到日志

## 依赖与改动影响

依赖控制器、代理配置文件、订阅目录、卡片期望配置与账号级字段同步。切换绑定会影响实际出站与 IP；保存目录记录、控制器加载成功、ISP 可达和供应商可调用是不同结果，配置补偿要验证原绑定仍可恢复

## 风险与禁止事项

不暴露 Clash 控制器，不关闭 TLS、不允许订阅任意重定向或借 URL 访问内网。已配置前置失败时不能降级为未经允许的直连；不要混用默认网关与指定 profile，也不要在没有业务授权时批量切换卡片出站

## 测试与通过标准

[网关绑定](../../../account-pool/tests/account_pool/test_proxy_gateways.py)、[配置替换](../../../account-pool/tests/account_pool/test_proxy_configuration.py)、[代理 profile](../../../account-pool/tests/account_pool/test_isp_profiles.py)、[探测](../../../account-pool/tests/account_pool/test_isp_probe.py)、[目录数据库](../../../account-pool/tests/account_pool/test_proxy_catalog_postgres.py)。须验证独立绑定、前置可选但失效不绕过、加载失败补偿、秘密不泄漏

## 故障排查

保存后不通先看实际监听端口、当前选中链路和 profile 是否启用/过期，再核对单卡 observed 配置版本及账号级 proxy_url。Controller 响应成功不证明上游模型成功；供应商鉴权失败也不直接证明 ISP 不通

## 恢复与回滚

恢复受保护的已知配置及原绑定，核对控制器实际状态后再确认卡片同步。镜像回退不自动回滚代理目录数据库或订阅内容；不要通过清空目录、改权限或暴露宿主端口“临时修好”
