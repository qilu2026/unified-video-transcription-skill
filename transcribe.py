"""Turn a supported public-video link into a local HTML transcript."""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

from report import make_result, render_html, safe_url


DEFAULT_CONFIG = Path.home() / ".video-transcription" / "config.env"

# 用 MiMo 的专用语音识别模型，不要换回通用对话模型 mimo-v2.5：通用模型会先"思考"，
# 转写有时只出现在 reasoning_content 里，偶尔还会回一句"请提供音频"；同一份音频
# 实测三次结果都不一样。ASR 模型不需要提示词、不输出推理，同一份音频三次结果一致。
# 文档：https://mimo.mi.com/docs/zh-CN/api/audio/Speech-Recognition
MIMO_URL = "https://api.xiaomimimo.com/v1/chat/completions"
MIMO_MODEL = "mimo-v2.5-asr"

# 单次请求的音频数据上限是 10 MB（按 base64 后计算，超出返回 400）；单次输出上限
# 约 2K token，语速密集时大约 6 分钟就会顶到。所以长音频切成 3 分钟一段分别转写。
CHUNK_SECONDS = 180
MAX_AUDIO_BYTES = 120 * 1024 * 1024  # 128 kbps 下约 2 小时
RETRYABLE_STATUS = {500, 502, 503, 504}


def load_config(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"找不到本地配置文件：{path}")
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name:
            os.environ[name] = value


def ffmpeg_path() -> str:
    configured = os.getenv("FFMPEG_PATH", "").strip() or shutil.which("ffmpeg")
    if configured:
        return configured
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def normalize_transcript(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    return re.sub(r"\n{3,}", "\n\n", normalized).strip()


def extract_audio(video_url: str, output: Path, headers: dict[str, str]) -> None:
    if not safe_url(video_url):
        raise ValueError("解析结果不是有效的 HTTP/HTTPS 视频地址")
    request_headers = "".join(f"{key}: {value}\r\n" for key, value in headers.items())
    command = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y"]
    if request_headers:
        command.extend(("-headers", request_headers))
    command.extend((
        "-i", video_url, "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "libmp3lame", "-b:a", "128k", str(output),
    ))
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=480)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("视频下载和音频提取超过 480 秒") from exc
    if result.returncode != 0:
        raise RuntimeError(f"音频提取失败：{result.stderr[-300:]}")
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError("视频中没有可用音频")
    if output.stat().st_size > MAX_AUDIO_BYTES:
        raise RuntimeError("音频超过约 2 小时，暂不支持这么长的视频")


def split_audio(audio_path: Path, directory: Path, seconds: int = CHUNK_SECONDS) -> list[Path]:
    """按固定时长切段（不重新编码），返回按顺序排列的分段文件。"""
    pattern = directory / "chunk_%03d.mp3"
    command = [
        ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(audio_path),
        "-f", "segment", "-segment_time", str(seconds), "-c", "copy", str(pattern),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"音频分段失败：{result.stderr[-300:]}")
    chunks = sorted(path for path in directory.glob("chunk_*.mp3") if path.stat().st_size > 0)
    if not chunks:
        raise RuntimeError("音频分段失败：没有生成分段文件")
    return chunks


def join_chunks(texts: list[str]) -> str:
    """拼接分段转写。中文直接相连；两边都是英文单词时补一个空格。"""
    joined = ""
    for text in texts:
        text = text.strip()
        if not text:
            continue
        if joined and re.match(r"[A-Za-z0-9]", text) and re.search(r"[A-Za-z0-9.,!?]$", joined):
            joined += " "
        joined += text
    return joined


