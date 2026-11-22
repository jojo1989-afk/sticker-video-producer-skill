"""Prepare sticker animation references, generate video, and inspect the returned clip."""

import argparse
from base64 import b64encode, b64decode
from hashlib import sha256
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import time
from urllib.parse import quote, urlsplit

from _common import RequestError, SkillError, atomic_write, download, ffmpeg, http_json, json_bytes, read_json, run_cli, source


WIDTH, HEIGHT, FPS, SECONDS = 720, 1280, 24, 5
GOOGLE = "https://generativelanguage.googleapis.com/v1beta"
ARK = "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"
MODELS = {"omni": "gemini-omni-1.2-flash", "seedance": "doubao-seedance-2-0-260128"}


def prepare(args):
    from PIL import Image, ImageColor, ImageOps

    if not re.fullmatch(r"#[0-9a-fA-F]{6}", args.color):
        raise SkillError("底色格式为 #RRGGBB。")
    background = ImageColor.getrgb(args.color)
    with Image.open(source(args.still)) as original:
        art = ImageOps.exif_transpose(original).convert("RGBA")
        art = ImageOps.contain(art, (WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    args.out.mkdir(parents=True, exist_ok=False)
    first = Image.new("RGB", (WIDTH, HEIGHT), background)
    first.save(args.out / "first.png")
    last = first.copy()
    last.paste(art, ((WIDTH - art.width) // 2, (HEIGHT - art.height) // 2), art)
    last.save(args.out / "last.png")
    print(args.out.resolve())


def contact_sheet(rows, out):
    from PIL import Image, ImageDraw, ImageOps

    tile_width, tile_height, gutter, label_height = 180, 320, 8, 24
    columns = max(len(row) for row in rows)
    row_height = tile_height + label_height + gutter
    canvas = Image.new("RGB", (gutter + columns * (tile_width + gutter), gutter + len(rows) * row_height), "#222222")
    draw = ImageDraw.Draw(canvas)
    for row_number, row in enumerate(rows):
        for column, (label, path) in enumerate(row):
            x = gutter + column * (tile_width + gutter)
            y = gutter + row_number * row_height
            draw.text((x + 4, y + 5), label, fill="white")
            with Image.open(source(path)) as picture:
                tile = ImageOps.contain(picture.convert("RGB"), (tile_width, tile_height))
            canvas.paste(tile, (x + (tile_width - tile.width) // 2, y + label_height))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, quality=90)


def sheet(args):
    cells = [(f"{number:02}", path) for number, path in enumerate(args.images, 1)]
    contact_sheet([cells[i:i + 5] for i in range(0, len(cells), 5)], args.out)
    print(args.out.resolve())


def ref_bytes(path):
    from PIL import Image

    path = source(path)
    with Image.open(path) as picture:
        if picture.format != "PNG" or picture.size != (WIDTH, HEIGHT):
            raise SkillError(f"{path.name} 应为 prepare 生成的 720×1280 PNG。")
    return path.read_bytes()


def credentials(provider):
    variable = {"omni": "GEMINI_API_KEY", "seedance": "ARK_API_KEY"}[provider]
    key = os.environ.get(variable, "").strip()
    if not key:
        raise SkillError(f"请在本机配置 {variable} 后再提交。")
    if provider == "omni":
        return {"x-goog-api-key": key}
    return {"Authorization": f"Bearer {key}"}


def google_output(response):
    if response.get("output_video"):
        return response["output_video"]
    for step in reversed(response.get("steps", [])):
        for part in step.get("content", []):
            if part.get("type") == "video":
                return part
    for part in response.get("outputs", []):
        if part.get("type") == "video":
            return part
    return {}


def record_omni_output(response, job, raw):
    video = google_output(response)
    if video.get("uri"):
        path = urlsplit(video["uri"]).path
        match = re.search(r"(?:^|/)files/([A-Za-z0-9_-]+)/?$", path)
        if not match:
            raise SkillError("Google 返回的文件 URI 无法解析，请按 interaction ID 到服务端检查。")
        job["file_id"] = match.group(1)
    elif video.get("data"):
        atomic_write(raw, b64decode(video["data"], validate=True))


def submit(provider, model, images, prompt, headers):
    text = {"type": "text", "text": prompt}
    encoded = [b64encode(image).decode("ascii") for image in images]
    if provider == "omni":
        payload = {
            "model": model,
            "input": [
                *({"type": "image", "mime_type": "image/png", "data": value} for value in encoded),
                text,
            ],
            "response_format": {
                "type": "video", "aspect_ratio": "9:16", "resolution": "720p",
                "duration": SECONDS, "delivery": "uri",
            },
        }
        return http_json(f"{GOOGLE}/interactions", payload=payload, headers=headers, timeout=300)

    frames = [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{value}"}, "role": role}
        for role, value in zip(("first_frame", "last_frame"), encoded)
    ]
    payload = {
        "model": model, "content": [text, *frames], "ratio": "adaptive",
        "resolution": "720p", "duration": SECONDS, "generate_audio": False,
    }
    return http_json(ARK, payload=payload, headers=headers)


def collect(provider, job, receipt, raw, headers, deadline):
    while time.monotonic() < deadline:
        if provider == "seedance":
            task_id = quote(job["id"], safe="")
            result = http_json(f"{ARK}/{task_id}", headers=headers)
            status = result.get("status")
            if status == "succeeded":
                url = result["content"]["video_url"]
                if urlsplit(url).scheme != "https":
                    raise SkillError("Seedance 未返回 HTTPS 视频地址。")
                download(url, raw, timeout=120)
                return
        elif job.get("file_id"):
            file_url = f"{GOOGLE}/files/{job['file_id']}"
            result = http_json(file_url, headers=headers)
            status = result.get("state")
            if status == "ACTIVE":
                download(f"{file_url}:download?alt=media", raw, headers=headers, timeout=120)
                return
        else:
            task_id = quote(job["id"].split("/")[-1], safe="")
            result = http_json(f"{GOOGLE}/interactions/{task_id}", headers=headers)
            status = result.get("status")
            record_omni_output(result, job, raw)
            if raw.is_file():
                return
            if job.get("file_id"):
                atomic_write(receipt, json_bytes(job))
                continue
            if status == "completed":
                raise SkillError(f"任务 {job['id']} 已完成，API 未返回视频。")
        if status in {"failed", "FAILED", "cancelled", "expired", "incomplete"}:
            detail = result.get("error") or {}
            code = detail.get("code") if isinstance(detail, dict) else None
            raise SkillError(f"视频任务 {job['id']} 状态 {status}（{code or '查看服务端任务详情'}）。")
        time.sleep(min(8, max(0, deadline - time.monotonic())))
    raise SkillError(f"任务 {job['id']} 等待超时。重复同一命令可继续查询。")


def generate(args):
    images = [ref_bytes(args.first), ref_bytes(args.last)]
    prompt = source(args.prompt).read_text(encoding="utf-8-sig").strip()
    if not prompt:
        raise SkillError("动画提示词为空。")
    provider = args.provider
    model = args.model or MODELS[provider]
    if args.timeout <= 0:
        raise SkillError("等待时间需要大于零。")
    fingerprint = {
        "provider": provider,
        "model": model,
        "images": [sha256(image).hexdigest() for image in images],
        "prompt": sha256(prompt.encode("utf-8")).hexdigest(),
    }
    args.job.mkdir(parents=True, exist_ok=True)
    receipt, raw = args.job / "job.json", args.job / "raw.mp4"

    if receipt.is_file():
        job = read_json(receipt)
        if job.get("input") != fingerprint:
            raise SkillError("本次输入与已有任务回执不符，改用新的镜头版本目录。")
        if raw.is_file():
            print(source(raw))
            return
        if not job.get("id"):
            raise SkillError("已有未确定结果的提交。先在服务端核对，再决定是否发起新任务。")
        headers = credentials(provider)
    else:
        if raw.exists():
            raise SkillError("任务目录已有 raw.mp4，请给新任务选择独立目录。")
        headers = credentials(provider)
        job = {"input": fingerprint}
        # Claim this job directory before any billable request.
        with receipt.open("xb") as handle:
            handle.write(json_bytes(job))
        try:
            result = submit(provider, model, images, prompt, headers)
        except RequestError as exc:
            if exc.status in {400, 401, 403, 404, 413, 415, 422, 429}:
                receipt.unlink()
            raise
        job["id"] = result["id"]
        atomic_write(receipt, json_bytes(job))
        if provider == "omni":
            record_omni_output(result, job, raw)
            atomic_write(receipt, json_bytes(job))
        print(f"视频任务 {job['id']}", file=sys.stderr, flush=True)

    if not raw.is_file():
        collect(provider, job, receipt, raw, headers, time.monotonic() + args.timeout)
    print(source(raw))


def finish(args):
    from PIL import Image
    from pymediainfo import MediaInfo

    raw = source(args.video)
    first, last = source(args.first), source(args.last)
    with Image.open(first) as opening:
        red, green, blue = opening.convert("RGB").getpixel((0, 0))
    pad_color = f"0x{red:02x}{green:02x}{blue:02x}"
    media = MediaInfo.parse(str(raw))
    if not media.video_tracks or not media.video_tracks[0].duration:
        raise SkillError("无法读取源视频时长。")
    speed = SECONDS * 1000 / float(media.video_tracks[0].duration)
    filters = (
        f"setpts={speed:.10f}*(PTS-STARTPTS),fps={FPS},"
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color={pad_color},"
        "setsar=1,tpad=stop_mode=clone:stop_duration=1"
    )
    args.out.mkdir(parents=True, exist_ok=True)
    if raw == (args.out / "clip.mp4").resolve():
        raise SkillError("输出位置与原视频重合，请换一个输出目录。")
    count = FPS * SECONDS
    with TemporaryDirectory(prefix=".finishing-", dir=args.out) as area:
        folder = Path(area)
        clip = folder / "clip.mp4"
        ffmpeg(
            "-i", raw, "-map", "0:v:0", "-vf", filters, "-an", "-sn",
            "-frames:v", count, "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", clip,
        )
        result = MediaInfo.parse(str(clip))
        video = result.video_tracks[0]
        dimensions = (video.width, video.height, int(video.frame_count), float(video.frame_rate))
        if dimensions != (WIDTH, HEIGHT, count, FPS) or result.audio_tracks:
            raise SkillError("规范化视频的尺寸、帧数、帧率或音轨检查失败。")
        sample_numbers = [0, FPS, FPS * 2, FPS * 3, FPS * 4, count - 1]
        selection = "+".join(f"eq(n,{number})" for number in sample_numbers)
        ffmpeg("-i", clip, "-vf", f"select='{selection}'", "-vsync", "vfr", folder / "%02d.png")
        sample_frames = sorted(folder.glob("*.png"))
        if len(sample_frames) != len(sample_numbers):
            raise SkillError("质检抽帧数量不足。")
        contact_sheet(
            [
                [(f"{second}s", path) for second, path in enumerate(sample_frames[:-1])],
                [("Reference start", first), ("Actual start", sample_frames[0]),
                 ("Actual end", sample_frames[-1]), ("Reference end", last)],
            ],
            folder / "qa.jpg",
        )
        clip.replace(args.out / "clip.mp4")
        (folder / "qa.jpg").replace(args.out / "qa.jpg")
    print(args.out.resolve() / "clip.mp4")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)
    frames = commands.add_parser("prepare", help="prepare a blank opening image and a fitted final image")
    frames.add_argument("--still", type=Path, required=True)
    frames.add_argument("--color", required=True)
    frames.add_argument("--out", type=Path, required=True)
    frames.set_defaults(action=prepare)
    grid = commands.add_parser("sheet", help="label a set of stills in their supplied order")
    grid.add_argument("--out", type=Path, required=True)
    grid.add_argument("images", nargs="+", type=Path)
    grid.set_defaults(action=sheet)
    animate = commands.add_parser("generate", help="submit a billable task or resume its recorded ID")
    animate.add_argument("--provider", choices=MODELS, default="omni")
    animate.add_argument("--model")
    animate.add_argument("--prompt", type=Path, required=True)
    animate.add_argument("--job", type=Path, required=True)
    animate.add_argument("--timeout", type=int, default=900)
    animate.set_defaults(action=generate)
    polish = commands.add_parser("finish", help="normalize a clip and build a QA contact sheet")
    polish.add_argument("--video", type=Path, required=True)
    polish.add_argument("--out", type=Path, required=True)
    polish.set_defaults(action=finish)
    for command in (animate, polish):
        command.add_argument("--first", type=Path, required=True)
        command.add_argument("--last", type=Path, required=True)
    args = parser.parse_args()
    args.action(args)


if __name__ == "__main__":
    run_cli(main)
