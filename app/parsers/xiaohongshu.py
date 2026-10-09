from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlparse

from app.config import settings
from app.http_client import PlatformClient
from app.models import ParsedMedia
from app.parsers.common import first_url, load_embedded_json, walk_dicts
from app.security import normalize_https_url


DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"


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


async def parse_xiaohongshu(url: str, client: PlatformClient) -> ParsedMedia:
    headers = {
        "User-Agent": DESKTOP_UA,
        "Referer": "https://www.xiaohongshu.com/",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if settings.xhs_cookie:
        headers["Cookie"] = settings.xhs_cookie
    resolved = await client.resolve(url, headers)
    html = await client.get_text(resolved, headers)
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
    if "/login" in resolved:
        raise ValueError("小红书将分享页重定向到登录页；当前 Cookie 已失效或未被接受")
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
