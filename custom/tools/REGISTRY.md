# custom/tools/REGISTRY.md — 六工具调用注册表

本文件只写**已经实现、并且已经核对过**的接口。核实环境：worktree
`feat/MOZ-27`（基线 `35cb953`），Asia/Taipei 2026-11-19；StudioWright
`0.2.13`（本机 `/home/banko/.local/bin/sw`）。

模型条数（70/39/35）与默认模型来自 MOZ-24/25/26 在 2026-11-19 对官方目录的
核对记录（写在各自源码注释里）。本单**没有**重新拉取目录、也没有做任何真实
生成（无 key、视频费用未界定）；若目录已变，以工具提交前的目录校验报错为准。

- 未实现的能力一律写在文末「未实现（不要当成已支持）」里。
- 所有命令都在**仓库根目录**执行（`import custom.*` 依赖这个 cwd）。
- 需要钱的路径（OpenRouter 付费生成、Flow 生成）与 `--dry-run` / `--mock-*`
  在输出里分得很清楚：前者打印 `mode: live`，后者打印 `mode: dry-run` 或
  `mode: mock`。日志里出现 mock 就不是实测。

## 0. 两条路线与通用约定

| 路线 | 工具 | 鉴权 | 花费 |
| --- | --- | --- | --- |
| Google Flow（StudioWright `sw` 驱动本机 Chrome） | `flow_image` / `flow_video` / `flow_music` | 不需要 API key；`sw auth login` 的浏览器登录态 | 自己的 Flow / Flow Music 额度（工具只报 `cost_status: flow_credits`，从不换算美元） |
| OpenRouter HTTP API | `openrouter_image` / `openrouter_audio` / `openrouter_video` | `OPENROUTER_API_KEY` | 真实美元；只有服务器回的 `usage` 会被打印，不自己估价 |

环境变量（全部可选；缺省值来自代码，不是猜测）：

| 变量 | 作用 | 缺省 |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | OpenRouter 鉴权 | 无；缺失时 live 路径直接退出 2，什么都不发 |
| `OPENROUTER_BASE_URL` | 覆盖 API 根（自建代理 / 本地回放） | `https://openrouter.ai/api/v1` |
| `FFMPEG` | 音频归一化用的 ffmpeg | 先探 `/usr/bin/ffmpeg`，再探 `PATH`；**第一个能 `-version` 成功的**才算数 |
| `FFPROBE` | 视频写入前校验用的 ffprobe | 同上顺序（`/usr/bin/ffprobe` 优先于 `PATH`） |

探测顺序固定为 `显式参数 → 环境变量 → /usr/bin → PATH`。本机 Homebrew 的
`ffmpeg`/`ffprobe` 因缺 `libx265.so.216` 起不来，所以会被跳过，实际使用
`/usr/bin/ffmpeg`、`/usr/bin/ffprobe`（8.0.1）。日志里每次都打印实际二进制。

OpenRouter 客户端重试策略（`custom/tools/common/openrouter_client.py`）：
`408/425/429/5xx` 与超时/连接错误可重试，退避 `1,2,4,8…` 秒（上限 8），
默认 `--max-attempts 3`；其它 4xx 立刻失败。错误文本会先抹掉 key 与
`Bearer` 头再打印。

Flow 三个工具是注册表里的 `BaseTool`（`flow_image`/`flow_video`/`flow_music`），
命令行入口是 `custom/plugins/studiowright/cli.py`。OpenRouter 三个工具是
**独立 argparse 脚本，不是 BaseTool**，不出现在 `registry` 里。

---

## 1. flow_image（Flow / Nano Banana 图像）

- 模块：`custom/tools/image/flow_image.py`（`FlowImage`）
- capability/provider：`image_generation` / `google_flow`
- 注册状态：`sw` 不在 PATH 时 `UNAVAILABLE`

```bash
python3 -m custom.plugins.studiowright image --prompt "a red fox in snow" \
    --output out/flow_image            # 默认目录就是 out/flow_image
```

