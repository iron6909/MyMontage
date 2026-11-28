# English Short Drama Playbook（A2 Anime Ghibli 反转短剧）

面向 **小红书 + 抖音英语学习号** 的 **2~3 分钟（120~180 秒）9:16 竖屏英语短剧 / 短故事** 复用剧本模板，绑定 OpenMontage 上游 `animation` 管线与 MyMontage 下游自定义工具链。

## 1. 锁定配置与入口路径

| 项目 | 锁定值 |
| --- | --- |
| 上游管线 | `pipeline_defs/animation.yaml`（`image_animation` 图片动画模式） |
| 音频架构 | `narrator_plus_characters`（旁白 + 多角色对话） |
| 合成运行时 | `render_runtime: hyperframes`（`composition_mode: atelier`，`renderer_family: animation-first`） |
| 画面规格 | `1080x1920`（9:16 竖屏），`30fps` |
| 默认风格 YAML | `styles/anime-ghibli.yaml`（Anime Ghibli） |
| 剧集模板 JSON | `custom/playbooks/english-short-drama/template.json` |
| 工作区生成器 | `custom/playbooks/english-short-drama/build_workspace.py` |
| 发布版压缩入口 | `custom/playbooks/english-short-drama/compress_publish.py` |
| 单集产出目录 | `projects/<project-id>/`（gitignored，遵循 `AGENT_GUIDE.md` 工作区契约） |

用单集规格中的 `style_path` 构建 HyperFrames 工作区。新集使用 Anime Ghibli。

