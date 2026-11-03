# 本机 Camoufox 连接助手

让号池页面使用操作者电脑上的独立 Camoufox 完成 OpenAI OAuth。助手不运行在项目 Docker 中，也不依赖 Donut 或 AdsPower

## macOS 安装与日常管理

先安装 Node.js 22 或以上版本（含 npm），以及独立 Camoufox。Node 安装入口是 [Node.js 下载页](https://nodejs.org/en/download)，Camoufox 安装包见 [官方 Releases](https://github.com/daijro/camoufox/releases)，选择适合本机架构的 macOS 包，将应用放入 `/Applications`。助手不会自动下载浏览器。双击入口会从 PATH、Homebrew 和 nvm 目录中寻找可用 Node；也可用 `CAMOUFOX_NODE=/完整/node路径` 指定版本

解压 `camoufox-helper-macos-0.2.0.zip`，双击其中的 `管理.command`。按菜单依次选择「安装/更新当前软件包」「配置网站与浏览器」「后台启动」。从仓库使用时也可直接双击本目录的 `管理.command`。macOS 若拦截未签名脚本，核实来源后在系统的「隐私与安全性」中允许打开，或在终端执行 `bash /完整路径/管理.command`

配置中的网站来源是打开号池的网站，例如 `https://pool.example.com`，不能带路径；本机开发可填 `http://127.0.0.1:4001`。默认助手端口为 10109，默认 Camoufox 路径为 `/Applications/Camoufox.app/Contents/MacOS/camoufox`。回调转发端口填 0 表示关闭；有 SSH 隧道时填写本机映射的 Manager 回调端口，不能填写网站端口

启动完成后可以关闭终端窗口，助手由当前用户的 launchd 管理。菜单提供停止、重启、状态和诊断，只有明确选择「显示连接 Key」才会输出 Key。启动不会启用登录自启动，注销或重启电脑后需要再次选择后台启动。修改配置后需要重启助手。停止和重启会关闭该助手打开的 Camoufox 窗口，账号登录数据继续保留

如果端口被旧的手动助手占用，先到启动它的终端按 `Ctrl+C`，再启动受管理版本。管理器不按 PID 杀进程，也不会停止属于其他数据目录的同名 launchd 服务。先运行「诊断」检查配置、Node/npm、浏览器和服务，再根据提示查看日志。安装依赖时需要能访问 npm；失败时重试安装即可

## 更新、回退与数据位置

收到可信的新版本 ZIP 后，先用 `shasum -a 256 -c camoufox-helper-macos-版本.zip.sha256` 核对外部校验文件，再解压并双击新包中的 `管理.command`，选择安装/更新。也可在现有菜单选择「从其他已解压软件包更新」并填写新包目录。当前没有在线发布或自动更新渠道，ZIP 内的 SHA-256 清单只验证文件完整性，不证明发布者身份

安装器检查固定文件清单和校验值，拒绝符号链接及额外文件，在独立目录执行 `npm ci --ignore-scripts` 并检查模块加载后才切换版本。安装失败保留原版本；运行中的服务更新失败时会恢复旧版本选择并尝试重启，恢复失败会明确报错。旧版本保留，可用菜单「回退上一版本」切回；相同版本内容不同不会覆盖，需要提高版本号

状态目录为 `~/.litellm-camoufox`，权限 0700。`settings.json` 保存网站来源、端口和浏览器路径，权限 0600；`helper.key`、`registry.json`、`profiles/` 是持久身份和登录数据。可替换应用位于 `apps/版本/`，`active.json` 记录当前和上一版本。安装、更新、回退不会重置 Key、登记表或浏览器数据。`helper.plist` 保存在此目录中，不放到 `~/Library/LaunchAgents`；日志在 `logs/helper.log` 和 `logs/helper.error.log`，不会在诊断中自动输出

非交互脚本必须指定命令，可用 `--data-dir` 在隔离目录验证。所有管理命令均只支持 macOS：

```sh
./管理.command install
./管理.command configure --origin https://pool.example.com --callback-port 0
./管理.command start
./管理.command status --json
./管理.command diagnose --json
./管理.command show-key
./管理.command restart
./管理.command stop
./管理.command install --source '/完整路径/新版/camoufox-helper'
./管理.command rollback
```

配置文件损坏时，菜单「配置网站与浏览器」会明确提示并用默认值重新填写；命令行修复需同时提供 `--origin`、`--port`、`--callback-port` 和 `--executable`。部分字段更新不会用默认值覆盖无法读取的旧配置，校验失败也不会覆盖已有有效配置

若管理器异常退出遗留 `manager.lock` 目录，确认所有管理窗口都已关闭后，按错误提示删除该空目录再重试。不要删除整个状态目录

维护者生成独立 ZIP：

```sh
node package-release.mjs --out /完整路径/发布目录
```

输出 ZIP 和同名 `.sha256`，仅打包运行代码、锁文件、说明、管理入口及 SHA-256 清单，不包含 `node_modules`、测试、Key、登记表、日志或浏览器数据。版本由本目录 `package.json` 和 `package-lock.json` 共同维护，与项目服务端版本独立

## 保留的命令行与授权流程

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

自动登录可以在授权面板选择一个 TXT 卡密文件，每次只接受一条 `邮箱----密码----TOTP密钥` 记录，允许前面带导出标题，文件不超过 8 KB。文件内容仅从网页发送到本机助手，不经过 LiteLLM 或 Manager。脚本自动填写邮箱、密码和本机计算的六位 TOTP，不保存卡密或输出页面错误原文；浏览器登录 Cookie 仍会保留在该卡片的本机目录。文件选择在本次尝试后清除

点击自动登录即发起当前卡片的完整授权，脚本核对当前邮箱后自动点击 Codex 授权页的 Continue，无需另外勾选。验证码、邮件验证、账号不匹配、未知页面或超时都会停止，不重复猜测密码或验证码。点击「停止自动登录」或关闭面板可取消自动填写，已经提交的登录或授权无法撤回

也可直接运行完整流程脚本。先启动上面的本机助手，并将号池管理员 Token 放在仅自己可读的文件中，避免写入命令行历史。指定已经保存 ISP 设置的卡片 ID：

```sh
node tools/camoufox-helper/authorize.mjs \
  --dashboard http://127.0.0.1:4001 \
  --card-id '<卡片 UUID>' \
  --credentials-file '/绝对路径/卡密.txt' \
  --access-token-file '/绝对路径/管理员Token文件'
```

脚本默认完成最终授权，旧命令中的 `--allow-consent` 参数继续兼容，无需指定。`--helper` 可指定本机助手地址，`--helper-key-file` 可指定本机助手 Key 文件。脚本会先匹配 ISP，再生成新授权链接、复核 ISP 未变化并打开登录页面。它不创建账号、不新建卡片，也不改变已保存的代理设置

输出 `callback_received` 仅表示浏览器到达本次授权的回调，不保证后台已接收凭据、渠道启动或额度刷新成功；最终以号池卡片状态为准。兼容调用显式传入 `allow_consent: false` 时，`confirmation_required` 表示需要手动确认；`verification_required` 表示需要检查浏览器，`account_mismatch` 表示浏览器账号与卡密不一致。脚本退出码 0 表示已到回调，2 表示已暂停，1 表示配置或请求失败。授权过期时重新运行生成新链接

`Ctrl+C` 停止助手并关闭其启动的浏览器。再次运行会复用本地登记和浏览器登录数据。用 `npm test` 运行不访问真实账号的代理、会话及权限测试

安装 Camoufox 的机器可运行浏览器回归测试，模拟慢速跳转和延迟渲染，确认脚本等待页面就绪且不会重复提交。所有页面由测试拦截，不连接真实账号：

```sh
CAMOUFOX_TEST_EXECUTABLE=/Applications/Camoufox.app/Contents/MacOS/camoufox npm test
```
