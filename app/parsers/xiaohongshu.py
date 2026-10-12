from __future__ import annotations

import asyncio
import os
import sys
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlparse

from app.config import settings
from app.http_client import PlatformClient
from app.models import ParsedMedia
from app.parsers.common import first_url, load_embedded_json, walk_dicts
from app.security import normalize_https_url


DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"

# Gaps before each attempt at a share page, in seconds. The first attempt is immediate.
# The /login wall turned out to be a TLS-fingerprint problem, not a request-count one:
# httpx's Python/OpenSSL handshake was being read as a script and got 302s to /login
# within milliseconds (it flipped instantly, with or without cookies, on every link form),
# which is why waiting minutes never helped. With curl_cffi impersonating Chrome the very
# same request returns 200 on the first try. Keep this short: it is for transient network
# hiccups now, not for waiting out a block.
DEFAULT_RETRY_DELAYS = (0, 30)


def _retry_delays() -> tuple[int, ...]:
    raw = os.getenv("XHS_RETRY_DELAYS", "").strip()
    if not raw:
        return DEFAULT_RETRY_DELAYS
    try:
        delays = tuple(int(item.strip()) for item in raw.split(",") if item.strip())
    except ValueError:
        print(f"XHS_RETRY_DELAYS 不是合法的秒数列表（{raw}），改用默认退避", file=sys.stderr)
        return DEFAULT_RETRY_DELAYS
    return delays or DEFAULT_RETRY_DELAYS


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "meta":
            self.meta.append({key.lower(): value or "" for key, value in attrs})


def _extract_from_meta_tags(html: str) -> tuple[str | None, str | None]:
    """Read Open Graph video/title tags without assuming attribute order or quotes."""
    parser = _MetaParser()
    parser.feed(html)
    values: dict[str, str] = {}
    for attrs in parser.meta:
        name = (attrs.get("property") or attrs.get("name") or "").lower()
        content = unescape(attrs.get("content", "")).strip()
        if name and content and name not in values:
            values[name] = content
    video_url = next((values.get(name) for name in (
        "og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream",
    ) if values.get(name)), None)
    return video_url, values.get("og:title") or values.get("og:description")


def _find_note(data: dict, item_id: str | None = None) -> dict | None:
    notes = [item for item in walk_dicts(data) if isinstance(item.get("video"), dict)]
    if item_id:
        return next((item for item in notes if str(item.get("noteId") or item.get("id")) == item_id), None)
    return max(notes, key=lambda item: len(item.keys()), default=None)


def _best_stream_url(video: dict) -> str | None:
    media = video.get("media", {}) if isinstance(video, dict) else {}
    stream = media.get("stream", {}) if isinstance(media, dict) else {}
    candidates: list[tuple[int, int, int, str]] = []
    for codec, codec_priority in (("h264", 3), ("h265", 2), ("h266", 1), ("av1", 1)):
        entries = stream.get(codec, []) if isinstance(stream, dict) else []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            url = entry.get("masterUrl") or first_url(entry.get("backupUrls"))
            if not url:
                continue
            resolution = int(entry.get("width", 0) or 0) * int(entry.get("height", 0) or 0)
            bitrate = int(entry.get("videoBitrate", 0) or entry.get("avgBitrate", 0) or 0)
            candidates.append((codec_priority, resolution, bitrate, url))
    candidates.sort(reverse=True)
    return candidates[0][3] if candidates else None


async def _fetch_share_page(
    url: str, client: PlatformClient, headers: dict[str, str]
) -> tuple[str, str]:
    """Fetch the share page, retrying briefly if it lands on the /login wall.

    Xiaohongshu answers with a 302 to /login when it distrusts the request. The cause was
    the TLS fingerprint of the HTTP client, not the cookie, the link or the exit IP: the
    same request through curl_cffi impersonating Chrome returns 200 immediately. See
    IMPERSONATE in app/http_client.py. If this starts firing again, check that value
    before changing anything here.

    Returns (resolved_url, html); resolved_url points at /login if the wall never lifted.
    """
    # Measured: the wall outlasts 230s, so a short tight backoff just spends the
    # request budget that keeps it closed. Few knocks, spread over several minutes.
    delays = _retry_delays()
    resolved, html = url, ""
    for attempt, delay in enumerate(delays):
        if delay:
            print(f"小红书访问受限，{delay} 秒后重试（第 {attempt + 1}/{len(delays)} 次）...", file=sys.stderr)
            await asyncio.sleep(delay)
        # browser_fingerprint=True 是关键：小红书认 TLS 指纹，不要自带 User-Agent
        resolved, html = await client.get_page(
            url, headers, browser_fingerprint=True
        )
        if _extract_from_meta_tags(html)[0]:
            return resolved, html
        if "/login" not in resolved:
            # Not a login wall: a real page without og:video. Let the caller decide.
            return resolved, html
    return resolved, html


