# 本机 Camoufox 连接助手

让号池页面使用操作者电脑上的独立 Camoufox 完成 OpenAI OAuth。助手不运行在项目 Docker 中，也不依赖 Donut 或 AdsPower

需要 Node.js 22 或以上版本，以及已安装的独立 Camoufox。已在 macOS ARM64 的 Camoufox 158.0.1-beta.41、Playwright 1.62.1 上验证浏览器启动和带认证的 SOCKS5 ISP 出口

从仓库目录执行：

```sh
cd tools/camoufox-helper
npm ci --ignore-scripts
npm start -- --origin http://127.0.0.1:4001
```

远程网站把 `--origin` 改成真实 HTTPS 网站来源，例如 `https://pool.example.com`，不包含路径。可以重复指定多个来源。默认监听 `127.0.0.1:10109`，用 `--port` 修改。Windows/Linux 用 `--executable` 指定已安装的 Camoufox 可执行文件

另开终端执行 `node cli.mjs --show-key`，把显示的 Key 填入号池授权面板的「Camoufox 浏览器」。先为卡片选择并保存「ISP 出站代理」，再测试连接和点击「匹配 ISP 并通过 Camoufox 打开」。浏览器需要允许该网页访问本地网络；远程页面通过 HTTPS 打开

助手支持 HTTP、HTTPS、带认证的 SOCKS5；当前转接库不支持直接填写 IPv6 地址的 SOCKS5 入口，可使用 ISP 域名或 IPv4 地址。匹配内容包括协议、地址、端口和完整认证信息；本机登记表仅保存使用本机密钥计算的匹配标识，不写入 ISP 用户名、密码或授权链接。相同 ISP 复用登记，新的 ISP 自动登记，每张卡片使用独立的持久浏览器目录。同一张卡片更换代理时，先关闭它的 Camoufox 窗口

默认状态目录是 `~/.litellm-camoufox`，目录权限 0700，连接 Key 和登记表权限 0600。浏览器登录数据会保留在该目录的 `profiles` 下。网页中的 Key 仅保留在当前组件内存中，离开页面或切换卡片后清除，不经过项目服务器。不要把状态目录或 Key 提交到 Git 或发送给他人

助手使用仅绑定回环地址、带随机认证的 HTTP 转接服务，让 Camoufox 使用已选 ISP。SOCKS5 由上游解析目标域名；代理失败不自动回退直连。浏览器关闭后转接服务一同关闭。WebRTC、DNS 预取和直连回退被禁用，回环回调地址例外

OAuth 回调仍需要正确连接到 Manager。远程部署先建立授权页面显示的 SSH 隧道。本机存在多个测试实例时，可以用 `--callback-port 1456` 将当前授权的 localhost 回调转到指定端口；仅在回调路径及 state 与该授权一致时改写，不占用原端口。这里的端口是本机已映射的 Manager 端口或 SSH 隧道端口，不是网站端口

连接成功或页面打开不表示账号已授权。号池后台完成凭据与渠道检查后才可使用账号

自动登录可以在授权面板选择一个 TXT 卡密文件，每次只接受一条 `邮箱----密码----TOTP密钥` 记录，允许前面带导出标题，文件不超过 8 KB。文件内容仅从网页发送到本机助手，不经过 LiteLLM 或 Manager。脚本自动填写邮箱、密码和本机计算的六位 TOTP，不保存卡密或输出页面错误原文；浏览器登录 Cookie 仍会保留在该卡片的本机目录。文件选择和授权勾选在本次尝试后清除

默认停在最终 Codex 授权页。明确勾选「允许此账号授权给当前 Codex 渠道」后，脚本核对当前邮箱再点击 Continue。验证码、邮件验证、账号不匹配、未知页面或超时都会停止，不重复猜测密码或验证码。点击「停止自动登录」或关闭面板可取消自动填写，已经提交的登录或授权无法撤回

也可直接运行完整流程脚本。先启动上面的本机助手，并将号池管理员 Token 放在仅自己可读的文件中，避免写入命令行历史。指定已经保存 ISP 设置的卡片 ID：

```sh
node tools/camoufox-helper/authorize.mjs \
  --dashboard http://127.0.0.1:4001 \
  --card-id '<卡片 UUID>' \
  --credentials-file '/绝对路径/卡密.txt' \
  --access-token-file '/绝对路径/管理员Token文件' \
  --allow-consent
```

`--allow-consent` 表示本次允许该账号授权给当前卡片的 Codex 渠道，省略则停在确认页。`--helper` 可指定本机助手地址，`--helper-key-file` 可指定本机助手 Key 文件。脚本会先匹配 ISP，再生成新授权链接、复核 ISP 未变化并打开登录页面。它不创建账号、不新建卡片，也不改变已保存的代理设置

输出 `callback_received` 仅表示浏览器到达本次授权的回调，不保证后台已接收凭据、渠道启动或额度刷新成功；最终以号池卡片状态为准。`confirmation_required` 表示需要手动确认，`verification_required` 表示需要检查浏览器，`account_mismatch` 表示浏览器账号与卡密不一致。脚本退出码 0 表示已到回调，2 表示已暂停，1 表示配置或请求失败。授权过期时重新运行生成新链接

`Ctrl+C` 停止助手并关闭其启动的浏览器。再次运行会复用本地登记和浏览器登录数据。用 `npm test` 运行不访问真实账号的代理、会话及权限测试

安装 Camoufox 的机器可运行浏览器回归测试，模拟慢速跳转和延迟渲染，确认脚本等待页面就绪且不会重复提交。所有页面由测试拦截，不连接真实账号：

```sh
CAMOUFOX_TEST_EXECUTABLE=/Applications/Camoufox.app/Contents/MacOS/camoufox npm test
```
