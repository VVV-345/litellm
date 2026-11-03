# 全局设置、单卡策略与插件

## 职责与边界

设置模块管理全局值、命名 profile、历史与分配；策略模块保留单卡路由和供应商策略；插件模块验证清单并协调实际 CPA 插件。保存控制面数据和运行时确认分别完成，不能把全局操作作为覆盖单卡设置的快捷方式

## 关键入口

[settings_for_card 与设置仓库](../../../account-pool/account_pool/settings.py)、[策略](../../../account-pool/account_pool/policies.py)、[profile 差异计算](../../../account-pool/account_pool/application/profile_updates.py)、[配置同步](../../../account-pool/account_pool/application/environments/settings_sync.py)、[CPA 设置/策略同步](../../../account-pool/account_pool/channels/cliproxyapi/settings_sync.py)、[插件服务](../../../account-pool/account_pool/plugins.py)、[单卡插件操作](../../../account-pool/account_pool/application/environments/plugins.py)

## 输入输出与流程

输入期望设置、版本和作用范围，计算哪些卡仍使用原默认/profile，保留单卡独立修改，再同步到运行时并记录期望/实际版本与错误。需要新运行时能力的供应商策略先核验能力；能力拒绝不等于策略已生效

插件目录清单、已安装记录、单卡实际安装/启停和插件配置是不同状态。更改来源、版本或运行参数会影响上游请求与副作用，不能仅以目录项存在判断可用

## 配置与最小使用

在测试卡的运行设置中查看当前 profile、单卡覆盖和作用范围，显式保存一项设置并核对 observed 版本。这是**写入配置且可能影响请求的操作**，文档检查不执行。插件按已批准清单选择来源与版本，未配置目录时不要临时接受任意外部代码

只读源码检查在仓库根目录执行 `git diff -- account-pool/account_pool/settings.py account-pool/account_pool/policies.py`，预期无额外默认值、字段或供应商范围变化

## 依赖与改动影响

设置和策略依赖环境操作锁、仓库版本、CPA 管理能力及代理字段。新增持久化字段会影响旧 Manager 的读取和回退兼容，需要针对性规则与测试，不能因“可选字段”就默认双向兼容

## 风险与禁止事项

不得为兼容旧镜像全局设置 extra=ignore，不把未确认字段写为 observed，不在同步失败后把卡片强写 ready。插件和工具可能产生外部副作用，失败重试必须尊重请求重放边界；秘密不放在公开清单或日志

## 测试与通过标准

[设置](../../../account-pool/tests/account_pool/test_settings.py)、[策略](../../../account-pool/tests/account_pool/test_policies.py)、[有效策略](../../../account-pool/tests/account_pool/test_effective_policy.py)、[插件](../../../account-pool/tests/account_pool/test_plugins.py)。通过标准是独立单卡值保留、版本冲突拒绝、同步失败可追溯、能力检查不被绕过，插件来源与版本校验保持

## 故障排查

pending 先比较期望与实际配置版本和最后同步错误，再核对镜像能力与实际 YAML/认证字段。插件失败分别检查目录、安装、启停和供应商运行结果；不能只清缓存后声称后台已应用

## 恢复与回滚

使用设置历史或原 profile 进行有版本约束的恢复，再确认运行时同步。镜像回退不删除新字段或插件数据，数据库和敏感配置恢复另按[数据边界](../data-and-backups.md)确认