```bash
.venv/bin/python custom/playbooks/english-short-drama/build_workspace.py \
  --spec "projects/${PROJECT_ID}/artifacts/episode_spec.json" \
  --workspace "projects/${PROJECT_ID}/hyperframes" \
  --emit-edit-decisions "projects/${PROJECT_ID}/artifacts/edit_decisions.json"
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
- **0:00–0:02 双语封面标题卡（Cover Card，必选）**：停留 **2.0 秒**，在安全区内呈现英文片名（`Noto Serif JP`，`92px`）、中文片名（`62px`，汉字使用 `Noto Sans SC` 字形回退）和 `CEFR A2` 角标。BGM 同步淡入，随后切入剧情；正片画面上方**不再叠加**额外短剧标题条。
- **0:02–0:08 冷开场钩子（Cold Open）**：直接抛出反常画面或悬念一句（不放冗长片头 Logo）。
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
| **9:16 分镜关键帧** | `nano-banana-2.1` | `--episode-spec <单集规格> --scene-id <镜头 ID> [--ref <场景锚点图>]` | 用于批量产出 12~20 张竖屏分镜图，人物脸部统一构图在画面上 55% 区域，为下方双语字幕留白 |
| **快速构图草图（可选）** | `nano-banana-2-lite` | `--aspect 9:16` | 仅在构图拿不准时做低成本快速试拍，不用于终稿镜头 |

角色沿用剧本中的外貌和服装设定，人物和场景统一采用手绘水彩质感。用柔和漫射光、森林绿和暖金保持跨镜头呼应。都市镜头可以用盆栽、雨水或天空体现自然元素，保留前景、中景和背景的层次。

角色三视图使用单集 `style_path` 中的 `asset_generation.image_prompt_prefix` 和 `image_negative_prompt`，在中性背景上保留水彩笔触。分镜入口会自动读取同一份风格配置并加入这两项，`storyboard_prompt` 填本镜头的构图、动作和布景。双语字幕在合成阶段叠加。

#### 角色、动物、道具和场景的一致性

在分镜设计前，按模板的 `continuity` 登记本集重复出现、需要辨认或随剧情改变状态的主体。以唯一 ID 区分人物、动物、道具和场景。旁白和无需辨认的背景群众可不建立角色参考。根据实际故事决定哪些道具和地点需要登记。

- `continuity.subjects` 中的 `kind` 使用 `person` 或 `animal`，对应现有 `characters` 中的同一 ID。每个主体填写 `stable_features`，分别记录已批准参考图的 `reference_path` 和 `reference_media_id`。现有角色字段 `turnaround_sheet` 和 `turnaround_media_id` 仍供 Flow 入口使用，应与该主体的参考记录一致。
- `continuity.props` 和 `continuity.locations` 分别记录道具与地点的 ID、`stable_features`、`reference_path` 和 `reference_media_id`。参考图建立前路径和媒体 ID 可以为空；一旦生成并确认就登记实际值。
- `continuity.shots` 以 `scene_id` 对应 `scenes[].id`，分别列出 `subjects`、`props`、`locations`、本镜头的 `states` 与 `spatial_notes`。动物属于 `subjects`，作为角色引用时同样写入 `scenes[].character_refs`。配音用的旁白 ID 无需加入画面主体。

1. **独立设定图。** 每个人物、每只需要辨认的动物分别生成参考图；每件关键道具单独生成参考图；每个需要维持空间关系的地点建立场景参考图。人物图不要与动物、独立道具或其他人物共用一张主体设定图。手持道具的关系可另用互动参考图说明。
2. **验收角色图。** 人物的正面、四分之三侧面和侧面应为同一人物，头、躯干、手、腿、脚完整，轮廓无遮挡，不与其他主体或道具融合。动物参考图记录品种或外形、年龄感、毛色与斑纹、四肢和尾巴，并给出与成人或常见物体的相对体型。服装和外观以剧本与已批准的设计为准。
3. **验收道具与场景图。** 道具记录外形、尺寸、材质、颜色、内容物和可变状态；道具参考应能辨认完整轮廓。场景记录地点布局、固定建筑或家具、出入口、地标、主要光线及观察方向。补充不同视角时保持同一空间关系。
4. **建立镜头状态。** 每个镜头在 `continuity.shots` 中列出本镜头涉及的实体 ID 和状态，包括受伤部位的左右与包扎范围、衣物穿脱、物品持有者、动物相对体型和场景视角。状态随剧情事件改变；相邻镜头继承此前状态，直至剧情明确改变。纯空镜和无需某类实体的镜头可以使用空列表。
5. **生成并检查。** 生成前按实体 ID 选择对应的独立参考图，说明每张参考图代表的主体和本镜头状态。逐张对照参考图及相邻镜头检查人物身份、肢体完整性、左右手、动物体型、道具形状和归属、场景方位。将问题、处理和复查结果写入素材阶段审核记录；发现影响叙事的一致性错误时修正后再进入合成。

`continuity` 是制作记录。当前 `flow_image --episode-spec --scene-id` 入口自动绑定的是 `scenes[].character_refs` 中的角色参考；道具、场景和状态需由制作人员依照 `continuity` 记录写入镜头提示词并通过 `--ref` 附加参考图。工具尚不自动读取或验证这些新增字段，也无法仅靠结构校验确认图像内容。旧单集可以先保留原有引用，补录和检查后再替换混合设定图。

#### Flow 跨会话续做

1. **分镜阶段建立单集规格。** 在原项目的 `artifacts/episode_spec.json` 保留角色 `characters[].id`、每个镜头的 `scenes[].storyboard_prompt` 和按图排列的 `scenes[].character_refs`。引用使用实际出镜角色的 ID，纯空镜填 `[]`。选定 Flow 项目后记录 `flow_project_id` 和实际使用的 `flow_profile`。
2. **小样阶段登记参考图。** 三视图使用同一个 Flow 项目和 profile。保存图像到本集 `assets/images/`，把相对项目路径 `turnaround_sheet` 和 Flow 返回的 `turnaround_media_id` 写入对应角色。用这套参考图生成分镜小样。小样获批后沿用同一组参考图补全其余镜头。每张完成的分镜在资产检查点中登记镜头 ID、输出路径、参考图和 Flow job ID，便于下一会话跳过已完成的镜头并查回未交付的任务。
3. **续做时加载原项目。** 依照上游 checkpoint protocol 读取阶段和 `metadata.partial_progress`，再用下列入口检查每个待做镜头。工具固定该集的 Flow 项目及 profile，按镜头顺序附上角色参考图，并在提示词里对应角色名。角色优先使用已登记的 Flow media ID，只有本地三视图时使用原图文件。缺少项目、角色或有效参考图时，工具在提交前报错。旧项目可从同目录的 `asset_manifest.json` 读取已登记的项目 ID 及三视图资产 ID。旧镜头如未存提示词，可用 `--prompt` 传入本镜头的描述。

```bash
.venv/bin/python -m custom.plugins.studiowright image \
  --episode-spec "projects/${PROJECT_ID}/artifacts/episode_spec.json" \
  --scene-id scene_04 --plan
