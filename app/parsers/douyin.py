from __future__ import annotations

import re

from app.http_client import PlatformClient
from app.models import ParsedMedia
from app.parsers.common import first_url, load_embedded_json, walk_dicts


MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Version/16.0 Mobile/15E148 Safari/604.1"


async def fetch_guest_detail(item_id: str, source_url: str) -> dict | None:
    """用匿名访客身份请求作品详情（见 app/parsers/douyin_guest），返回 aweme_detail。"""
    from app.parsers.douyin_guest import DouyinGuestAPI

    return await DouyinGuestAPI().fetch_detail(item_id, source_url=source_url)


async def _item_from_share_page(
    client: PlatformClient, resolved: str, item_id: str, headers: dict[str, str]
) -> dict | None:
    """旧做法：从分享页的 window._ROUTER_DATA 里找作品。拿到空壳时返回 None。"""
    for page_url in (resolved, f"https://www.iesdouyin.com/share/video/{item_id}"):
        try:
            html = await client.get_text(page_url, headers)
            data = load_embedded_json(html, "window._ROUTER_DATA")
        except (ValueError, KeyError):
            continue
        items = [
            item
            for item in walk_dicts(data)
            if isinstance(item.get("video"), dict) and (item.get("aweme_id") or item.get("desc") is not None)
        ]
        item = next((value for value in items if str(value.get("aweme_id")) == item_id), items[0] if items else None)
        if item:
            return item
    return None


async def parse_douyin(url: str, client: PlatformClient) -> ParsedMedia:
    headers = {"User-Agent": MOBILE_UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    resolved = await client.resolve(url, headers)
    match = re.search(r"/(?:video|note)/(\d+)", resolved)
    if not match:
        match = re.search(r"(\d{15,22})", resolved)
    if not match:
        raise ValueError("无法从抖音链接识别作品 ID")
    item_id = match.group(1)

    # 先走匿名访客详情接口：分享页经常只给一个不含作品数据的空壳（实测连续十几次都是空壳）。
    # 访客接口失败时再退回解析分享页，两条都不行才报错。
    guest_error = ""
    try:
        item = await fetch_guest_detail(item_id, resolved)
    except Exception as exc:
        item = None
        guest_error = str(exc)
    if not item:
        item = await _item_from_share_page(client, resolved, item_id, headers)
    if not item:
        detail = f"（访客接口：{guest_error}）" if guest_error else ""
        raise ValueError(
            "抖音作品数据缺少视频信息：访客接口和分享页都没有拿到作品数据"
            f"{detail}。如果作品在浏览器里能正常打开，多半是抖音改了签名，请更新本 Skill 后重试；"
            "也可能是作品已删除或设为私密"
        )

    video = item["video"]
    play_addr = video.get("play_addr") or video.get("playAddr") or {}
    uri = play_addr.get("uri") if isinstance(play_addr, dict) else None
    if uri:
        media_url = f"https://www.iesdouyin.com/aweme/v1/play/?video_id={uri}&ratio=1080p&line=0"
    else:
        media_url = first_url(play_addr)
    if not media_url:
        raise ValueError("抖音作品没有可用的视频地址")

    cover = first_url(video.get("cover")) or first_url(video.get("origin_cover"))
    title = str(item.get("desc") or f"抖音作品_{item_id}").strip()
    author = item.get("author") or {}
    stats = item.get("statistics") or {}
    return ParsedMedia(
        platform="douyin",
        item_id=item_id,
        title=title,
        cover_url=cover,
        media_urls=[("video", media_url)],
        headers={"User-Agent": MOBILE_UA, "Referer": resolved},
        author_name=str(author.get("nickname") or ""),
        counts={label: str(stats[key]) for label, key in (
            ("likes", "digg_count"), ("favorites", "collect_count"),
            ("comments", "comment_count"), ("shares", "share_count"),
        ) if stats.get(key) not in (None, "")},
    )

