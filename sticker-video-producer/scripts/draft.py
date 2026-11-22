"""Build a new editable Jianying draft from narration, SRT, and an ordered clip list."""

import argparse
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import time

from _common import SkillError, json_bytes, read_json, run_cli, source


CUE_TIME = r"\d{2,}:\d{2}:\d{2}[,.]\d{3}"
CUE_LINE = re.compile(rf"^({CUE_TIME})[ \t]+-->[ \t]+({CUE_TIME})(?:[ \t].*)?$")


def microseconds(stamp):
    hours, minutes, seconds, milliseconds = map(int, re.split(r"[:,.]", stamp))
    if minutes > 59 or seconds > 59:
        raise SkillError(f"SRT 时码非法（{stamp}）。")
    return (hours * 3600000 + minutes * 60000 + seconds * 1000 + milliseconds) * 1000


def read_cues(path, audio_duration):
    data = source(path).read_text(encoding="utf-8-sig").strip()
    cues = []
    previous_end = 0
    for number, block in enumerate(re.split(r"\n[ \t]*\n+", data), 1):
        lines = block.strip().splitlines()
        if lines and re.fullmatch(r"\d+", lines[0].strip()):
            lines = lines[1:]
        match = CUE_LINE.fullmatch(lines[0].strip()) if lines else None
        text = "\n".join(lines[1:]).strip()
        if not match or not text:
            raise SkillError(f"SRT 第 {number} 条缺少有效时码或文字。")
        start, end = (microseconds(stamp) for stamp in match.groups())
        if end > audio_duration + 1000:
            raise SkillError(f"SRT 第 {number} 条超出旁白尾部，请核对旁白与字幕版本。")
        end = min(end, audio_duration)
        if start < previous_end or end <= start:
            raise SkillError(f"SRT 第 {number} 条重叠或时长无效。")
        cues.append((start, end, text))
        previous_end = end
    return cues


def plan_cuts(path, cues, duration, j):
    items = read_json(path)
    if not isinstance(items, list) or not items:
        raise SkillError("镜头表应为包含 clip 和 through_cue 的非空数组。")
    directory = source(path).parent
    start = 0
    previous_cue = 0
    cuts = []
    for number, item in enumerate(items, 1):
        through = item["through_cue"]
        if type(through) is not int or not previous_cue < through <= len(cues):
            raise SkillError(f"镜头 {number} 的 through_cue 需要递增，且在字幕序号范围内。")
        material = j.VideoMaterial(str(source(directory / item["clip"])))
        if material.material_type != "video" or material.duration <= 0:
            raise SkillError(f"镜头 {number} 需要可读取的本地视频。")
        in_ms = item.get("in_ms", 0)
        out_ms = item.get("out_ms")
        if type(in_ms) is not int or (out_ms is not None and type(out_ms) is not int):
            raise SkillError(f"镜头 {number} 的 in_ms 和 out_ms 需为整数毫秒。")
        source_start = in_ms * 1000
        source_end = material.duration if out_ms is None else out_ms * 1000
        if not 0 <= source_start < source_end <= material.duration:
            raise SkillError(f"镜头 {number} 选取的源片范围超出素材时长。")
        end = cues[through][0] if through < len(cues) else duration
        if end <= start:
            raise SkillError(f"镜头 {number} 的目标时长必须大于零。")
        target = j.Timerange(start, end - start)
        selected = j.Timerange(source_start, source_end - source_start)
        # Let the library compute speed from the source and target, keeping the exact target range.
        cuts.append(j.VideoSegment(material, target, source_timerange=selected, volume=0.0))
        start = end
        previous_cue = through
    if previous_cue != len(cues):
        raise SkillError("最后一个 through_cue 应等于字幕总条数。")
    return cuts


def sync_metadata(folder, final, content):
    meta_path = folder / "draft_meta_info.json"
    meta = read_json(meta_path)
    now = time.time_ns() // 1000
    meta.update({
        "draft_id": content["id"],
        "draft_name": final.name,
        "draft_fold_path": final.as_posix(),
        "draft_root_path": final.parent.as_posix(),
        "tm_draft_create": now,
        "tm_draft_modified": now,
        "tm_duration": content["duration"],
    })
    meta_path.write_bytes(json_bytes(meta))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--srt", type=Path, required=True)
    parser.add_argument("--cuts", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    if (
        not args.name or args.name != args.name.strip() or args.name.endswith(".")
        or re.search(r'[\\/:*?"<>|\x00-\x1f]', args.name)
    ):
        raise SkillError("请提供剪映可用的新草稿名。")

    import pyJianYingDraft as j

    root = args.root.expanduser().resolve()
    final = root / args.name
    if final.exists() or final.is_symlink():
        raise SkillError(f"已有同名草稿 {final}，请为新版本取名。")
    audio = j.AudioMaterial(str(source(args.audio)))
    if audio.duration <= 0:
        raise SkillError("无法读取旁白时长。")
    cues = read_cues(args.srt, audio.duration)
    cuts = plan_cuts(args.cuts, cues, audio.duration, j)
    speeds = [round(cut.speed.speed, 3) for cut in cuts]
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".building-", dir=root) as scratch:
        script = j.DraftFolder(scratch).create_draft(args.name, 720, 1280, fps=24)
        video_track, audio_track, caption_track = script.append_tracks([
            j.TrackSpec(j.TrackType.video, "拼贴画面"),
            j.TrackSpec(j.TrackType.audio, "旁白"),
            j.TrackSpec(j.TrackType.text, "字幕"),
        ])
        for cut in cuts:
            script.add_segment(cut, video_track)
        script.add_segment(j.AudioSegment(audio, j.Timerange(0, audio.duration)), audio_track)
        style = j.TextStyle(size=9.0, bold=True, align=1, auto_wrapping=True, max_line_width=0.82)
        position = j.ClipSettings(transform_y=-0.72)
        border = j.TextBorder(width=28)
        for start, end, text in cues:
            segment = j.TextSegment(
                text, j.Timerange(start, end - start), style=style, clip_settings=position, border=border,
            )
            script.add_segment(segment, caption_track)
        script.save()

        folder = Path(scratch) / args.name
        path = folder / "draft_content.json"
        data = read_json(path)
        expected = {"video": len(cuts), "audio": 1, "text": len(cues)}
        actual = {track["type"]: len(track["segments"]) for track in data["tracks"]}
        if data["duration"] != audio.duration or actual != expected:
            raise SkillError("草稿三轨片段数或总时长核对失败。")
        data["name"] = args.name
        content = json_bytes(data)
        path.write_bytes(content)
        (folder / "draft_info.json").write_bytes(content)
        sync_metadata(folder, final, data)
        if final.exists() or final.is_symlink():
            raise SkillError(f"同时生成了同名草稿 {final}。")
        folder.rename(final)
    print(json.dumps({
        "draft": str(final), "duration_us": audio.duration, "tracks": actual,
        "video_speeds": speeds,
    }, ensure_ascii=False))


if __name__ == "__main__":
    run_cli(main)
