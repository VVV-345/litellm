# 部署升级、逐卡换镜像与回滚

## 先区分操作对象

| 操作 | 实际结果 | 不自动代表 |
| --- | --- | --- |
| Git commit | 代码在本地历史中 | 已 push 或已构建 |
| 四组件发布完成 | 固定提交对应镜像与清单可核验 | 服务器已部署 |
| 两业务服务切换 | LiteLLM 与 Manager 使用目标镜像 | 旧卡 CPA 已逐卡换新 |
| 新的显式逐卡阶段完成 | 该批卡片镜像、原卷、能力和读回通过 | Token 一定有效或模型推理成功 |
| 原路由真实模型调用成功 | 被测卡、模型和请求范围可用 | 其他卡或其他模型也可用 |

完整执行门禁见[统一更新手册](../../deploy/UPDATE_RUNBOOK.md)，镜像身份见[统一镜像说明](../../deploy/UNIFIED_IMAGES.md)。不要复制旧终端命令猜测主机、目录、架构、容器名或 target digest

## 两张已有卡会不会都换新

使用包含新编排的 release-worker 和 Manager，走正式新发布流程，并且逐卡验证全部通过后，第一张、第二张以及该批其余 CPA 卡才算换到本次发布指定的镜像。原 ID、数据卷、当前凭据、代理和策略保留，不要求删卡重建

第一张失败就停止，第二张不会继续。仅运行旧发布器、重启 Manager 或 `docker compose up` 两业务服务，都不能保证执行了新逐卡阶段。发布器是独立组件，升级方法按统一更新手册执行，不能把它悄悄包含在一次普通业务部署里

## 准入、备份和验证顺序

实现入口为 [ReleaseService](../../account-pool/account_pool/release_service.py)、[DockerReleaseRuntime](../../account-pool/account_pool/release_runtime.py)、[RuntimeRollout](../../account-pool/account_pool/runtime_rollout.py)和[维护仓库](../../account-pool/account_pool/runtime_rollout_repository.py)

1. 四组件预检固定 release commit、架构和 CPA digest，不追 latest
2. 单卡所有权锁下短事务安装持久门禁，网关拒绝新租约；释放环境行锁后等待在途请求结算
3. 备份原 compose、实际旧镜像 `image.tar`、环境元数据和认证卷 `data.tar`，验证哈希；备份目录受保护
4. 只换镜像，复用原卷；实际镜像与 compose 文本必须分别核验，同镜像也不能跳过能力验证
5. 管理接口必须拒绝未认证请求；核验 managed-identity、access-token-fallback、唯一原凭据及配置/策略读回摘要。原停止状态在验证后保留，原 ERROR 不直接写成 READY
6. 该卡验证成功才解除其门禁并继续下一张。结果分列 `image_verified`、`credential_state`、`model_inference`，自动镜像维护的推理字段为 `not_run`

`image_verified` 不等于凭据续期成功。GET 模型列表、健康接口 200、哈希没变都不能代替真实推理。生产恢复必须另有明确授权和调用证据

## 命令位置、前提与结果

下面只显示 CLI 语法，**只读、无网络请求**，在仓库根目录及正确 Python 环境执行，预期打印帮助并退出 0：

```sh
python deploy/releasectl.py --help
python -m account_pool.runtime_rollout --help
python -m account_pool.runtime_maintenance --help
```

下列命令在已核验的服务器实际部署目录执行，须确认 release-worker 已升级、已完成备份和兼容预检、目标 TAG 为本次固定提交前 10 位：

```sh
python3 releasectl.py status
python3 releasectl.py deploy TAG
```

`status` 只读查询，预期返回当前版本、任务与问题；`deploy` **会修改运行服务及卡片镜像**，需要发布授权和原有确认，不得由文档检查自动执行。连接失败应报告失败，不能当作任务为空。实际命令还须满足统一更新手册的目录与权限前提

## 中断、失败和同版本续跑

排空超时、验证失败或进程中断，数据库断点与门禁不会自行清除。失败时只恢复旧镜像和原进程运行状态，绝不回放 `data.tar` 覆盖当前轮换凭据。回退失败也保留阻断，不放行下一张

发布器根目录的 `card-rollout-active.json` 记录未完成目标，并阻止切换其他 Manager 版本；逐卡报告是 `card-rollout-PAIR_ID.json`。Manager 备份在 `data_root/runtime-rollouts/RELEASE_COMMIT/CARD_ID/`，不得将认证备份放到公共报告或版本库

确认卡片失败原因和原固定目标后，重新走同一版本 `deploy TAG` 的预检与确认。两业务服务已是目标版本时只续跑卡片阶段；已 verified 的卡片不重复切换。不要手改数据库 phase、删除活动标记、伪造 disabled 状态或关闭兼容检查来解锁

## 验证边界

源码与本地测试已覆盖门禁、在途结算、崩溃续跑、镜像回退不恢复认证卷、实际镜像不符、能力/唯一凭据/配置失败和同版本发布续跑。测试位置见 [runtime_rollout](../../account-pool/tests/account_pool/test_runtime_rollout.py)、[真实 PostgreSQL](../../account-pool/tests/account_pool/test_runtime_rollout_postgres.py)、[镜像维护](../../account-pool/tests/account_pool/test_cliproxy_runtime_image.py)、[发布服务](../../account-pool/tests/test_releases.py)与[发布运行时](../../account-pool/tests/test_release_runtime.py)

这不等于生产逐卡换镜像、真实 OAuth 或模型推理验收。部署前以当前实际构建、备份和现场结果为准，历史通过数不能替代新一次验证
