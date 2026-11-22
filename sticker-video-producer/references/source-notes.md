# 原视频功能核对

来源为小红书笔记《3 个 skill 做出一条贴纸风视频》。下表依据视频画面、原始字幕和笔记正文，时间为约数。[原视频](https://www.xiaohongshu.com/discovery/item/6a616863000000001c00ea9d)

| 视频位置 | 演示的操作 | 本 Skill 的入口 |
| --- | --- | --- |
| 00:00–00:12 | 把口播、视频、声音、字幕和剪辑串起来 | 同一项目按镜头编号保存所有交付物 |
| 00:12–00:26 | 一句口播提炼为视觉隐喻，依次确认构想和静帧，再做约五秒纸片组装 | `SKILL.md` 的两次确认、`animate.py prepare/sheet/generate/finish` |
| 笔记正文 | 拼贴 B-roll 使用 Gemini 生视频，作者也用即梦 Seedance 做了这条视频 | Gemini Omni Flash 默认，Seedance 方舟 API 或即梦官方网页 |
| 00:27–00:42 | 约 30 秒声音作参考，在本机复刻音色并生成旁白，贴近本人语速、停顿和语气 | `voice.py add-sample/speak`，语速和停顿以参考样本和口播标点为基础，逐句试听 |
| 00:42–00:59 | 素材导入、基础剪辑、字幕和旁白对齐到同一剪映时间轴，作者检查后微调与导出 | `draft.py` 写入三轨可编辑草稿，SRT 时码采用生成语音的实际长度 |

画面中可见大色块底纸、半调照片贴纸、局部彩色图形和纸边阴影。动画使用滑入、卡位和相机固定的画面，上方章节条和底部字幕另行叠加。

- 拼贴流程和默认短镜规格参考原项目。新实现改用当前 Gemini Omni 1.2 Flash，保留首尾帧插值方式。[gbro-collage-broll](https://github.com/pyang5166/gbro-collage-broll) · [Google Omni Flash 文档](https://ai.google.dev/gemini-api/docs/omni)
- 声音生成通过 Voicebox 本机 API，复用其 Qwen3-TTS 引擎和音色档案。[Voicebox](https://github.com/jamiepine/voicebox) · [API 文档](https://docs.voicebox.sh/api-reference/generation/generate_speech_generate_post)
- 笔记列出的剪映自动剪辑项目地址当前返回 404。三轨草稿以 pyJianYingDraft 独立实现。[笔记引用的剪映项目](https://github.com/luoluoluo22/jianying-editor-skill) · [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft)
