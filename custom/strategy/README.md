# OpenMontage 同步与 MyMontage 验证

## 合入检查

以 Git 历史完整的独立检出为起点，准备 Python 3.11、Git、Make、FFmpeg 和 ffprobe。CI 的安装和验证命令在仓库根目录执行。

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt -r custom/requirements.txt
make -f custom/Makefile ci BASE_SHA="$BASE_SHA"
```

`BASE_SHA` 由运行器填入 PR 目标分支的完整提交 SHA。验证当前候选提交或已包含目标分支的合并预览；普通 PR 以该目标提交的 `policy.json` 为准。同步 PR 额外传入由维护者选定的 `UPSTREAM_SHA="$UPSTREAM_SHA"`，校验候选提交确实集成了该上游 SHA，并且新基线与之相同。分支更新后重新验证。

现有上游工作流继续运行上游测试。提供合入门禁的外部运行器须调用上述 `ci` 目标，把退出结果发布为 `main` 的必过状态检查。Strategy Stack 常规代码任务已有 `verify.py` 接口，它只验证下游范围和离线回归。外部 CI 接入及必过状态由仓库维护者配置。合入检查使用无生产凭据的环境，依赖安装完成后，测试无需浏览器登录或付费 API。

如需只跑下游验证，从 Git worktree 根目录运行 `.venv/bin/python -B custom/strategy/verify.py`。加 `--policy-only` 可只检查 Git 基线、范围和产物。传入 `--base <目标分支 SHA>` 时，还会检查该 PR 新增的下游提交，包含随后删除的产物。验证脚本从当前 worktree 读取候选内容，支持由外部 Strategy Stack 使用已有解释器启动。

| 功能 | 验证入口与通过条件 |
|---|---|
| 凭据与桌面环境 | `test_providers_env.py`、`test_flow_sw_env.py`。测试进程拿到受信配置，既有值优先，证据不含凭据值。 |
| Flow 工具与 CDP | `test_flow_contract.py`、`test_flow_cdp_preflight.py`。配置和工具调用遵守当前接口，预检异常能定位原因。 |
| OpenRouter 的图、音、视频 | `test_openrouter_{image,audio,video}.py`。隔离夹具验证成功、失败、缺凭据与验证失败的行为。 |
| 工具注册与 Piper | `test_piper_tts.py`。定制工具名与上游互不冲突，单语音模型省略默认 `--speaker`，显式的非零角色保留。 |

## 更新上游基线

1. 获取 MyMontage 目标分支与 OpenMontage 主干，固定两个提交。在独立工作树合并候选上游。

   ```bash
   git fetch origin main
   git fetch upstream main
   BASE_SHA=$(git rev-parse refs/remotes/origin/main)
   UPSTREAM_SHA=$(git rev-parse refs/remotes/upstream/main)
   git worktree add -b "sync/openmontage-${UPSTREAM_SHA:0:12}" ../mymontage-sync "$BASE_SHA"
   cd ../mymontage-sync
   git merge --no-ff --no-edit "$UPSTREAM_SHA"
   ```

2. 如果发生冲突，先确认上游是否占用了 `custom/`，以及变更是否影响下游适配器。确认合并后的上游文件与候选 SHA 一致。将 [policy.json](policy.json) 中的 `baseline` 更新为 `$UPSTREAM_SHA`，提交该变更。若上游接口变动，适配器及回归用例随同步 PR 修复。
3. 安装上述测试依赖，在同一工作树运行 `make -f custom/Makefile ci BASE_SHA="$BASE_SHA" UPSTREAM_SHA="$UPSTREAM_SHA"`。检查通过后才合入；同步 PR 使用 merge commit，保留 OpenMontage 候选提交的祖先关系。失败时当前 `main` 仍使用原基线。

## 生产适配器

`production.py` 从本次 attempt 的项目目录读取管线检查点。待人工审批退出 76。有效成片须包含音画流且超过一秒，归档成功后才产生回执。生产凭据和 Google Drive remote 由部署注入。内容检查点从 Backlot 或管线入口审批。
