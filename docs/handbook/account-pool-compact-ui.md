# 号池紧凑界面与代理轮换

## 入口

授权自动化和自动更新面板的按钮、状态标签与时间格式跟随控制台语言。卡片名、代理名和 Manager 返回的任务详情保留原文。

- **仪表盘**：统计范围 1 / 7 / 14 / 30 / 90 天，K/M/B 与完整数字切换；额度刷新间隔和立即刷新放在同一工具栏。卡片按供应商标签显示，GLM（智谱 / Z.AI）与 OpenCode 使用各自的 provider family 单独分组，与通用「OpenAI 兼容」不再混在一个标签里；旧接口没有返回 `provider_family` 时回退到 `supplier`。默认选 Codex；没有匹配的 Codex 时使用第一个可见供应商。保留搜索、状态筛选、排序与分页。
- **提供商与 OAuth**：AI 提供商目录、OAuth 登录、批量操作共用一个入口。提供商只显示当前标签的认证方式和新建操作；OAuth 用紧凑账号行，不重复仪表盘完整卡片。Codex 目录、待授权卡片和新建后的授权引导均提供“浏览器自动化授权”，进入同一个授权自动化工作区；从具体卡片进入会预选该卡，不重复创建卡片，不自动提交资料或开启自动化。其他供应商保留原授权方式。
- **自动化上号**：同一入口下区分 CPA 文件、Sub2API 文件、2FA / OAuth 授权。旧 `tab=oauth-automation` 直接打开授权标签；手动 OAuth 与邮箱库存保留在授权页内。
- **代理层**：网关出口、ISP 代理、订阅与配置分开；默认显示数量摘要，点击标签展开对应内容。
- **全局设置总览**：分类展开服务端当前值，标题跳转唯一编辑入口。网络、模型访问、流式等链接会直接打开策略中心对应标签。
- **策略中心**：跨卡调度、额度阈值、供应商运行分组、卡片独立配置和运行判定分开。切换标签保留已访问表单草稿；离开页面仍需先保存。
- 独立配额导航移除，旧 `tab=quotas` 进入仪表盘；旧 `tab=oauth` 进入合并后的 OAuth 标签。只移除版本页的“代码回退说明”，实际备份、回退检查和执行功能保留。

窄屏下全局侧栏默认收起为图标栏，顶栏保留视图切换与通知；桌面继续使用原来的完整导航与工具。

## 多文件代理

默认保留原来的单代理选择。勾选“多代理轮换”后，按勾选顺序给文件依次分配节点，例如 A、B、A、B。预检后可查看每份文件的代理；改变选择会清空旧预检，防止提交过期分配。

每条 `OnboardingEntry.proxy_profile_id` 可覆盖批次的 `proxy_profile_id`；未提供时沿用旧单代理协议。多账号整包展开后继承该文件的代理。OAuth 账号表按账号顺序轮换。服务器把最终代理写入加密任务载荷，重启、重试和同批次重复提交都不会重新分配；已有卡片的重试不重复创建卡片。

轮换不改变凭据所有权校验、模型验证、代理可用性检查或 OAuth 人工授权要求。失效节点不会静默改用其他节点或直连。

视觉层次保留轻卡片、供应商标识、模块图标与状态色。一级模块有独立浅底框，二级功能和供应商选项保留各自描边与间距；选中项统一使用蓝色描边和浅色背景。窄屏标签可横向滑动，提供商说明与新建按钮分行；不增加完整卡片的重复展示，也不改变保存、代理分配或认证行为。

## 复用与验收

运行日志默认保留卡片、结果、模型筛选，高级筛选和存储清理按需展开。列表合并账号与模型、输入输出与缓存用量，详细请求标识和原始记录在详情内展开；折叠不会清空筛选草稿或改变查询、分页、导出范围。反馈表单固定标题和保存区，只滚动填写内容；复现步骤与环境可折叠，关闭折叠不丢字段，保存失败仍保留输入，附件继续沿用原有大小限制和修订版本流程。

`AccountPoolWorkspace` 复用原生 Tabs 和 `DeferredTabPanel`，未访问的重型内容不挂载，已访问表单用 React Activity 保留状态。`useAccountPoolQuotaInterval` 共用原有 API、查询键与缓存，仪表盘是唯一的额度刷新设置入口，完整额度详情仍可折叠查看。

