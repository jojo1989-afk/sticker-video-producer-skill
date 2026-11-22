"""Clone and generate narration through local Voicebox, then derive SRT from WAV lengths."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import time
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener
from uuid import uuid4
import wave

from _common import RequestError, SkillError, atomic_write, download, ffmpeg, http_json, json_bytes, open_http, read_json, run_cli, source


RATE = 48000
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac", ".webm", ".opus"}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Voicebox:
    def __init__(self, url):
        parts = urlsplit(url)
        if (
            parts.scheme != "http" or parts.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parts.path not in {"", "/"} or parts.query or parts.fragment
            or parts.username or parts.password
        ):
            raise SkillError("Voicebox 地址应为本机 HTTP 服务，例如 http://127.0.0.1:17493。")
        self.base = url.rstrip("/")
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def json(self, route, payload=None, timeout=120):
        return http_json(self.base + route, payload=payload, opener=self.opener, timeout=timeout)

    def upload(self, route, audio, fields):
        audio = source(audio)
        extension = audio.suffix.lower()
        if extension not in AUDIO_EXTENSIONS or audio.stat().st_size > 50 * 1024 * 1024:
            raise SkillError("请使用小于 50 MB 的 WAV、M4A、MP3 等 Voicebox 支持的音频。")
        boundary = uuid4().hex
        body = bytearray()
        for name, value in fields.items():
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(value.encode("utf-8") + b"\r\n")
        body.extend(
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample{extension}"'
            "\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
        )
        body.extend(audio.read_bytes())
        body.extend(f"\r\n--{boundary}--\r\n".encode())
        with open_http(
            self.base + route, data=bytes(body), opener=self.opener, timeout=180,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        ) as response:
            return json.load(response)

    def wait(self, task_id, timeout):
        route = f"/generate/{quote(task_id, safe='')}/status"
        with open_http(self.base + route, opener=self.opener, timeout=30) as response:
            deadline = time.monotonic() + timeout
            for line in response:
                if time.monotonic() >= deadline:
                    break
                if line.startswith(b"data:"):
                    result = json.loads(line[5:])
                    state = result.get("status")
                    if state == "completed":
                        return
                    if state in {"failed", "not_found", "cancelled"}:
                        raise SkillError(f"Voicebox 任务 {task_id} 状态 {state}，请在生成历史核对。")
        raise SkillError(f"Voicebox 任务 {task_id} 等待中断，可重复同一命令继续查询。")

    def fetch_wav(self, task_id, path):
        url = self.base + "/audio/" + quote(task_id, safe="")
        download(url, path, opener=self.opener, timeout=60)


def profiles(args):
    result = Voicebox(args.url).json("/profiles")
    fields = ("id", "name", "language", "voice_type", "default_engine")
    print(json.dumps([{field: item.get(field) for field in fields} for item in result], ensure_ascii=False, indent=2))


def transcribe(args):
    result = Voicebox(args.url).upload("/transcribe", args.sample, {"language": args.language})
    if not result.get("text"):
        raise SkillError("转写还未就绪。请在 Voicebox 查看 Whisper 下载、模型状态或音频输入。")
    print(result["text"].strip())


def add_sample(args):
    api = Voicebox(args.url)
    sample = source(args.sample)
    text = source(args.transcript).read_text(encoding="utf-8-sig").strip()
    if not text:
        raise SkillError("声音样本的转写为空。")
    if sample.suffix.lower() not in AUDIO_EXTENSIONS or sample.stat().st_size > 50 * 1024 * 1024:
        raise SkillError("请为音色档案提供 Voicebox 支持且小于 50 MB 的音频。")
    profile = args.profile
    if args.name:
        names = {item["name"] for item in api.json("/profiles")}
        if args.name in names:
            raise SkillError("已有同名声音档案。使用 profiles 核对后通过 --profile 指定档案。")
        result = api.json("/profiles", {
            "name": args.name, "language": args.language, "voice_type": "cloned", "default_engine": "qwen",
        })
        profile = result["id"]
        print(f"已创建档案 {profile}", file=sys.stderr, flush=True)
    result = api.upload(f"/profiles/{quote(profile, safe='')}/samples", sample, {"reference_text": text})
    print(json.dumps({"profile_id": profile, "sample_id": result["id"]}, ensure_ascii=False))


def read_script(path):
    entries = read_json(path)
    if not isinstance(entries, list) or not entries:
        raise SkillError("口播 JSON 应为包含 id 和 text 的非空数组。")
    used_ids = set()
    for item in entries:
        line_id, text = item["id"], item["text"]
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}", line_id) or line_id in used_ids:
            raise SkillError(f"句子编号需要唯一，且由英文字母、数字、连字符或下划线组成（{line_id}）。")
        if not text.strip() or not 1 <= len(text) <= 5000:
            raise SkillError(f"口播 {line_id} 应为 1–5000 字符。")
        caption = item.get("caption", text).strip()
        if not caption or re.search(r"\n[ \t]*\n", caption):
            raise SkillError(f"字幕 {line_id} 需要有效文字，可用单个换行排版。")
        item["caption"] = caption
        used_ids.add(line_id)
    return entries


def make_take(api, item, args, folder):
    payload = {
        "profile_id": args.profile, "text": item["text"], "language": args.language,
        "engine": args.engine, "model_size": args.model_size, "personality": False,
    }
    for key in ("seed", "instruct"):
        value = item.get(key, getattr(args, key))
        if value is not None:
            payload[key] = value
    digest = sha256(json_bytes(payload)).hexdigest()[:16]
    stem = f"{item['id']}-{digest}"
    wav_path, receipt = folder / f"{stem}.wav", folder / f"{stem}.json"
    if wav_path.is_file():
        return source(wav_path)

    if receipt.is_file():
        task = read_json(receipt)
        if not task.get("id"):
            raise SkillError(f"句子 {item['id']} 有一次未确认的提交，请先在 Voicebox 历史中核对。")
    else:
        with receipt.open("xb") as handle:
            handle.write(b"{}")
        try:
            result = api.json("/generate", payload, timeout=args.timeout)
        except RequestError as exc:
            if exc.status in {400, 401, 403, 404, 413, 415, 422, 429}:
                receipt.unlink()
            raise
        task = {"id": result["id"], "status": result.get("status", "completed")}
        atomic_write(receipt, json_bytes(task))
        print(f"Voicebox {item['id']} · {task['id']}", file=sys.stderr, flush=True)

    if task["status"] != "completed":
        api.wait(task["id"], args.timeout)
        task["status"] = "completed"
        atomic_write(receipt, json_bytes(task))
    api.fetch_wav(task["id"], wav_path)
    return wav_path


def srt_stamp(milliseconds):
    seconds, rest = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02},{rest:03}"


def write_narration(entries, files, out, gap_ms):
    if not 0 <= gap_ms <= 5000:
        raise SkillError("句间停顿应在 0–5000 毫秒之间。")
    out.mkdir(parents=True, exist_ok=True)
    draft_wav, draft_srt = out / "narration.wav", out / "subtitles.srt"
    if draft_wav.resolve() in files:
        raise SkillError("请把合成输出和输入的分句音频放在不同位置。")
    with TemporaryDirectory(prefix=".voice-", dir=out) as area:
        working = Path(area)
        full = working / "narration.wav"
        part = working / "part.wav"
        stamps = []
        elapsed = 0
        gap = gap_ms * RATE // 1000
        with wave.open(str(full), "wb") as narration:
            narration.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            for number, (item, path) in enumerate(zip(entries, files), 1):
                ffmpeg("-i", path, "-map", "0:a:0", "-vn", "-c:a", "pcm_s16le", "-ac", 1, "-ar", RATE, part)
                start_ms = elapsed * 1000 // RATE
                with wave.open(str(part), "rb") as audio:
                    elapsed += audio.getnframes()
                    while True:
                        samples = audio.readframes(RATE)
                        if not samples:
                            break
                        narration.writeframesraw(samples)
                end_ms = elapsed * 1000 // RATE
                if end_ms <= start_ms:
                    raise SkillError(f"句子 {item['id']} 的音频过短。")
                stamps.append(f"{number}\n{srt_stamp(start_ms)} --> {srt_stamp(end_ms)}\n{item['caption']}")
                if gap and number < len(entries):
                    narration.writeframesraw(b"\x00" * (gap * 2))
                    elapsed += gap
        atomic_write(draft_srt, ("\n\n".join(stamps) + "\n").encode("utf-8"))
        full.replace(draft_wav)
    print(json.dumps({
        "narration": str(draft_wav.resolve()), "srt": str(draft_srt.resolve()),
        "duration_ms": elapsed * 1000 // RATE, "cue_count": len(stamps),
    }, ensure_ascii=False))


def speak(args):
    entries = read_script(args.script)
    if not 0 <= args.gap_ms <= 5000 or args.timeout <= 0:
        raise SkillError("等待时间需要大于零，句间停顿应在 0–5000 毫秒之间。")
    api = Voicebox(args.url)
    folder = args.out / "takes"
    folder.mkdir(parents=True, exist_ok=True)
    files = [make_take(api, item, args, folder) for item in entries]
    write_narration(entries, files, args.out, args.gap_ms)


def join(args):
    entries = read_script(args.script)
    files = [source(args.takes / f"{item['id']}.wav") for item in entries]
    write_narration(entries, files, args.out, args.gap_ms)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("VOICEBOX_URL", "http://127.0.0.1:17493"))
    commands = parser.add_subparsers(required=True)
    commands.add_parser("profiles").set_defaults(action=profiles)

    transcription = commands.add_parser("transcribe")
    sample = commands.add_parser("add-sample")
    existing = sample.add_mutually_exclusive_group(required=True)
    existing.add_argument("--name")
    existing.add_argument("--profile")
    sample.add_argument("--transcript", required=True, type=Path)
    for command in (sample, transcription):
        command.add_argument("--sample", required=True, type=Path)
        command.add_argument("--language", default="zh")
    sample.set_defaults(action=add_sample)
    transcription.set_defaults(action=transcribe)

    narration = commands.add_parser("speak")
    narration.add_argument("--profile", required=True)
    narration.add_argument("--language", default="zh")
    narration.add_argument("--engine", default="qwen")
    narration.add_argument("--model-size", default="1.7B")
    narration.add_argument("--instruct")
    narration.add_argument("--seed", type=int)
    narration.add_argument("--timeout", type=int, default=900)
    narration.set_defaults(action=speak)
    combine = commands.add_parser("join")
    combine.add_argument("--takes", type=Path, required=True)
    combine.set_defaults(action=join)
    for command in (narration, combine):
        command.add_argument("--script", required=True, type=Path)
        command.add_argument("--out", required=True, type=Path)
        command.add_argument("--gap-ms", type=int, default=0)
    args = parser.parse_args()
    args.action(args)


if __name__ == "__main__":
    run_cli(main)
