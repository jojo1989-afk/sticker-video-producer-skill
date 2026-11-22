# 剪纸贴纸画面

## 逐镜策划

按一句口播或一个叙事动作切镜。一镜对应一个可以用动作表达的意思。例如「经验散了」可以做成纸盒一打开，档案标签依次飞散。为同一批画面保留一致的纸边、阴影方向和字体留白，同时用底色变化区分段落。推荐深蓝、森林绿、暗红、姜黄或奶油白纸面，每镜少量强调色。

把关键物件放在竖屏中央的安全区域，留出顶部章节标签和底部后加字幕的位置。使用粗网点黑白照片剪影作主体，搭配彩色卡纸、奶油白裁切边、纸纤维和柔和短投影。把物件的空间关系画清楚，给每件纸片安排明确落点。口播和章节字放在剪映独立文字轨。

给 ImageGen 的提示词写清画面尺寸、底色、物件清单、每件的方位、纸边和阴影。拿到原图后，从主要背景区域确定十六进制底色值，并制作首尾帧。

```bash
python3 "$SKILL/scripts/animate.py" prepare --still shots/01/still.png --color '#123652' --out shots/01/frames
python3 "$SKILL/scripts/animate.py" sheet --out stills.jpg shots/01/frames/last.png shots/02/frames/last.png
```

`prepare` 等比置入竖屏画布，保留原图。若接边明显或裁切构图有问题，先调整配色或重出竖版静帧。展示正好将用于生成的 `last.png`，并记录用户批准的是哪一版。

## 动作提示词

根据已通过的尾帧，把入场动作写成一镜内的短时间序列。每个动作交代物件、来自画面哪一边、进入后停在哪里。给最终构图保留半秒左右。固定正视相机和底色，纸片用滑入、弹入、轻旋转、卡位和分段速度形成定格感。让纸片保持剪影、尺度和层叠关系。把动作描述与实际输入顺序对应。

```text
Use image 1 as the opening frame and image 2 as the exact destination composition.
Keep the camera fixed over a midnight-blue sheet of paper. Hold the empty sheet for
the first 0.35 seconds. Slide the cream filing box in from below, then snap three
halftone index cards into its slots from left to right. The red paper clasp drops
in last. Use stepped stop-motion movement with short contact shadows. Hold the
assembled composition for the last half second. Create only the paper elements
visible in the final image. Keep the frame free of titles and captions.
```

## 验收单镜

`finish` 生成 `clip.mp4` 与 `qa.jpg`。首行按动作时间抽帧，次行依次为预期首帧、实际首帧、实际尾帧、预期尾帧。查看纸片有没有提前出现，底色和相机有没有变化，各件是否按可读顺序落位，尾帧有没有加物或改构图。通过的镜头才进入剪映。返工时优先收紧动作表述、减少物件数量或调整首尾帧，再在独立版本目录重做。