测试跟随对应组件；重点覆盖标签与折叠、草稿保持、跨页入口、保存请求、旧链接兼容、多文件轮换、整包展开和重启重试。UI 测试按文件运行，不执行整个 Vitest 测试库。本地预览使用独立构建目录和演示数据，不连接生产服务；演示保存不代表生产已变更。


## GLM API Key 与兼容接入（2026-11-16）

入口：号池 → 提供商与 OAuth → GLM（智谱 / Z.AI）→ 新建。选择国内或国际站的标准 API 或 Coding Plan 后，自动填写所选方案的地址。通用「OpenAI 兼容」仍有独立入口，输入域名时默认用 `/v1`，填写了接口路径则保留该路径。两种接入复用现有卡片、Key、代理和模型测试，不增加路由类型、分组或数据库表，不修改已有卡片。

创建表单的「从资源侧获取」使用当前地址、API Key、出站代理和高级连接设置中的请求头，向对应上游发出 `GET <Base URL>/models`。按 [New API 的资源侧查询](https://github.com/QuantumNous/new-api/blob/main/controller/channel_upstream_update.go) 的交互，只返回候选，用户勾选后才填入表单；候选是常驻的勾选列表，支持搜索、全选和清空，可以连续勾选多个模型，勾选与取消都不会关闭列表，已选模型以标签展示且与逗号分隔的模型输入双向同步。它不是模型调用测试，不自动保存卡片。上游不返回列表时可手填。表单不再单独填写测试模型：第一个已选（或手填）模型即测试模型，`test_model` 与 `custom_models` 都由这次选择得出。

日志 → 号池日志 → 日志详情 → 请求与关联信息 → 思考强度。日常摘要仅记录进入号池网关的 HTTP 请求中明确提供的 `reasoning_effort`、`reasoning.effort`、`output_config.effort` 或 `thinking.budget_tokens`。流式请求也记录这些参数；不需要开启完整日志，不保存思考内容。旧日志或未指定的请求显示「未指定」，不推断模型默认值。

GLM 标准与 Coding 地址的 Chat 请求都会把 `max_completion_tokens` 等值转换成官方 `max_tokens`；同时传入且值冲突时返回明确 400，不选取其一或丢弃限制。其他通用 OpenAI 兼容卡片保持原行为

国内站地址 `https://open.bigmodel.cn/api/paas/v4`，国际站地址 `https://api.z.ai/api/paas/v4`。来源：[智谱标准 API](https://docs.bigmodel.cn/cn/api/introduction)、[Z.AI 标准 API](https://docs.z.ai/guides/overview/quick-start)。标准 API 使用对应站点的标准 Key。Coding Plan 单独使用同站点的 /api/coding/paas/v4，只用于编程场景，以套餐权限为准。两者不混用 Key 或额度，不使用浏览器登录或 OAuth。见 [官方端点说明](https://zcode.z.ai/cn/docs/configuration/)

模型 ID 留空等待用户填写，不预置账号有权使用的模型，不自动导入或启用发现结果。现有创建流程将明确填写的自定义模型（为空时用测试模型）保存为目录；后台可能请求 `/models`，失败时仍保留手填目录。创建成功只表示配置和加密凭据已保存，不证明 Key 有效或模型可调用，必须继续运行卡片的 Chat 模型测试

本地回归使用同一张测试卡、同一 `glm-5.3` 和固定假 Key，调用真实 Router 与本地 HTTP 网关；鉴权身份、Manager 数据和 GLM HTTP 上游为测试替身，没有访问 GLM 上游或消耗 GLM 额度

| 入口 | 工具调用 | 推理参数 | 验收边界 |
| --- | --- | --- | --- |
| Chat | 原生 Chat 结构透传，工具字段及流式工具事件回归通过 | 标准 GLM 受管部署允许 `reasoning_effort`、`thinking`，后者使用 OpenAI SDK 的 `extra_body` 原值进入 JSON 请求体，不丢弃 `clear_thinking` 等嵌套字段 | 普通 Chat、流式和卡片 Chat 模型测试本地通过；推理字段本地传输回归通过，实际模型可接受的取值仍由上游校验 |
| Responses | 本预设未获支持验证，无 Chat 协议转换 | 同样未获支持验证；请求原样转发，不能静默丢弃 | 模拟上游拒绝必须返回失败，不冒充成功；真实上游未验收 |
| Messages | 网关转换：虚拟 Key 经 LiteLLM 转为 Responses，工具结构随之转换；直接卡片 Key 则原样转发 Messages | 不再把精确 `thinking.budget_tokens` 悄悄近似为推理强度；此标准 GLM 部署在无法无损转换 `thinking` 时明确返回 400，建议改用 Chat | 本预设不承诺支持；模拟上游拒绝必须返回失败，真实上游未验收 |

[Z.AI Chat 参数文档](https://docs.z.ai/api-reference/llm/chat-completion)描述了工具及模型相关的思考参数，但文档支持不等于本项目全链路实测。仅标准 GLM 地址的受管部署自动放行两个推理字段，不按模型名称猜测，不设置 `drop_params`；普通兼容卡片、Coding 地址不自动放行。切换回通用地址时撤销本次自动放行并恢复原有白名单，保留原来的其他参数。后续如需三协议完整适配，应单独设计并以获准的标准测试 Key 验证

真实上游未验收：此次没有获准使用的 GLM 标准测试 Key，实际打到 GLM 上游的调用为 0。没有 Docker 构建、生产部署或配置修改

本轮复用既有 `allowed_openai_params` 和 [OpenAI Python SDK 的 extra_body 扩展参数接口](https://github.com/openai/openai-python#undocumented-request-params)，不引入 GLM 专用路由、SDK 或数据库表。参照 [Z.AI 的 OpenAI SDK 接入说明](https://docs.z.ai/guides/develop/openai/python)使用标准 Chat 端点。没有把其他项目的 Coding Plan / Anthropic 专用地址混入标准预设

Responses 不做 Chat 桥接，仍向所选标准 Base 的 `/responses` 发送 Responses 结构；Messages 无 `thinking` 时沿用现有 Messages -> Responses 桥接（函数工具、`max_tokens` 等转换），并不因此获得标准 GLM 上游支持。有显式原生 Messages 透传配置的旧部署不受新转换保护影响，仍由所配置上游决定支持情况。本预设没有自动打开该透传或改到其他专用端点

此处的模拟 404 只验证错误不会伪装成成功，不是“GLM 实际返回 404”的证据。当前标准端点的 Responses / Messages 原生能力均未完成真实上游确认；请不要把 Chat 回归通过解读为三个入口都可用

## OpenCode API Key 接入

在号池的「提供商与 OAuth」中选择「OpenCode」，点击「新建」。选择 [Console / Zen](https://opencode.ai/docs/console/api/inference/) 或 [OpenCode Go](https://opencode.ai/docs/console/go/) 后，预填该方案的 OpenAI 兼容地址，复用现有卡片、代理和模型测试。只使用 OpenCode API Key，不使用浏览器授权

「从资源侧获取」中，Console / Zen 查询其兼容 API 的 `/models`，Go 查询官方 `/inference/go/v1/models`。Go 的兼容旧地址 `/zen/go/v1` 也用同一目录，原推理地址不变。仍可手填模型。发现的模型不会自动启用。公共目录不保证当前 Key 有权调用或所选模型支持相应协议，仅限 OpenCode 客户端的免费模型不可用

## 卡片模型测试的判定与诊断（2026-11-17）

Chat 测试把 `finish_reason` 为 `stop` 或 `length`、且响应里存在可见文本或推理文本的情况视为「返回了文本响应」。原来的实现只接受 `stop` 加非空 `content`，GLM 一类思考模型在 256 token 预算内把额度用在推理上时，会得到 200 响应却被判为 `incomplete_response`。现在测试预算提高到 512 token；只有空响应、`content_filter` 之类非文本终止原因、流式缺少终止事件时才算不完整，失败消息会带上 `finish_reason` 便于定位

连接失败同样给出可操作信息：消息里附上脱敏后的上游摘要（URL 整体替换为 `[url]`，`Bearer`、`sk-` 前缀和 `key=` / `token=` 之类赋值一律脱敏，最长 400 字符）。上游的具体 400 原因（例如不支持的参数或请求头）会直接出现在卡片的测试结果里，不再只有「检查请求日志」

`max_completion_tokens` 到标准 GLM `max_tokens` 的转换现在同时覆盖 `/api/paas/v4` 与 `/api/coding/paas/v4`；推理字段 `reasoning_effort` / `thinking` 的白名单仍只对标准地址生效，Coding 地址不会因此自动放行推理参数

