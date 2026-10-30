# 来源与许可

Cockpit Tools 作者为 jlcodes99，来源为 https://github.com/jlcodes99/cockpit-tools ，固定提交 `76fbea02ba8ab5a071205a0bfa59ef4db5cc64b4`

直接复用的文件是 `sidecars/cockpit-cliproxy/third_party/CLIProxyAPI/internal/runtime/executor/helps/managed_identity.go`，内容未改，SHA-256 为 `37330aef72f90930e842244f209f2a7e4161fbff328e478bf94f574c0ccdf275`。同时复用该目录下 `managed_identity_test.go` 的身份字段测试，未引入依赖桌面传输层的测试

本地新增服务器认证卷持久化与执行器接入，改动包含在 `settings-adaptation.patch`。未复制 Tauri 页面、桌面账号池、代理传输层或第二套重试逻辑

Cockpit 固定提交的 README.md “许可证”段落声明默认采用 CC BY-NC-SA 4.0，允许非商业使用和修改，要求署名并按相同协议分享。复用内容和对应改编遵守 [CC BY-NC-SA 4.0 完整条款](https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode.zh-hans)，不改其他独立文件原有许可。当前用途是用户确认的个人非商业自用；商业用途需另获作者书面许可，不能把这个适配后的引擎当成纯 MIT 构建发布

CLIProxyAPI 源码来自 `https://github.com/VVV-345/CLIProxyAPI`，固定提交 `b3c954b63df09156ac2ac52ad038b3dc3a20748f`。其原有 MIT 许可保存在 `CLIProxyAPI.LICENSE`，应随源码及二进制分发保留