```

核对 `--project`、`--profile` 和每项 `--ref` 后，在已批准的生产阶段移除 `--plan` 提交。默认输出到 `assets/images/<scene-id>/`。生成后把成图与三视图并排核对脸型、发型、服装、配饰及多人镜头的身份归属。发生偏移时从相同参考图修正该镜头。Flow 中原 media ID 无法再附加时，确认本地原图与已批准的三视图一致后清空该 media ID，再用本地原图重试。

若某个镜头已批准单独的造型参考图，在规格中为该造型登记独立 ID，并在该镜头的 `character_refs` 中引用。旧集重做前，先对照资产检查点中的实际参考图，补录获批的镜头专用参考图。

### B. 图片动画与镜头表演

- 所有剧情镜头使用分镜图，`scenes[].kind` 填写 `still`，`src` 指向图片，`motion` 选择 `push-in`、`pull-out`、`pan-left`、`pan-right` 或 `none`。
- 利用不同景别、反应镜头、推拉和平移表现剧情。穿脱衣物等动作可拆成前后两个图片镜头，并在时间线上依次排列。
- 如需一场戏的多个姿态或表情，为每张图片建立独立镜头并分配起止时间。生成器当前每个镜头读取一张图片；音频和字幕按原时间线对齐。
- 使用已确认的角色参考图生成全部分镜，重点核对面部特征、身高比例、服装和道具在相邻镜头中的一致性。

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
   - 第一行：英文逐词卡拉 OK 高亮（当前词用暖金 `#FFB347` + 1.06x 柔和弹出，已读词用暖白 `#F5F0E8`，未读词半透）。
   - 第二行：同步中文译文（暖白 `#F5F0E8`，汉字使用 `Noto Sans SC` 字形回退）。

---

## 5. 阶段审批清单（遵循 `pipeline_defs/animation.yaml`）

1. `research`：收集选材原型与 A2 表达语料 → `research_brief.json`
2. `proposal` **[人工审批]**：3 个反转故事备选、确认 `render_runtime=hyperframes` / `composition_mode=atelier`、工具与额度预估 → `proposal_packet.json` + `decision_log.json`
3. `script` **[人工审批]**：完整英汉双语台词、**2.0 秒英汉大字封面定义**（`cover.duration_seconds: 2.0`、`title_en`、`title_zh`）、角色音色分配、停顿提示与 A2 重点表达 → `script.json`
4. `scene_plan` **[人工审批]**：镜头表（标注图片、景别、运镜、所需参考图），登记实体、每镜头状态与空间关系 → `scene_plan.json` + `episode_spec.json` 初稿
5. `assets` **[人工审批]**：先小样（1 张角色三视图 + 1 张分镜 + 2 句配音）→ 获批后分别制作所需主体、道具和场景参考，批量出图/出配乐/逐词对齐，逐镜记录一致性复核结果 → `asset_manifest.json` + 更新后的 `episode_spec.json`
6. `edit`：调用 `build_workspace.py` 生成 `hyperframes/index.html` 与 `edit_decisions.json`
7. `compose`：执行 `npx hyperframes lint`、`validate`、`inspect`、`snapshot` 抽帧目检 → 通过 `video_compose` / `hyperframes_compose(operation="render_existing")` 渲染 `renders/final.mp4`，做音画自检并完成[发布版压缩](#发布版压缩) → `render_report.json` + `final_review.json`
8. `publish` **[人工审批]**：将 `render_report.metadata.publish_video.path` 写入两个平台的 `publish_log.entries[].export_path`，同时输出封面、标题和 A2 核心词汇笔记。

### 发布版压缩

母版渲染并通过音画自检后，在完成 `compose` 检查点前执行下面的命令。

```bash
.venv/bin/python custom/playbooks/english-short-drama/compress_publish.py \
  --project-dir "projects/${PROJECT_ID}"
```

脚本调用 OpenMontage 的 `video_compose(operation="encode")`。用 H.264、CRF 23、`medium` 和 AAC 输出 MP4，并用 `ffprobe` 检查音轨、分辨率、帧率和时长。压缩后文件更小时，输出 `renders/final-publish.mp4` 并保留 `renders/final.mp4` 母版。若重编码后的文件没有变小，脚本选择母版发布。

把命令返回的 `publish` 写入 `render_report.metadata.publish_video`，把 `encoding` 和 `saved_bytes` 写入 `render_report.metadata.publish_compression`。压缩版单独加入 `render_report.outputs`，并抽帧检查英文逐词高亮、中文字幕及画面细节。报告记录实际发布文件，续做时从报告读取该路径。
