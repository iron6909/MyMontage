# MyMontage 下游开发与集成规则

MyMontage 以 OpenMontage 为上游。先读仓库根目录的 `AGENT_GUIDE.md`，视频生产继续使用其管线和审批规则。以下规则适用于 MyMontage 的开发与上游同步。

## 提交边界

- 常规开发只在 `custom/` 内新增、修改或删除下游文件。同步完成后，将候选提交与选定的 OpenMontage 提交比较，`custom/` 以外的文件树和上游已跟踪的文件必须一致。
- 上游修复优先使用 `custom/` 中的适配器，或向上游提交。开发、同步和 CI 工作树不得通过 `git apply` 改写上游文件，也不得使用 `skip-worktree` 或 `assume-unchanged` 隐藏修改。现有 Piper 修复已迁移到 `mymontage_piper_tts`。[旧检出迁移说明](docs/LOCAL_DEPLOY.md#retire-the-legacy-working-tree-patch)
- `.task/`、`out/`、`projects/` 等运行目录和其中的证据不入库，包括 `custom/` 下的同名目录。`projects/` 已由上游忽略。开发机可将 `.task/` 和 `out/` 加入本地 `.git/info/exclude`。CI 检查候选文件和 PR 新增的下游提交，忽略配置只用于减少本地噪音。
- `custom/config/` 只提交配置模板及占位符。生产凭据由部署环境提供。

## 同步与合入

- [固定上游基线](strategy/policy.json)记录已经集成的 OpenMontage 完整 SHA。普通 PR 使用目标分支上的基线，基线变更走专门的上游同步 PR。
- 同步从干净的 MyMontage `main` 开始，获取并固定 `upstream/main` 的候选 SHA，在独立分支合并，再在同一个 PR 更新 `policy.json`。同步 PR 使用保留上游提交祖先关系的合并方式。
- 合入前在独立干净检出运行 `make -f custom/Makefile ci BASE_SHA=<目标分支完整SHA>`。同步 PR 额外传入 `UPSTREAM_SHA=<已确认的上游完整SHA>`。提交范围、产物、上游 lint 与测试、下游离线回归均须通过。验证失败时保留原基线，在同步分支修复后重跑。[同步步骤及 CI 接线](strategy/README.md)

## 定制资产和接入点

| 资产 | 路径 | 接入和验证 |
|---|---|---|
| 工具 | `custom/tools/<domain>/` | Flow 和 Piper 由使用它们的进程显式调用 `custom.tools.loader.load()` 注册。OpenRouter 使用独立模块 CLI。新增工具要声明接入方式、避免注册名冲突，并补对应的离线契约检查。[调用契约](tools/REGISTRY.md) |
| 插件 | `custom/plugins/<name>/` | 提供实际调用的模块入口、CLI 参数或环境变量。例如 `python -m custom.plugins.studiowright`，由 Flow 的离线命令契约验证。 |
| 剧本 | `custom/playbooks/<genre>/` | 新增剧本要在所属管线的输入或适配器中明确指定模板路径，并从该入口验证读取与生成的阶段产物。 |
| 风格 | `custom/styles/<style_name>/` | 新增风格要由下游配置或调用方传入渲染器。YAML 风格可由调用方通过 `load_playbook(..., styles_dir=...)` 显式加载。使用实际渲染入口验证样式配置生效。 |
| 依赖和命令 | `custom/requirements.txt`、`custom/Makefile` | 本地安装与 CI 安装同时读取上游和下游依赖。从仓根用 `make -f custom/Makefile <target>` 调用。 |

部署入口见 [本地 CPU 部署](docs/LOCAL_DEPLOY.md)与 [Flow 部署](docs/FLOW.md)。
