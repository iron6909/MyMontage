# English Short Drama Playbook（A2 轻写实电影感反转短剧）

面向 **小红书 + 抖音英语学习号** 的 **2~3 分钟（120~180 秒）9:16 竖屏英语短剧 / 短故事** 复用剧本模板，绑定 OpenMontage 上游 `animation` 管线与 MyMontage 下游自定义工具链。

## 1. 锁定配置与入口路径

| 项目 | 锁定值 |
| --- | --- |
| 上游管线 | `pipeline_defs/animation.yaml`（`mixed` 视觉模式） |
| 音频架构 | `narrator_plus_characters`（旁白 + 多角色对话） |
| 合成运行时 | `render_runtime: hyperframes`（`composition_mode: atelier`，`renderer_family: animation-first`） |
| 画面规格 | `1080x1920`（9:16 竖屏），`30fps` |
| 风格 YAML | `custom/styles/light-cinematic-a2-drama/light-cinematic-a2-drama.yaml` |
| 剧集模板 JSON | `custom/playbooks/english-short-drama/template.json` |
| 工作区生成器 | `custom/playbooks/english-short-drama/build_workspace.py` |
| 单集产出目录 | `projects/<project-id>/`（gitignored，遵循 `AGENT_GUIDE.md` 工作区契约） |

显式加载风格与构建 HyperFrames 工作区：

```bash
.venv/bin/python custom/playbooks/english-short-drama/build_workspace.py \
  --spec projects/<project-id>/artifacts/episode_spec.json \
  --style custom/styles/light-cinematic-a2-drama/light-cinematic-a2-drama.yaml \
  --workspace projects/<project-id>/hyperframes \
  --emit-edit-decisions projects/<project-id>/artifacts/edit_decisions.json
```

---

## 2. 英语教学与叙事结构规范（CEFR A2 · 反转幽默）

### 语言难度（CEFR A2）
- **篇幅与语速**：全片 120~180 秒，英文总词数 **220~330 词**（约 115~130 WPM 学习者友好语速，留足笑点与反转停顿）。
- **句式与词汇**：高频日常词为主，单句 6~12 词；避免生僻长难句与大段语法说教。
- **高频表达点睛**：每集精选 **3~5 个高频地道口语表达**（如 `on the house`、`make up my mind`、`just in case`），在对应字幕出现时以温和的词汇角标提示，不打断剧情节奏。

### 四种反转幽默母题
1. **欧·亨利式巧合反转（O. Henry Twist）**：善意隐瞒或双向误判，结尾揭晓暖心又好笑的真相。
2. **契诃夫式荒诞讽刺（Chekhovian Irony）**：小人物为一桩日常小事过度紧张或虚张声势，结尾被现实轻轻戳破。
3. **纽约客都市日记（Metropolitan Diary）**：地铁、咖啡馆、旧书店、雨夜街头的真实偶遇与神回复。
4. **A2 日常语境误会（Everyday Misunderstanding）**：因双关词或生活习语产生的连锁误会，顺带自然教会该表达。

### 150 秒竖屏留存节奏
- **0:00–0:06 冷开场钩子（Cold Open）**：直接抛出反常画面或悬念一句（不放冗长片头 Logo）。
- **0:06–0:40 建置（Setup）**：确立人物目标、场景氛围与一个看似不起眼的伏笔道具。
- **0:40–1:45 升级（Escalation，2~3 拍对话交锋）**：冲突或误会层层加码，每 5~9 秒切景别/反应镜头。
- **1:45–2:15 反转落地（Twist Reveal）**：停顿 0.4s，用最简短的一句台词+反打镜头揭开谜底。
- **2:15–2:30 轻回味尾声（Tag & Takeaway）**：幽默余韵 + 本集 1 句金句/3 个核心表达收尾，促成收藏与二刷。

---

## 3. 按模型特性路由的资产生产策略

### A. 角色三视图与分镜图（Google Flow · `flow_image`）
每次正式生成前须遵循 `AGENT_GUIDE.md` 决策沟通与“先小样、后批量”原则。

