# Google Flow 与 StudioWright

MyMontage 的 `flow_image`、`flow_video` 和 `flow_music` 调用 [StudioWright fork](https://github.com/iron6909/studiowright) 的 `sw` CLI。原生 StudioWright MCP 由同一个可执行文件的 `sw mcp` 启动。两条入口共用 Google Flow 或 Flow Music 的 Chrome 登录态和任务记录。

## 安装与检查

```bash
make -f custom/Makefile sw-install
make -f custom/Makefile sw-credentials
make -f custom/Makefile sw-tools
make -f custom/Makefile sw-doctor
```

`sw-install` 使用 `custom/Makefile` 记录的 fork 提交。已登录的 Chrome profile 仍由 StudioWright 管理。首次登录 Flow 用 `sw auth login`，首次登录 Flow Music 用 `sw auth login --url https://www.flowmusic.app/`。MCP 客户端的 stdio 启动命令可以直接使用已安装的 `sw mcp`。从 fork 安装的 Claude Code 插件也使用同一提交。

StudioWright 自行启动或复用 profile 对应的 Chrome。`default` 使用 `127.0.0.1:9319`，其他 profile 的端口为 `9300 + crc32(profile) % 90`。MyMontage 将父进程环境传给 `sw`，并在两种 `NO_PROXY` 拼写中保留现有绕行项与 loopback。`chrome-cdp-check` 及 `CHROME_CDP_URL` 属于独立的 Chrome 调试服务检查。

## 工具入口

```bash
python3 -m custom.plugins.studiowright image --prompt 'a red fox in snow' --output out/flow_image
python3 -m custom.plugins.studiowright video --prompt 'clouds drift over a fjord' --model omni-flash-1.2 --duration 4 --dry-run
python3 -m custom.plugins.studiowright music --prompt 'quiet piano, instrumental' --length 1:00 --output out/flow_music
```

`bash custom/scripts/flow_cli.sh` 也支持这些子命令。进程内调用可通过 `custom.tools.loader.load()` 注册三个 `BaseTool`。完整参数见 [工具注册表](../tools/REGISTRY.md)。

- `--output` 指向目录。成功结果给出实际落盘的绝对路径。
- 图像和视频默认下载原始尺寸。`--size 2K`、`--size 4K`、`--size 1080p` 等显式选择会由 Flow 处理。若已生成的媒体需要回退到原始尺寸，结果的 `detail.sw_data.notes` 会说明。
- 图像和视频支持 `--project PROJECT_ID`。
- `--plan` 只构造命令。图像和音乐的 `--dry-run` 只做本地参数检查。视频的 `--dry-run` 从 Flow 读报价，`--max-credits` 为正式提交设置上限。
- `--timeout` 设定 `sw` 进程的等待时间。`--retries` 只增加对同一个任务的状态查询次数。

## 状态与下载

每次正式调用分配一个 `job_id`。结果为 `ok` 时，文件已在输出目录通过格式和数量检查。`pending` 表示提交结果待确认，或媒体已生成但仍有文件待下载，退出码为 `1`。结果保留已经验证的文件、已确认的 `media_ids` 和 `project_id`。音乐的歌曲 ID 在 `detail.songs` 中。调用还剩时间时，适配器会按已确认的 ID 补下载。

查看任务记录或在稍后补下载时，使用原来的 profile 和 ID。以下命令只查询或下载已有任务的产物。

```bash
sw job "$JOB_ID" --json
sw download "$MEDIA_ID" --project "$PROJECT_ID" --out out/recovered --json
sw music download "$SONG_ID" --format mp3 --out out/recovered --json
```

原生 MCP 对应 `flow_job`、`flow_download` 和 `music_download`。如果提交回包丢失且没有确认到媒体 ID，可以用 `sw media list --project "$PROJECT_ID" --json` 查看项目里的媒体并人工确认归属。

## 离线检查

```bash
python3 -m custom.tests.test_flow_contract
python3 -m custom.tests.test_flow_cdp_preflight
python3 -m custom.tests.test_flow_sw_env
```