| 参数 | 必需 | 取值 / 限制 |
| --- | --- | --- |
| `--prompt` | ✅ | 生成提示词 |
| `--model` | | `nano-banana-2.1`（默认）/ `nano-banana-pro` / `nano-banana-2-lite` |
| `--aspect` | | `16:9`（默认）/ `4:3` / `1:1` / `3:4` / `9:16` |
| `--count` | | `1`–`4`，默认 `1` |
| `--size` | | 下载尺寸 `1K` / `2K`（默认）/ `4K`；2K/4K 由 Flow 放大 |
| `--ref` | | 参考图路径或 Flow media id，可重复（多参考） |
| `--output` | | 输出目录（不是文件） |
| `--timeout` | | 秒；默认 `120 × count` |
| `--retries` | | 仅 **timeout** 重试，默认 `0`；重试=重新提交，可能重复扣额度 |
| `--dry-run` | | **客户端**预检，不碰浏览器、不扣额度 |
| `--plan` | | 只打印拼好的 `sw` argv，连 `sw` 没装都能跑 |
| `--profile` | | StudioWright 多账号 profile |

产物：目录下的 `*.jpg/*.png/*.webp`，结果里带 `files`。`sw` 内联下载失败时会
用 `sw media list` + `sw download` 按 media id 补下载，不重生成、不重复扣费。

限制：必须有已登录的 Chrome 与有效 Flow 套餐；约 20 s/张；不允许 headless
服务器；没有远端报价接口，所以 `--dry-run` 只做本地参数校验。未暴露
`sw --project`。

---

## 2. flow_video（Flow / Omni Flash、Veo 3.1）

- 模块：`custom/tools/video/flow_video.py`（`FlowVideo`）
- capability/provider：`video_generation` / `google_flow`

```bash
python3 -m custom.plugins.studiowright video --prompt "drone shot over a fjord" \
    --model veo-lite --duration 8 --aspect 16:9 --output out/flow_video
```

| 参数 | 必需 | 取值 / 限制 |
| --- | --- | --- |
| `--prompt` | ✅ | 视频提示词 |
| `--model` | | `omni-flash`（默认）/ `omni-flash-1.2` / `veo-lite` / `veo-fast`（2026-12-05 退役）/ `veo-quality`（同） |
| `--duration` | | 秒，默认 `8`；Omni 1.1 只收 `4/6/8/10`，Omni 1.2 收 `3–15` |
| `--aspect` | | `16:9`（默认）/ `9:16` |
| `--resolution` | | `360p` / `720p`（默认）；仅 Omni |
| `--size` | | 下载尺寸 `720p` / `1080p`（默认）/ `4K` / `270p` |
| `--start` / `--end` | | 首帧 / 尾帧：本地路径或 Flow media id |
| `--ref` | | 参考图（`--mode ingredients`），可重复 |
| `--max-credits` | | Flow 报价超过这个整数额度就拒绝提交（`sw` 只收 INTEGER） |
| `--output` | | 输出目录 |
| `--timeout` | | 默认 `max(300, 2 × 估算)` |
| `--retries` | | 仅 timeout；默认 `0` |
| `--dry-run` | | 调 `sw video --dry-run`：**只向 Flow 问报价，不提交**（需要 `sw` 且能连到已登录的 Chrome；连不上就是 `status: error`，不花钱） |
| `--plan` | | 只打印 argv，不需要 `sw` |

产物：`*.mp4`。限制：需要已登录 Chrome + Flow 套餐；30–120 s/条；未暴露
`sw --count` / `--project`；画面质量与首尾帧可用性随模型不同，以 Flow 页面
实际报价/报错为准。本机 run-worker（无可用 Chrome 调试端口）实测 `--dry-run`
直接返回 `sw video exited 1: ... Chrome did not open its debugging port`，属环境
阻断而非工具缺陷；`--plan` 在无 Chrome、无 `sw` 时也能跑。

---

## 3. flow_music（Flow Music / Lyria）

- 模块：`custom/tools/audio/flow_music.py`（`FlowMusic`）
- capability/provider：`music_generation` / `google_flow_music`
- 前置：`sw auth login --url https://www.flowmusic.app/`（与 Flow 图像/视频是**不同**的登录）

```bash
python3 -m custom.plugins.studiowright music --action create \
    --prompt "moody indie folk, warm male vocal" --length 1:30 --output out/flow_music

python3 -m custom.plugins.studiowright music --action trim --song-id last \
    --start 0:05 --end 0:35 --output out/flow_music
```