| 用途 | 选定模型 | 推荐参数 | 选型理由 |
| --- | --- | --- | --- |
| **角色三视图 / 设定图** | `nano-banana-pro` | `--aspect 16:9 --size 2K` | 细节解析力与五官/服装结构稳定性最强；每名常驻角色先出一张干净中性光的三视图（正面、3/4 侧、侧面 + 2 个关键微表情），记录本地路径与 Flow `media_id` 作为全片唯一锚点 |
| **9:16 分镜关键帧** | `nano-banana-2.1` | `--aspect 9:16 --ref <角色三视图> [--ref <场景锚点图>]` | 日常免费额度充足、出图快且多参考图一致性好；用于批量产出 12~20 张竖屏分镜图，人物脸部统一构图在画面上 55% 区域，为下方双语字幕留白 |
| **快速构图草图（可选）** | `nano-banana-2-lite` | `--aspect 9:16` | 仅在构图拿不准时做低成本快速试拍，不用于终稿镜头 |

> 提示词必须叠加 `light-cinematic-a2-drama.yaml` 的 `asset_generation.image_prompt_prefix` 与 `consistency_anchors`，严禁生成画面内文字或硬烧字幕。

### B. 关键表演动态镜头（Google Flow · `flow_video`）
- **混排比例（`mixed` 模式）**：推荐 **40%~60% 核心表演/反转镜头用 `flow_video`**（6~10 条 4~8s 片段），其余 **40%~60% 过渡/反应/道具镜头用 `nano-banana-2.1` 分镜图 + HyperFrames 电影感微运镜（缓慢推近/平移/淡入淡出）**，既保住短片动感又控制 Flow 视频额度。
- **模型与绑定方式**：
  - 人物微表情与对话动作：`omni-flash-1.2`（支持 `3–15s`、`--aspect 9:16`、`--start <分镜图> [--end <下一分镜>] [--ref <角色三视图>]`）。
  - 城市空镜 / 氛围建立：`veo-lite`（`--duration 8 --aspect 9:16`）。
- **硬性额度保护**：提交任何正式视频生成前，**必须先跑 `--dry-run` 查询 Flow 实时报价**，并在正式命令中传入 `--max-credits <上限>`。

### C. 背景音乐（Google Flow Music · `flow_music`）
- 使用 `python3 -m custom.plugins.studiowright music --action create --model lyria-3.5 --length <2:00-3:00> --format mp3 --output projects/<project-id>/assets/music`。
- **不传 `--lyrics`**（纯器乐），避免人声与英语对白打架。
- 首次使用前确认 Flow Music 独立登录态（`sw auth login --url https://www.flowmusic.app/`）。

### D. 旁白与多角色配音（Google Cloud TTS — Gemini-TTS 主路径 + Gemini 3.8 备用 + OpenRouter 备选）
- **主路径 — `google_gemini_tts`**（`custom/tools/audio/gemini_tts.py`，通过 `custom.tools.loader.load()` 注册，也支持 CLI 直接调用）：
  - 默认模型 **`gemini-2.5-flash-tts`**（重点戏剧段落可升至 **`gemini-2.5-pro-tts`**），调用 Cloud Text-to-Speech REST `POST /v1/text:synthesize`，支持自然语言导演提示词 `prompt` 与方括号内联表演标记 `[sigh]`、`[laughing]`、`[uhm]`、`[sarcasm]`、`[whispering]`、`[short pause]`、`[long pause]`（避免 `[scared]`/`[curious]`/`[bored]` 等会被直接念出的形容词标记）。
  - **同账号备用高阶模型 — `gemini-3.8-flash-tts`（及 `gemini-3.8-flash-lite-tts`）**：共用同一份服务账号 JSON，传入 `--model gemini-3.8-flash-tts` 自动走 Gemini Enterprise Agent Platform API（`aiplatform.googleapis.com` `:generateContent`），使用 `speechMetadata.style` 控戏与尖括号标记 `<short pause>` / `<sigh>` / `<gasp>`；服务端固定输出 24 kHz 无损 WAV（指定 `.mp3` 时由本机 ffmpeg 自动转码）。
  - 30 种预置英文音色（如旁白 `Orus` / `Charon`，男主角 `Puck` / `Fenrir`，女主角 `Callirrhoe` / `Aoede` / `Kore`），3 人及以上（旁白 + 多角色）按句单人合成再拼接；纯 2 人对话也可用 `turns` + `speakers` 原生双人合成（≤4,000 字节）。正式生产建议优先输出 `.wav` 供 `hyperframes transcribe` 精准逐词对齐。
  - 鉴权：服务账号 JSON `GOOGLE_APPLICATION_CREDENTIALS`（放 `~/.harness/providers.env` 或环境变量，账号需授予 **`Agent Platform User`（`roles/aiplatform.user`）**，并启用 `texttospeech.googleapis.com` 与 `aiplatform.googleapis.com`）；检查命令：`.venv/bin/python -m custom.tools.audio.gemini_tts --check-auth`。
