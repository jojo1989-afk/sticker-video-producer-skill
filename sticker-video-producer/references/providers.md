# 视频与配音接入

## 环境

在项目目录准备 Python 3.9+ 环境。下面示例中的 `$SKILL` 指本 Skill 的绝对路径，`python3` 指项目启用的 Python。静帧和视频质检需要 Pillow，视频规范化需要 FFmpeg 和 MediaInfo Python 库，三轨草稿使用固定版本的 pyJianYingDraft。仅跑本机 Voicebox 和拼接时用 Python 标准库与 FFmpeg 即可。

```bash
python3 -m pip install -r "$SKILL/scripts/requirements.txt"
```

确认 `ffmpeg` 在 PATH，或用 `FFMPEG_BIN` 指定现有的可执行文件。可先制作隐喻、整理现有素材和写分句 JSON，接入时再处理对应缺项。

## Gemini Omni Flash

默认模型为 `gemini-omni-1.2-flash`，以 Gemini Interactions API 把首图和尾图插值成视频。配置本机环境变量 `GEMINI_API_KEY`。生成消耗 Google 账户额度。在静帧确认时一并说明模型和待生成镜数。用户确认后执行通过的镜头。

```bash
python3 "$SKILL/scripts/animate.py" generate \
  --first shots/01/frames/first.png --last shots/01/frames/last.png \
  --prompt shots/01/motion.txt --job shots/01/omni-v01
python3 "$SKILL/scripts/animate.py" finish \
  --video shots/01/omni-v01/raw.mp4 \
  --first shots/01/frames/first.png --last shots/01/frames/last.png \
  --out shots/01/omni-v01
```

`generate` 的同一次任务使用同一个目录。拿到任务 ID 后，重复同一命令会接着查任务或复用已下载的原片。提交后断线且回执尚无 ID 时，先在服务方核对再新开任务。用户要换图、提示词或模型时使用新版本目录。[Gemini Omni Flash 首尾帧文档](https://ai.google.dev/gemini-api/docs/omni#first-and-last-frame-interpolation)

## 即梦 Seedance

- **使用即梦网页。** 用户指定即梦并有已登录会话时，用可用的浏览器操作工具打开即梦官方平台。选择视频生成、有首尾帧能力的 Seedance 模型和首尾帧模式，按顺序上传 `first.png`、`last.png`，填入 `motion.txt`，设置约 5 秒并核对竖屏预览。提交已确认镜头，记录任务并等结果，下载原片后执行 `finish`。使用用户已配置的官方即梦 CLI 时，先用本机帮助确认首尾帧命令、参数、当前模型和查询方式，保留同样的输入与输出。权限、登录和额度由当前账户提供。[即梦官方平台](https://jimeng.jianying.com/ai-tool/home)
- **使用火山方舟。** 已有方舟按量 API 的用户可设置 `ARK_API_KEY`，在上述 `generate` 命令加 `--provider seedance` 并使用独立的任务目录。默认 `doubao-seedance-2-0-260128`，也接受账户已开通且支持首尾帧的模型 ID 或端点 ID（`--model`）。脚本提交两个画面的指定角色，查询同一任务并下载成片。[方舟创建视频任务](https://docs.volcengine.com/docs/ark/create-video-generation-task-api) · [查询任务](https://docs.volcengine.com/docs/ark/get-video-generation-task-api)

已有生成视频时，把它作为 `finish --video` 输入即可。跨后端换方案由用户的选择决定，每个镜头保留一个可追溯的任务目录。

## 本机 Voicebox

启动 Voicebox 桌面应用，确认当前引擎和模型可用。桌面服务默认 `http://127.0.0.1:17493`，源码本机开发服务通常为 `http://127.0.0.1:8000`。用 `VOICEBOX_URL` 或子命令前的 `--url` 指定本机端口。首次使用的模型权重需要下载，之后本地推理。中文复刻默认 Qwen3-TTS。选用约 30 秒的单人干声，转写和声音逐字相符。若启用了档案效果或其他引擎，以试听为准。

```bash
python3 "$SKILL/scripts/voice.py" profiles
python3 "$SKILL/scripts/voice.py" transcribe --sample reference.m4a --language zh > reference.txt
python3 "$SKILL/scripts/voice.py" add-sample --name '我的旁白' \
  --sample reference.m4a --transcript reference.txt --language zh
```

已有音色直接取其 ID。已有可靠转写时直接使用。转写出来后核对人名、数字、专有词与录音；样本是否清晰会影响复刻质量。在语音里保留对停顿有作用的标点，口语化改稿须与用户定稿一致。可用 `--instruct` 传当前引擎支持的语气提示，并试听效果。

`scenes.json` 是有序数组，`id` 在同一项目唯一。每项对应一段实际配音和一条字幕。需要字幕分行时提供 `caption`，旁白仍读 `text`。单句可提供 `seed` 或 `instruct` 做修改。

```json
[
  {"id": "01", "text": "有些想法，一眨眼就散了。"},
  {"id": "02", "text": "写下来，才找得到下一步。", "caption": "写下来，\n才找得到下一步。"}
]
```

```bash
python3 "$SKILL/scripts/voice.py" speak --script scenes.json \
  --profile "$VOICEBOX_PROFILE_ID" --language zh --out voice
```

脚本会保留每句生成任务与对应 WAV，重跑时复用未变化的句子。试听后可改需要重做的那一句，或给它新的种子。最终输出 `narration.wav`（48 kHz PCM）和 `subtitles.srt`。默认延续各句语音本身的停顿，额外句间留白可用 `--gap-ms` 指定。SRT 时间来自实际送进旁白轨的采样数。

如果从 Voicebox 图形界面逐句导出，将每句保存成 `takes/<id>.wav`，运行 `voice.py join --script scenes.json --takes takes --out voice`，得到相同的时间码交付。遇到首轮模型加载可在 Voicebox 查看进度，拿到任务 ID 后沿用同一生成目录。

[Voicebox 官方安装](https://docs.voicebox.sh/overview/installation) · [生成语音 API](https://docs.voicebox.sh/api-reference/generation/generate_speech_generate_post) · [添加音色样本 API](https://docs.voicebox.sh/api-reference/profiles/add_profile_sample_profiles__profile_id__samples_post)
