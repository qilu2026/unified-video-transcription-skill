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
TRANSCRIPTION_PROMPT = """请逐字转写音频中实际听到的内容，严格保持原词、语序、口头语和重复内容，不得总结、纠错、改写、补充或解释。

请根据语气和停顿添加中文标点。不要一句一行；同一话题连续成段。只有说话人切换、话题明显变化或较长停顿时才换段。

直接输出转写正文，不要输出推理过程、时间戳、说话人标记。"""


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
    if output.stat().st_size > 22 * 1024 * 1024:
        raise RuntimeError("音频文件过大")


async def transcribe_mimo(audio_path: Path) -> str:
    key = os.getenv("MIMO_API_KEY", "").strip()
    if not key:
        raise RuntimeError("本机尚未配置 MiMo API Key")
    payload = {
        "model": "mimo-v2.5",
        "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": TRANSCRIPTION_PROMPT},
            {"type": "input_audio", "input_audio": {
                "data": base64.b64encode(audio_path.read_bytes()).decode(), "format": "mp3",
            }},
        ]}],
    }

    # Retry up to 3 times if we get empty content
    for attempt in range(3):
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=15)) as client:
            try:
                response = await client.post(
                    "https://api.xiaomimimo.com/v1/chat/completions",
                    headers={"api-key": key}, json=payload,
                )
            except httpx.TimeoutException as exc:
                raise RuntimeError("MiMo 转写超过 600 秒") from exc
            if response.is_error:
                hints = {401: "MiMo 密钥无效或已失效，请更新 MIMO_API_KEY", 402: "MiMo 额度不足，请到开放平台查看余额",
                         403: "MiMo 拒绝访问，请检查账号和模型使用权限", 429: "MiMo 请求受限，请查看账号额度或稍后再试"}
                raise RuntimeError(hints.get(response.status_code, f"MiMo 服务返回 HTTP {response.status_code}，本次未生成文稿"))
            resp_json = response.json()
            choice = resp_json["choices"][0]

            if choice.get("finish_reason") not in (None, "stop"):
                raise RuntimeError("MiMo 未完整结束转写，本次不保存可能被截断的文稿")

            content = choice["message"].get("content") or ""

            # MiMo may put transcription in either 'content' or 'reasoning_content'
            if not content:
                # Try reasoning_content as fallback
                reasoning = choice["message"].get("reasoning_content") or ""
                if reasoning:
                    # Extract text from reasoning_content (may contain timestamps and speaker labels)
                    # Remove timestamp prefixes:
                    # Format 1: "0.00-27.11 | SPEAKER_00: " or "0.00-27.11 | SPEAKER_00(description): "
                    # Format 2: "00:00-00:27\n" (time on separate line)
                    import re
                    lines = reasoning.split('\n')
                    cleaned_lines = []
                    for line in lines:
                        # Remove format 1: timestamp with speaker
                        cleaned = re.sub(r'^\d+\.\d+-\d+\.\d+\s*\|\s*SPEAKER_\d+(\([^)]+\))?\s*:\s*', '', line)
                        # Remove format 2: timestamp only (HH:MM:SS-HH:MM:SS or MM:SS-MM:SS)
                        cleaned = re.sub(r'^\d{1,2}:\d{2}(?::\d{2})?-\d{1,2}:\d{2}(?::\d{2})?\s*', '', cleaned)
                        if cleaned.strip():
                            cleaned_lines.append(cleaned.strip())
                    content = ' '.join(cleaned_lines)

        if isinstance(content, list):
            text = "".join(item if isinstance(item, str) else item.get("text", "") for item in content)
        else:
            text = str(content or "")
        text = normalize_transcript(text)
        if text:
            if attempt > 0:
                print(f"MiMo 第 {attempt + 1} 次尝试成功", file=sys.stderr)
            return text
        if attempt < 2:
            print(f"MiMo 返回空内容，正在重试 ({attempt + 1}/3)...", file=sys.stderr)
            await asyncio.sleep(2)

    raise RuntimeError("MiMo 三次尝试均返回空文稿")


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
        transcript = await transcribe_mimo(audio_path)
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