- **备选路径 — `openrouter_audio` / 上游 `google_tts` (Chirp 3 HD)**：
  - 若需要备用网关或 Chirp 3 HD 免费层，**先向用户说明原因并获批**（禁止静默切换），再切至 `openrouter_audio`（`custom/tools/audio/openrouter_audio.py`）或 `google_tts`。

---

## 4. 英汉双语逐词字幕与小红书/抖音安全区规范

1. **逐词时间戳对齐**（`google_gemini_tts` 与 `openrouter_audio` 均不直接输出逐词时间戳）：
   - 对每句生成的英文 `.wav` 音频调用本机免费的 OpenMontage 上游 `transcriber`（`faster-whisper`，已验证可用）：
     ```python
     from tools.analysis.transcriber import Transcriber
     Transcriber().execute({"input_path": "<line_audio.wav>", "model_size": "base", "language": "en"})
     ```
     *（如系统装有 `cmake` + `whisper.cpp`，也可用 `npx hyperframes transcribe <line_audio.wav> --model small.en`。）*
   - 将每句的词级 JSON（`[{text, start, end}]`）加上该句在全片时间线的起始偏移 `start_sec`，并用原始英文字幕校对专有名词与标点，写入 `episode_spec.json` 的 `cues[].words`。
   - 译文 `zh` 由剧本阶段逐句锁定，保证口语地道且不断错意群。
2. **小红书 / 抖音 9:16 视觉安全区（1080×1920）**：
   - **底部留空**：`>= 480px`（避开底部标题文案、话题标签与进度条）。
   - **右侧留空**：`>= 190px`（避开点赞/收藏/评论/转发竖排按钮）。
   - **顶部留空**：`>= 260px`（避开状态栏、搜索框与关注条）；**左侧留空**：`>= 96px`。
   - **手机端可读字号下限**：全部标签、说话人徽章、词汇角标字号 `>= 28px`。
3. **双语逐词呈现规范**：
   - 顶部轻量说话人色标（如 `Narrator` / `Leo` / `Clara`）+ 可选 A2 重点词条（`key_phrase`）。
   - 第一行：英文逐词卡拉 OK 高亮（当前词变暖金 `#F5B942` + 1.06x 柔和弹出，已读词纯暖白 `#F7F2E8`，未读词半透）。
   - 第二行：同步中文译文（`Noto Sans SC`，柔和暖白，不喧宾夺主）。

---

## 5. 阶段审批清单（遵循 `pipeline_defs/animation.yaml`）

1. `research`：收集选材原型与 A2 表达语料 → `research_brief.json`
2. `proposal` **[人工审批]**：3 个反转故事备选、确认 `render_runtime=hyperframes` / `composition_mode=atelier`、工具与额度预估 → `proposal_packet.json` + `decision_log.json`
3. `script` **[人工审批]**：完整英汉双语台词、角色音色分配、停顿提示与 A2 重点表达 → `script.json`
4. `scene_plan` **[人工审批]**：镜头表（标注 `still` / `video`、景别、运镜、所需参考图） → `scene_plan.json`
5. `assets` **[人工审批]**：先小样（1 张角色三视图 + 1 张分镜 + 2 句配音）→ 获批后批量出图/出视频/出配乐/逐词对齐 → `asset_manifest.json` + `episode_spec.json`
6. `edit`：调用 `build_workspace.py` 生成 `hyperframes/index.html` 与 `edit_decisions.json`
7. `compose`：执行 `npx hyperframes lint`、`validate`、`inspect`、`snapshot` 抽帧目检 → 通过 `video_compose` / `hyperframes_compose(operation="render_existing")` 渲染 `renders/final.mp4` 并做音画自检
8. `publish` **[人工审批]**：输出小红书/抖音封面、标题、A2 核心词汇笔记与成片包 → `publish_log.json`
