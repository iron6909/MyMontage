# Google Flow (studiowright) — 免侵入接入说明

Flow 的图像 / 视频 / 音乐走 [StudioWright](https://github.com/umutkocak/studiowright)
的 `sw` CLI：它用 Playwright 驱动**本机已登录的 Chrome** 打开 Google Flow，
不需要 API key，消耗你自己 Google 账号的 Flow 额度。

本仓不修改任何上游文件，全部代码在 `custom/`：

| 资产 | 路径 |
|---|---|
| 图像工具 | `custom/tools/image/flow_image.py` |
| 视频工具 | `custom/tools/video/flow_video.py` |
| 音乐工具 | `custom/tools/audio/flow_music.py` |
| `sw` 调用层 / CLI 入口（插件） | `custom/plugins/studiowright/` |
| CLI 启动器 | `custom/scripts/flow_cli.sh` |
| 凭证存在性检查 | `custom/scripts/sw_credentials.py` |
| 本机 CDP 预检（共享） | `custom/tools/common/chrome_cdp.py` |
| `chrome-cdp.service` 模板 | `custom/config/systemd/chrome-cdp.service` |
| 契约自检 | `custom/tests/test_flow_contract.py`、`custom/tests/test_flow_cdp_preflight.py` |

## 入口（在仓根可跑，不再 `ModuleNotFoundError: custom`）

三个工具都是 `BaseTool`（注册表契约），**本身不是 argparse 脚本**。原来
`python3 custom/tools/graphics/flow_image.py --help` 会因为在 `sys.path` 里
放的是 `custom/tools/graphics` 而失败。现在只有一个入口，且会自己切到仓根：

```bash
cd ~/MyMontage                       # 任何目录也可以，启动器会自己 cd 回来
python3 -m custom.plugins.studiowright image --help
python3 -m custom.plugins.studiowright video --help
python3 -m custom.plugins.studiowright music --help

bash custom/scripts/flow_cli.sh image --help          # 等价，不依赖 cwd
make -f custom/Makefile flow-help                     # 同上，一次跑完三个
```

`python3 -m ...` 是唯一被文档化的 module 入口；直接跑文件路径永远不行
（`custom` 不在 `sys.path`），这正是本次修正的失败原因。

## 三种「不花钱」的模式

| 模式 | 命令 | 会不会碰 Flow |
|---|---|---|
| `--plan` | `... video --prompt "x" --plan` | 不会。只把拼好的 `sw` argv 打印出来（连 `sw` 没装都能跑） |
| `--dry-run` | `... image --prompt "x" --dry-run` | 图像/音乐：客户端直接返回，不碰浏览器；视频：`sw video --dry-run`，只向 Flow 问价格，不提交 |
| `--retries` | `... image --prompt "x" --retries 1` | 仅在 **timeout** 时重试（对齐各工具的 `RetryPolicy(retryable_errors=["timeout"])`）。重试会重新提交，可能重复扣额度，默认 0 |

成功才算成功：CLI 会检查 `files` 里的路径**在磁盘上真的存在**，否则输出
`status: "error"`。标准状态永远是同一个 JSON：

```json
{"status": "ok", "tool": "flow_image", "provider": "google_flow",
 "output": "out/fox/xxx.jpg", "files": ["out/fox/xxx.jpg"], "attempts": 1,
 "duration_seconds": 21.4, "command": ["/home/.../sw", "image", "...", "--json"]}
```

`--prompt` / `--output` / `--timeout` / `--retries` 是三个工具共有的；其余
`--model` / `--aspect` / `--count` / `--size` 等按子命令给出（见 `--help`）。

## 本机 CDP 预检（live 运行的前置门禁）

除 `--plan` / `--dry-run` 之外的真实运行，提交前会过一道 CDP 预检
（`custom/tools/common/chrome_cdp.py`，由 `custom/plugins/studiowright/cli.py`
调用一次）。三处入口（`python3 -m ...`、`flow_cli.sh`、`make flow`）都经过它，
没有任何一个工具模块各自复制一份。

| 情形 | 行为 |
|---|---|
| `CHROME_CDP_URL` 未设置 | 探 `http://127.0.0.1:9222/json/version`（本机默认） |
| 端点有响应 | 直接放行，不碰 systemd |
| loopback 端点无响应 | `systemctl --user start chrome-cdp.service` 一次 → 等 3 s → 再探一次 |
| 重探仍失败 | `status: "error"`，退出码 1，错误里写明端点与 unit 状态 |
| `CHROME_CDP_URL` 指向**非** loopback | 只探、**不**启动本机服务；连不上就是错误 |
| URL 里带 userinfo / query | 探针本身不带凭据；日志与 JSON 里只有 `***@host:port` 与 `?***` |

单独检查（与 Flow 入口同一份代码；只会在 loopback 且 unit 已安装时才去 start）：

```bash
make -f custom/Makefile chrome-cdp-check      # 打印 JSON 状态；端点通=0
python3 -m custom.tools.common.chrome_cdp     # 等价
```

unit 模板是 `custom/config/systemd/chrome-cdp.service`：loopback `9222`、独立
`~/.harness/chrome-flow-profile`、`--password-store=basic`、`--no-first-run`、
`--no-default-browser-check`、`Restart=always`，起始页
`https://labs.google/fx/tools/flow`；每个开关各占一个参数（多行续行）。
本仓**不**安装、不 enable、不 start 它：

```bash
make -f custom/Makefile chrome-cdp-install    # 只复制到 ~/.config/systemd/user/；已存在则拒绝
# 部署（运维步骤，不在本仓自动执行）：systemctl --user enable --now chrome-cdp.service
```

边界：预检只验证端点可达——不登录 Google、不填表单、不开隧道/代理、不动你日常
的 Chrome profile、不杀已在跑的 Chrome。另外 `sw` 0.2.13 目前仍按自己的 9300+
loopback 端口启动自己的 Chrome；把 `sw` 接到这条端点上**不在本单范围**，本单只
把「端点没起来」变成一句明确的非 0 错误。

## 已核对的 `sw` 契约（StudioWright 0.2.13，本机安装）

`sw` 是 click 命令行，不是 argparse；`sw ... --json` 只打印**一个** JSON 对象
（`_emit` = `json.dumps(..., indent=1)`），结果里的文件路径在 `files` 键。
逐行 `json.loads` 会解析失败——`client.parse_sw_json` 按整段解析。

核对时修正的三处真实缺陷：

1. `sw music trim` / `sw music replace` 的 `--start` / `--end` 是**必填**，
   原实现从不传，命令必然报 UsageError → 现在作为输入校验 + 传参。
2. `sw --max-credits` 是 `INTEGER`，原实现透传 `str(10.0)` → 现改成 `int()`。
3. `sw music stems / variation` 只接受 `--format/--out/--json`，给它们塞
   `--lyrics/--title` 会被 click 拒绝 → 现在按 action 分别拼参数。
   顺带：`--format` 现在对所有 action 都生效（原来只有 create/stems 传）。

`custom/tests/test_flow_contract.py` 把「我们发出的 `--flag` 必须是
`sw --help` 里存在的」和「必填项必须发出去」固化成断言：

```bash
python3 -m custom.tests.test_flow_contract
```

## 凭证

`sw` 的登录态在 StudioWright 自己的 Chrome profile 里
（默认 `~/.local/share/studiowright/profiles/<profile>`，`STUDIOWRIGHT_HOME`
可覆盖）。本仓的 Flow 工具**不读 cookie、不导出 session、不写 token**：它们
只调用 `sw`，登录态归 `sw` 管。

任何密钥/会话只允许来自环境变量、被忽略的 `.env` 或 `~/.harness/`。检查只会
打印「存在与否 + 路径」，不会打印值：

```bash
make -f custom/Makefile sw-credentials      # 或 python3 custom/scripts/sw_credentials.py
```

输出是 bool + path（`sw_on_path`、`chrome_profile`、`repo_env_exists`、
`repo_env_git_ignored` …）。`.env` 已幂等写入 `.git/info/exclude`。

## 一次性准备

```bash
make -f custom/Makefile sw-install        # uv tool install git+.../studiowright
make -f custom/Makefile sw-auth-flow      # sw auth login
make -f custom/Makefile sw-auth-music     # sw auth login --url https://www.flowmusic.app/
make -f custom/Makefile sw-hide           # 把 Chrome 挪到屏幕外（不要最小化）
make -f custom/Makefile sw-doctor         # 只读检查 Flow 页面钩子，不花钱
make -f custom/Makefile sw-tools          # 确认三个工具都已注册
```

## 迁移记录（本单以前 → 现在）

| 旧路径 | 新路径 |
|---|---|
| `custom/tools/graphics/flow_image.py` | `custom/tools/image/flow_image.py` |
| `custom/tools/music/flow_music.py` | `custom/tools/audio/flow_music.py` |
| `custom/tools/video/flow_video.py` | 不变 |
| `custom/tools/_sw.py` | `custom/plugins/studiowright/client.py` |

仓内没有任何地方再引用旧路径，因此旧文件直接删除，没有留两份业务代码。
`custom/tools/audio/` 现在是包（会被 `registry.discover` 走查），
`custom/tools/loader.py` 显式跳过其中的 `piper_tts.py`——那是 piper 补丁的
参照副本，注册它会用快照顶替真正的上游 provider。
