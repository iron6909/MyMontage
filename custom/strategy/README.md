# MyMontage 自动化验证

从 MyMontage worktree 根目录运行 `.venv/bin/python -B custom/strategy/verify.py`，独立 worktree 可由部署环境提供已有的解释器及依赖。验证过程中检查以下可观察结果。

| 功能 | 验证入口与通过条件 |
|---|---|
| 凭据与桌面环境 | `test_providers_env.py`、`test_flow_sw_env.py`。测试进程拿到受信配置，既有值优先，证据不含凭据值。 |
| Flow 工具与 CDP | `test_flow_contract.py`、`test_flow_cdp_preflight.py`。配置和工具调用遵守当前接口，预检异常能定位原因。 |
| OpenRouter 的图、音、视频 | `test_openrouter_{image,audio,video}.py`。隔离夹具验证成功、失败、缺凭据与验证失败的行为。 |
| 视频出货 | `production.py` 从本次 attempt 的项目目录读管线检查点。待人工审批退出 76；有效成片须有音画流，超过一秒，归档成功后才产生回执。 |

`policy.json` 以已同步的 OpenMontage 提交为比较基线，只允许 `custom/` 内的开发改动。上游改变时，随 MyMontage 的同步更新基线。生产凭据和 Google Drive remote 由本地部署注入。应从应用自己的 Backlot/管线入口批准内容检查点。