| 参数 | 必需 | 取值 / 限制 |
| --- | --- | --- |
| `--action` | | `create`（默认）/ `extend` / `cover` / `replace` / `variation` / `trim` / `stems` |
| `--prompt` | `create`/`cover` ✅ | 风格/情绪描述；其余 action 可不给 |
| `--song-id` | | 编辑类 action 用；song id、URL 或 `last`（缺省 `last`） |
| `--model` | | `lyria-3.5`（默认）/ `lyria-3-pro` |
| `--lyrics` / `--lyrics-file` | | 只对 `create`/`extend`/`cover`/`replace` 发送；不给就是纯音乐 |
| `--length` | | `M:SS`，范围 **1:00–3:00**，默认 `1:30` |
| `--bpm` | | 只对 `create` 发送，整数 |
| `--title` | | 同上（四类文本 action） |
| `--format` | | `mp3`（本工具默认）/ `wav` / `m4a` |
| `--start` / `--end` | `trim`/`replace` ✅ | `M:SS`；这两个 action 缺任一参数直接报错，不会提交 |
| `--output` | | 输出目录 |
| `--dry-run` | | 客户端预检（`sw music` 无远端报价） |
| `--plan` | | 打印 argv |

产物：`*.mp3/*.wav/*.m4a`；`stems` 一次产出多个文件（drums/bass/vocals/other）。
限制：最短 1:00；需要 Flow Music 套餐；`stems`/`variation` 不接受
`--lyrics`/`--title`（`sw` 会以 UsageError 拒绝）；未暴露 `--seed`、
`--cover`、`--cover-prompt`。

---

## 4. openrouter_image（OpenRouter 图像）

- 模块：`custom/tools/image/openrouter_image.py`（argparse 脚本）
- 路由：`POST {base}/images`（**只**用主路径，不实现 `/images/generations` 兼容路由）

```bash
python3 -m custom.tools.image.openrouter_image \
    --prompt "a red fox in snow" --model openai/gpt-image-2 \
    --aspect-ratio 16:9 --output out/fox.png
```

| 参数 | 必需 | 取值 / 限制 |
| --- | --- | --- |
| `--prompt` | ✅ | 提示词 |
| `--output` | ✅ | 文件路径（原子写入）；传目录直接报错 |
| `--model` | | 默认 `openai/gpt-image-2`；2026-11-19 在 `GET /api/v1/images/models`（70 条）中核实存在 |
| `--aspect-ratio` | | 默认 `16:9`；透传，如 `1:1` / `9:16` / `auto` |
| `--quality` | | `auto` / `low` / `medium` / `high`（不给就不发） |
| `--timeout` | | 单请求秒数，默认 `180` |
| `--max-attempts` | | 默认 `3`（见第 0 节重试策略） |
| `--dry-run` | | 打印请求，不发网、不写文件 |
| `--mock-response FILE` | | 回放一份捕获的响应 JSON，走真实解码+落盘路径 |

响应解析：`data[0]` 里的 `b64_json`（含 `data:` URL 形式）优先，其次
`url`/`image_url`（仅 https）；两者都没有 → 退出 1。媒体类型优先取
`media_type`，否则按字节嗅探（png/jpeg/webp/gif/svg）。

限制：**不**在提交前校验模型 slug（图像路由没有客户端目录检查），写错模型
就是服务器 4xx；没有批量/多图循环；没有尺寸像素参数（只有宽高比）。

---

## 5. openrouter_audio（OpenRouter 语音合成）

- 模块：`custom/tools/audio/openrouter_audio.py`
- 路由：`POST {base}/audio/speech`；目录：`GET {base}/models?output_modalities=speech`

```bash
python3 -m custom.tools.audio.openrouter_audio \
    --text "Hello there." --voice en_paul_neutral --output out/line.wav
```

| 参数 | 必需 | 取值 / 限制 |
| --- | --- | --- |
| `--text` / `--prompt` | ✅（二选一） | 即 API 的 `input`；空字符串报错 |
| `--output` | ✅ | 必须以 `.wav` 或 `.mp3` 结尾 |
| `--model` | | 默认 `mistralai/voxtral-mini-tts-2603`；2026-11-19 在语音目录（39 条）中核实 |
| `--voice` | | 必须是**该模型** `supported_voices` 里的 id；目录可读时不匹配就拒绝发送 |
| `--speed` | | 透传；官方说明「支持的模型才生效，其它忽略或 400」，工具不声称已生效 |
| `--response-format` | | `mp3`（默认）/ `pcm` |
| `--pcm-sample-rate` | `pcm` ✅ | 原始 pcm 无头，各家不同（如 24 kHz / 44.1 kHz） |
| `--ffmpeg` | | 覆盖探测顺序 |
| `--dry-run` / `--mock-response FILE` | | 同上；mock 文件是**裸音频字节** |

产物：用 ffmpeg 归一化成 `.wav`（`pcm_s16le`）或 `.mp3`（`libmp3lame -q:a 2`）；
响应体若是 JSON 或空 → 判定为错误，不落盘。原始音频没有 `usage` 块，所以
**不打印任何费用**。