async def transcribe_mimo(audio_path: Path, attempts: int = 3) -> str:
    """转写一段音频（不超过 CHUNK_SECONDS）。返回空字符串表示这段确实没有人声。

    重试只针对临时性问题：网络超时、5xx、返回空内容。密钥、额度、权限、限流这类
    重试也不会好的错误直接报出。
    """
    key = os.getenv("MIMO_API_KEY", "").strip()
    if not key:
        raise RuntimeError("本机尚未配置 MiMo API Key")
    data = base64.b64encode(audio_path.read_bytes()).decode()
    payload = {
        "model": MIMO_MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "input_audio", "input_audio": {"data": f"data:audio/mpeg;base64,{data}"}},
        ]}],
        "asr_options": {"language": "auto"},
    }
    hints = {
        401: "MiMo 密钥无效或已失效，请更新 MIMO_API_KEY",
        402: "MiMo 额度不足，请到开放平台查看余额",
        403: "MiMo 拒绝访问，请检查账号和模型使用权限",
        429: "MiMo 请求受限，请查看账号额度或稍后再试",
    }
    last_problem = ""
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            print(f"MiMo {last_problem}，正在重试（{attempt}/{attempts}）...", file=sys.stderr)
            await asyncio.sleep(2 * (attempt - 1))
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=15)) as client:
            try:
                response = await client.post(MIMO_URL, headers={"api-key": key}, json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_problem = f"连接出错（{type(exc).__name__}）"
                continue
        if response.status_code in RETRYABLE_STATUS:
            last_problem = f"服务暂时出错（HTTP {response.status_code}）"
            continue
        if response.is_error:
            raise RuntimeError(hints.get(response.status_code, f"MiMo 服务返回 HTTP {response.status_code}，本次未生成文稿"))
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") not in (None, "stop"):
            raise RuntimeError("MiMo 未完整结束转写，本次不保存可能被截断的文稿")
        text = normalize_transcript(str(choice["message"].get("content") or ""))
        if text:
            return text
        last_problem = "返回了空内容"
    if last_problem == "返回了空内容":
        return ""
    raise RuntimeError(f"MiMo {last_problem}，重试 {attempts} 次仍失败，本次未生成文稿")


async def transcribe_audio(audio_path: Path) -> str:
    """整段音频切成 CHUNK_SECONDS 一段逐段转写，再拼接。"""
    with tempfile.TemporaryDirectory(prefix="video-transcription-chunks-") as directory:
        chunks = await asyncio.to_thread(split_audio, audio_path, Path(directory))
        texts = []
        for index, chunk in enumerate(chunks, start=1):
            if len(chunks) > 1:
                print(f"正在转写第 {index}/{len(chunks)} 段...", file=sys.stderr)
            texts.append(await transcribe_mimo(chunk))
    transcript = join_chunks(texts)
    if not transcript:
        raise RuntimeError("视频里没有识别到人声，本次未生成文稿")
    return transcript


async def process(text: str) -> dict:
    # Import after config.env has populated the local parser settings.
    from app.http_client import PlatformClient
    from app.parsers import parse_link
    from app.security import extract_platform_url

    source_url = extract_platform_url(text)
    print("正在解析视频链接...", file=sys.stderr)
    client = PlatformClient()
    try:
        parsed = await parse_link(text, client)
    finally:
        await client.close()
    video_url = next((url for kind, url in parsed.media_urls if kind == "video"), "")
    if not video_url:
        raise RuntimeError("解析结果没有可转写的视频地址")
    with tempfile.TemporaryDirectory(prefix="video-transcription-") as directory:
        audio_path = Path(directory) / "audio.mp3"
        print("正在提取音频...", file=sys.stderr)
        await asyncio.to_thread(extract_audio, video_url, audio_path, parsed.headers)
        print("正在调用 MiMo 转写...", file=sys.stderr)
        transcript = await transcribe_audio(audio_path)
    return make_result(parsed, source_url, transcript)


def write_reports(result: dict, output: Path, overwrite: bool = False) -> list[Path]:
    output = output.expanduser()
    if output.suffix.lower() != ".html":
        raise ValueError("--output 请指定 .html 文件")
    output.parent.mkdir(parents=True, exist_ok=True)
    content = render_html(result)
    with output.open("w" if overwrite else "x", encoding="utf-8") as file:
        file.write(content)
    return [output]


def main() -> int:
    parser = argparse.ArgumentParser(description="将视频分享链接转为本地 HTML 文稿")
    parser.add_argument("text", help="视频链接或平台复制的完整分享文案")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true", help="允许替换已有 HTML；默认保留已有文件")
    args = parser.parse_args()
    try:
        load_config(args.config.expanduser())
        if args.output.suffix.lower() != ".html":
            raise ValueError("--output 请指定 .html 文件")
        args.output = args.output.expanduser()
        if args.output.exists() and not args.overwrite:
            raise ValueError("目标 HTML 已存在，请换一个文件名；确认要替换时使用 --overwrite")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=args.output.parent):
            pass
        if not os.getenv("MIMO_API_KEY", "").strip():
            raise ValueError("本机尚未配置 MiMo API Key")
        result = asyncio.run(process(args.text))
        for output in write_reports(result, args.output, args.overwrite):
            print(f"已生成文稿：{output}", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"转写失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