async def parse_xiaohongshu(url: str, client: PlatformClient) -> ParsedMedia:
    # 这里刻意不设 User-Agent：真实指纹由 PlatformClient 的 impersonate 提供，
    # 两边对不上会直接退回 /login。DESKTOP_UA 只用于下载视频时的请求头。
    headers = {
        "Referer": "https://www.xiaohongshu.com/",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if settings.xhs_cookie:
        headers["Cookie"] = settings.xhs_cookie
    resolved, html = await _fetch_share_page(url, client, headers)
    meta_video_url, meta_title = _extract_from_meta_tags(html)
    if meta_video_url:
        item_id = urlparse(url).path.rstrip("/").split("/")[-1]
        return ParsedMedia(
            platform="xiaohongshu", item_id=item_id,
            title=meta_title or f"小红书作品_{item_id}", cover_url=None,
            media_urls=[("video", normalize_https_url(meta_video_url) or meta_video_url)],
            headers={"User-Agent": DESKTOP_UA, "Referer": resolved},
            author_name="", counts={},
        )
    if "/404" in urlparse(resolved).path or "error_code=300031" in resolved:
        # xsec_token 过期或笔记不可浏览时，小红书跳到 /404（不带 token 时是 300031）。
        # 这跟 Cookie 无关，别让用户去折腾 XHS_COOKIE。
        raise ValueError(
            "小红书链接已失效或笔记不可浏览：分享链接里的 xsec_token 会过期。"
            "请在小红书 App 或网页里重新点“分享”复制新链接再试"
        )
    if "/login" in resolved:
        raise ValueError(
            "小红书把分享页重定向到了登录页。已知原因：HTTP 客户端的 TLS 指纹被识别为"
            "脚本（不是 Cookie、链接或出口 IP 的问题）。请检查 app/http_client.py 里的"
            "IMPERSONATE 是否仍被小红书接受（对照参考项目 "
            "https://github.com/JoeanAmier/XHS-Downloader 的 source/module/static.py），"
            "必要时把它换成更新的 chrome 版本"
        )
    data = load_embedded_json(html, "window.__INITIAL_STATE__")
    note = _find_note(data, urlparse(resolved).path.rstrip("/").split("/")[-1])
    if not note:
        raise ValueError("小红书页面未返回可解析的视频数据；可以配置 XHS_COOKIE 后重试")

    video = note["video"]
    consumer = video.get("consumer") if isinstance(video, dict) else {}
    media_url = _best_stream_url(video) or first_url(consumer) or first_url(video)
    if not media_url and isinstance(consumer, dict) and consumer.get("originVideoKey"):
        key = str(consumer["originVideoKey"]).replace("\\u002F", "/").lstrip("/")
        media_url = f"https://sns-video-bd.xhscdn.com/{key}"
    if not media_url:
        raise ValueError("小红书作品没有可用的视频地址")

    item_id = str(note.get("noteId") or note.get("id") or urlparse(resolved).path.rstrip("/").split("/")[-1])
    title = str(note.get("title") or note.get("desc") or f"小红书作品_{item_id}").strip()
    cover = first_url(note.get("imageList"))
    author = note.get("user") or {}
    stats = note.get("interactInfo") or {}
    return ParsedMedia(
        platform="xiaohongshu",
        item_id=item_id,
        title=title,
        cover_url=normalize_https_url(cover),
        media_urls=[("video", normalize_https_url(media_url) or media_url)],
        headers={"User-Agent": DESKTOP_UA, "Referer": resolved},
        author_name=str(author.get("nickname") or author.get("nickName") or ""),
        counts={label: str(stats[key]) for label, key in (
            ("likes", "likedCount"), ("favorites", "collectedCount"),
            ("comments", "commentCount"), ("shares", "shareCount"),
        ) if stats.get(key) not in (None, "")},
    )