限制：单条语音；不实现流式；**没有逐词/句级时间戳对齐**、没有多角色、
没有 `srt` 输出（那属于别的工单）。

---

## 6. openrouter_video（OpenRouter 异步视频）

- 模块：`custom/tools/video/openrouter_video.py`
- 路由：`POST {base}/videos` → 轮询 `GET {base}/videos/{id}` → `GET {base}/videos/{id}/content?index=0`

```bash
python3 -m custom.tools.video.openrouter_video \
    --prompt "a red fox running in snow" --model google/veo-3.1 \
    --duration 4 --aspect-ratio 16:9 --output out/fox.mp4
```

| 参数 | 必需 | 取值 / 限制 |
| --- | --- | --- |
| `--prompt` | ✅ | 提示词；空则退出 2 |
| `--output` | ✅ | 必须以 `.mp4` 结尾（内容路由返回 mp4） |
| `--model` | | 默认 `google/veo-3.1`；2026-11-19 在 `GET /api/v1/videos/models`（35 条）中核实 |
| `--duration` | | 秒；按目录 `supported_durations` 预校验 |
| `--aspect-ratio` | | 如 `16:9/9:16/1:1`；按目录 `supported_aspect_ratios` 预校验 |
| `--image-url` / `--first-frame` | | https 首帧图；必须是该模型 `supported_frame_images` 含 `first_frame` |
| `--ffprobe` | | 覆盖探测顺序；找不到可用 ffprobe 直接退出 2 |
| `--poll-interval` | | 默认 `30` s |
| `--poll-timeout` | | 默认 `900` s；超时放弃（服务器上的任务可能仍在跑） |
| `--max-bytes` | | 下载上限，默认 `512 MiB` |
| `--timeout` / `--max-attempts` | | 单请求超时 / 重试，默认 `180` / `3` |
| `--dry-run` | | 打印请求与轮询计划，不发网 |
| `--mock-dir DIR` | | 回放 `submit.json` + `poll*.json` + `content.bin`，走**真实**轮询/下载/ffprobe 路径 |

流程细节：job id 只接受 `[A-Za-z0-9_-]{1,128}`；状态只认
`pending`/`in_progress`/`completed`/`failed`/`cancelled`/`expired`；
`unsigned_urls` 只打印不跟随，下载始终走由 job id 重建的内容路由；下载先写
同目录临时文件，**ffprobe 确认有视频流之后**才 `os.replace` 到 `--output`，
所以非视频响应永远不会落到目标路径。

费用：按输出秒计价（Veo 官方示例约 $0.50/s），本工具不做任何估价；服务器回
`usage.cost` 才打印。付费视频生成前必须能界定上限，否则只用 `--dry-run` /
`--mock-dir`。

限制：只支持 first-frame 图生视频（无 last-frame、无多参考图）；不做任务
取消；不做多输出索引（固定 `index=0`）；没有回调/Webhook；不做放大/转码。

---

## 未实现（不要当成已支持）

- OpenRouter 图像：`/images/generations` 兼容路由、批量 N 张、尺寸像素参数。
- OpenRouter 语音：流式、逐词时间戳、多角色对白、直接出 `srt`。
- OpenRouter 视频：last-frame、多参考图、任务取消、Webhook、多输出索引。
- Flow：headless/服务器运行；`sw --project`、`sw --count`（视频）、
  `sw --seed`/`--cover`（音乐）都未在本仓 CLI 暴露。
- fal / Harness：本仓没有安装它们的 Bot 插件，只按只读方式参照本地
  `tools/` 源码契约。
- Piper TTS 补丁：`custom/patches/piper_tts_speaker.patch` 由
  `custom/scripts/setup_local.sh` 在运行期套用，**不是本批六工具之一**，也不
  会注册成工具（`custom/tools/loader.py` 里的 `_SKIP`）。

## 相关文件

| 内容 | 路径 |
| --- | --- |
| OpenRouter 共用客户端 | `custom/tools/common/openrouter_client.py` |
| Flow CLI / `sw` 调用层 | `custom/plugins/studiowright/cli.py`、`client.py` |
| Flow 免侵入说明 | `custom/docs/FLOW.md` |
| 契约自检 | `custom/tests/test_flow_contract.py`、`custom/tests/test_openrouter_*.py` |
| 本地部署 | `custom/docs/LOCAL_DEPLOY.md` |
