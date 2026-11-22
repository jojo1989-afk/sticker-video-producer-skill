import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class SkillError(Exception):
    pass


class RequestError(SkillError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def source(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise SkillError(f"找不到可读素材 {path}")
    return path


def read_json(path):
    return json.loads(source(path).read_text(encoding="utf-8-sig"))


def json_bytes(data):
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def atomic_write(path, data):
    path = Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def ffmpeg(*args):
    binary = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg")
    if not binary:
        raise SkillError("请安装 FFmpeg，或用 FFMPEG_BIN 指定可执行文件。")
    subprocess.run(
        [binary, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *map(str, args)],
        check=True,
    )


def open_http(url, *, data=None, headers=None, method=None, timeout=60, opener=None):
    request = Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        request.add_unredirected_header(key, value)
    try:
        connect = opener.open if opener else urlopen
        return connect(request, timeout=timeout)
    except HTTPError as exc:
        raise RequestError(f"接口返回 HTTP {exc.code}（{request.host}）", exc.code) from None
    except (URLError, TimeoutError):
        raise RequestError(f"接口连接失败或超时（{request.host}）") from None


def http_json(url, *, payload=None, headers=None, **options):
    if payload is not None:
        headers = {**(headers or {}), "Content-Type": "application/json"}
        options["data"] = json_bytes(payload)
    with open_http(url, headers=headers, **options) as response:
        return json.load(response)


def download(url, path, **options):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with open_http(url, **options) as response, temporary.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        source(temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def run_cli(command):
    try:
        command()
    except (SkillError, OSError, ValueError, KeyError, TypeError, ImportError, subprocess.CalledProcessError) as exc:
        print(f"执行失败，{exc}", file=sys.stderr)
        raise SystemExit(1) from None
