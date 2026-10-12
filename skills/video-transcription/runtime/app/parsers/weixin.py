from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, urlparse

from app.config import settings
from app.http_client import PlatformClient
from app.models import ParsedMedia


YUANBAO_PARSE_URL = "https://yuanbao.tencent.com/api/weixin/get_parse_result"
FEED_INFO_URL = "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"
WORKER_URL = "https://sph.litao.workers.dev/api/fetch_video_profile"


def yuanbao_headers(cookie: str) -> dict[str, str]:
    value = cookie.strip()
    if value.lower().startswith("cookie:"):
        value = value.split(":", 1)[1].strip()
    value = "".join(char for char in value if " " <= char <= "~")
    return {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://yuanbao.tencent.com",
        "referer": "https://yuanbao.tencent.com/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/137.0.0.0 Safari/537.36",
        "cookie": value,
    }


def _expires_at(value: object) -> str | None:
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _parsed_media(url: str, initial: dict, data: dict, resolver: str) -> ParsedMedia:
    feed = data.get("feedInfo") or {}
    author = data.get("authorInfo") or {}
    video_url = (
        (feed.get("h264VideoInfo") or {}).get("videoUrl")
        or feed.get("videoUrl")
        or (feed.get("h265VideoInfo") or {}).get("videoUrl")
    )
    if not video_url:
        raise ValueError("视频号没有返回视频地址")
    scene = data.get("sceneInfo") or {}
    item_id = scene.get("dynamicExportId") or initial.get("wx_export_id")
    return ParsedMedia(
        platform="weixin",
        item_id=str(item_id or url.rstrip("/").rsplit("/", 1)[-1]),
        title=feed.get("description") or initial.get("desc") or "视频号作品",
        cover_url=feed.get("coverUrl") or initial.get("cover_url"),
        media_urls=[("video", video_url)],
        author_name=author.get("nickname") or initial.get("author") or "",
        author_avatar_url=author.get("headImgUrl") or initial.get("author_icon") or "",
        counts={
            "likes": str(feed.get("likeCountFmt") or ""),
            "favorites": str(feed.get("favCountFmt") or ""),
            "comments": str(feed.get("commentCountFmt") or ""),
            "shares": str(feed.get("forwardCountFmt") or ""),
        },
        expires_at=_expires_at(scene.get("expiredTime")),
        resolver=resolver,
    )


async def parse_via_yuanbao(url: str, client: PlatformClient) -> ParsedMedia:
    if not settings.yuanbao_cookie:
        raise ValueError("本机尚未配置腾讯元宝 Cookie")
    initial_response = await client.post_json(
        YUANBAO_PARSE_URL,
        {"type": "video_channel_url", "url": url, "scene": 1},
        yuanbao_headers(settings.yuanbao_cookie),
    )
    if initial_response.get("code") not in {None, 0}:
        raise ValueError(initial_response.get("msg") or "元宝解析失败")
    initial = initial_response.get("data") or {}
    playable_url = initial.get("playable_url")
    if not playable_url:
        raise ValueError("元宝没有返回可用的视频信息")
    playable_query = parse_qs(urlparse(playable_url).query)
    token = (playable_query.get("token") or [""])[0]
    export_id = (playable_query.get("eid") or [initial.get("wx_export_id") or ""])[0]
    if not token or not export_id:
        raise ValueError("视频临时访问令牌不完整")

    rid = f"{int(time.time()):x}-{secrets.token_hex(4)}"
    feed_url = (
        f"{FEED_INFO_URL}?_rid={rid}"
        "&_pageUrl=https:%2F%2Fchannels.weixin.qq.com%2Ffinder-preview%2Fpages%2Ffeed"
    )
    referer = (
        "https://channels.weixin.qq.com/finder-preview/pages/feed"
        f"?entry_card_type=48&comment_scene=39&appid=0&token={quote(token, safe='')}"
        f"&entry_scene=0&eid={quote(export_id, safe='')}"
    )
    feed_response = await client.post_json(
        feed_url,
        {"baseReq": {"generalToken": token}, "exportId": export_id},
        {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "origin": "https://channels.weixin.qq.com",
            "referer": referer,
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/137.0.0.0 Safari/537.36",
        },
    )
    if feed_response.get("errCode") not in {None, 0} or not (
        feed_response.get("data") or {}
    ).get("feedInfo"):
        raise ValueError(feed_response.get("errMsg") or "视频号没有返回视频信息")
    return _parsed_media(url, initial, feed_response["data"], "yuanbao")


async def parse_via_worker(url: str, client: PlatformClient) -> ParsedMedia:
    result = await client.post_json(WORKER_URL, {"url": url})
    if result.get("errCode") != 0:
        raise ValueError(result.get("errMsg") or "备用解析服务失败")
    return _parsed_media(url, {}, result.get("data") or {}, "public-worker")


async def parse_weixin(url: str, client: PlatformClient) -> ParsedMedia:
    try:
        return await parse_via_yuanbao(url, client)
    except Exception as exc:
        if not settings.weixin_worker_fallback:
            raise ValueError(
                f"元宝解析失败：{exc}。视频号要靠腾讯元宝的登录状态解析，Cookie 过期后会失败："
                "请重新登录 https://yuanbao.tencent.com/ ，按 README 的方法复制 Cookie，"
                "更新 config.env 里的 YUANBAO_COOKIE"
            ) from exc
        return await parse_via_worker(url, client)
