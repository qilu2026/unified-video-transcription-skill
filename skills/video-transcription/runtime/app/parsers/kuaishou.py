from __future__ import annotations

from urllib.parse import urlparse

from app.config import settings
from app.http_client import PlatformClient
from app.models import ParsedMedia
from app.parsers.common import load_embedded_json, walk_dicts


MOBILE_UA = "Mozilla/5.0 (Linux; Android 13; Pixel 7 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"


def _video_candidates(photo: dict) -> list[tuple[int, int, int, str]]:
    candidates: list[tuple[int, int, int, str]] = []
    if photo.get("photoUrl"):
        candidates.append((3, 1, 1, photo["photoUrl"]))
    if photo.get("photoH265Url"):
        candidates.append((1, 1, 1, photo["photoH265Url"]))
    resource = photo.get("videoResource") or {}
    resource_json = resource.get("json") if isinstance(resource, dict) else {}
    for codec, priority in (("h264", 3), ("hevc", 1), ("h265", 1)):
        codec_data = resource_json.get(codec, {}) if isinstance(resource_json, dict) else {}
        for adaptation in codec_data.get("adaptationSet", []) if isinstance(codec_data, dict) else []:
            for representation in adaptation.get("representation", []):
                url = representation.get("url")
                if url:
                    resolution = int(representation.get("width", 0)) * int(representation.get("height", 0))
                    bitrate = int(representation.get("maxBitrate", 0) or representation.get("avgBitrate", 0))
                    candidates.append((priority, resolution, bitrate, url))
    manifest = photo.get("manifest") or {}
    for adaptation in manifest.get("playInfo", {}).get("adaptationSet", []) if isinstance(manifest, dict) else []:
        for representation in adaptation.get("representation", []):
            url = representation.get("url")
            if url:
                resolution = int(representation.get("width", 0)) * int(representation.get("height", 0))
                bitrate = int(representation.get("maxBitrate", 0) or representation.get("avgBitrate", 0))
                candidates.append((3, resolution, bitrate, url))
    for item in photo.get("mainMvUrls", []):
        if isinstance(item, dict) and item.get("url"):
            candidates.append((2, 1, 1, item["url"]))
    return candidates


async def parse_kuaishou(url: str, client: PlatformClient) -> ParsedMedia:
    headers = {"User-Agent": MOBILE_UA, "Referer": "https://v.kuaishou.com/", "Accept-Language": "zh-CN,zh;q=0.9"}
    if settings.kuaishou_cookie:
        headers["Cookie"] = settings.kuaishou_cookie
    resolved = await client.resolve(url, headers)
    resolved_url = urlparse(resolved)
    if resolved_url.path.rstrip("/") == "":
        raise ValueError("快手短链没有指向具体作品，可能已失效；请从快手重新复制分享链接")
    html = await client.get_text(resolved, headers)
    if html.lstrip().startswith("{") and '"result":2' in html.replace(" ", ""):
        raise ValueError("快手拒绝了匿名访问；请配置 KUAISHOU_COOKIE 后重试")
    try:
        data = load_embedded_json(html, "window.INIT_STATE")
    except ValueError:
        data = load_embedded_json(html, "window.__APOLLO_STATE__")
    photos = [
        item
        for item in walk_dicts(data)
        if item.get("__typename") == "VisionVideoDetailPhoto"
        or item.get("photoUrl")
        or item.get("videoResource")
        or item.get("manifest")
        or item.get("mainMvUrls")
    ]
    if not photos:
        raise ValueError("快手页面未返回可解析的作品数据")
    photo = max(photos, key=lambda value: len(_video_candidates(value)))
    candidates = _video_candidates(photo)
    if not candidates:
        raise ValueError("快手作品没有可用的视频地址")
    candidates.sort(reverse=True)
    media_url = candidates[0][3]
    item_id = str(photo.get("id") or photo.get("photoId") or urlparse(resolved).path.rstrip("/").split("/")[-1])
    title = str(photo.get("caption") or f"快手作品_{item_id}").strip()
    cover = photo.get("coverUrl") or photo.get("coverThumbnailUrl")
    if not cover and photo.get("coverUrls"):
        cover = photo["coverUrls"][0].get("url") if isinstance(photo["coverUrls"][0], dict) else None
    return ParsedMedia(
        platform="kuaishou",
        item_id=item_id,
        title=title,
        cover_url=cover,
        media_urls=[("video", media_url)],
        headers={"User-Agent": MOBILE_UA, "Referer": resolved},
        author_name=str(photo.get("userName") or ""),
        counts={label: str(photo[key]) for label, key in (
            ("likes", "likeCount"), ("comments", "commentCount"),
            ("shares", "shareCount" if photo.get("shareCount") not in (None, "") else "forwardCount"),
            ("views", "viewCount"),
        ) if photo.get(key) not in (None, "")},
    )
