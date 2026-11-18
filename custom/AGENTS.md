# MyMontage 免侵入式二开宪章 (Zero-Intrusion Development Rules)

本项目是 `OpenMontage` 的下游派生仓库。为确保随时执行 `git pull upstream main` 零冲突，所有 Agent（Pi / agy）在本仓库开发时必须遵守以下五条死律：

## 1. 上游原生文件 100% 只读铁律 (Zero Upstream Modification)
- **严禁直接修改或删除**任何 `OpenMontage` 上游已追踪的文件（包括但不限于根目录 `AGENTS.md`、`SKILL.md`、`Makefile`、`.gitignore`、`config.yaml`、`requirements.txt`、`tools/**` 及 `remotion-composer` 下的 `Root.tsx`、`tsconfig.json`）。
- 仓库与 `upstream/main` 的 Git 差异中，`custom/` 目录以外的文件变更数必须为 **0**。

## 2. 四大定制资产强制归口 `custom/` 命名空间
1. **工具 (Tools)**：自研 TTS、逐词时间戳对齐、视觉/音频处理工具一律放入 `custom/tools/<domain>/`。若需修复上游原生工具 Bug，优先在 `custom/tools/` 编写 Wrapper；若必须打补丁，须将 `.patch` 存入 `custom/patches/`，由 `custom/scripts/setup_local.sh` 在运行期自动 `git apply` 并执行 `git update-index --skip-worktree <上游文件>`，严禁将修改提交进 Git。
2. **插件 (Plugins)**：自研管线插件与外部 API 路由一律放入 `custom/plugins/<name>/`，通过 CLI 参数或环境变量挂载，严禁修改上游 `__init__.py` 或核心注册表。
3. **剧本 (Playbooks)**：英语微短剧等领域的分镜模板、叙事规则与系统提示词一律放入 `custom/playbooks/<genre>/`，严禁修改上游默认剧本模板。
4. **风格 (Styles)**：字幕动效、逐词高亮样式、配色与画幅预设一律放入 `custom/styles/<style_name>/`，通过配置参数注入渲染器，严禁硬编码侵入 `Root.tsx`。

## 3. 依赖、配置与命令入口外挂化
- 新增依赖写入 `custom/requirements.txt`，新增配置写入 `custom/config/`，新增快捷命令写入 `custom/Makefile`（使用 `make -f custom/Makefile <target>` 调用）。

## 4. 运行产物与验证证据零入库
- `.task/`、`out/`、`projects/` 仅通过本地 `.git/info/exclude` 忽略，**严禁将 `.task/evidence/` 下的任何日志或截图 `git add` 提交入库**。
