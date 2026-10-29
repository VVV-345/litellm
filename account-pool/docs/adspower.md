# AdsPower 手动同步代理

在号池的「代理管理」中点击「读取 AdsPower 环境」，勾选环境后点击「同步选中代理」。同步读取环境当前的 HTTP、HTTPS 或 SOCKS5 代理，保存为加密 ISP 档案；不启动浏览器，不同步登录 Cookie，也不替换账号的授权凭据

导入后，在目标端口选择对应的 ISP 和前置节点，再把账号的出站代理设置为该端口。同步本身不会切换运行中的账号出口。如果原环境需要系统代理才能连接 ISP，前置节点仍需单独配置，AdsPower 返回的环境代理不包含操作系统的完整路由

同一环境的地址、协议和凭据没有变化时，重复同步复用现有启用档案。配置变化会创建新档案，原有绑定保持不变，需要在端口上重新选择。每次最多选择 20 个环境；不支持的代理类型或未配置代理的环境不可选择。读取失败不会保存该批次的一部分，出现 Local API 限流时应稍后重试或减少选择数量

在 Manager 的环境变量中配置 Local API 地址和密钥，并重建 Manager 容器使变量生效：

```dotenv
ACCOUNT_POOL_ADSPOWER_API_URL=http://host.docker.internal:50325
ACCOUNT_POOL_ADSPOWER_API_KEY=<AdsPower Local API 密钥>
```

该地址适用于 AdsPower 在 Mac 宿主机、Manager 在 Docker Desktop 的情况。远程服务器的 `localhost` 指向服务器自身；要读取 Mac 上的 AdsPower，应先建立受保护且 Manager 可访问的连接。不要直接把 Local API 暴露到公网

页面和接口仅返回环境名称、代理协议、地址、端口及认证是否配置，不返回 API Key 或代理密码。API Key 留在 Manager 配置，ISP 凭据沿用号池现有加密存储。调用入口为管理员专用的 `GET /account_pool/adspower/profiles?page=1` 和 `POST /account_pool/adspower/sync`，请求体示例为 `{"profile_ids":["环境 ID"]}`

若导入成功后端口仍不可用，检查现有 Clash/Mihomo 网关、ISP 认证、前置节点以及 Manager 到网关的网络。导入成功只说明配置已保存，实际出口须通过端口探测和账号额度刷新验证
