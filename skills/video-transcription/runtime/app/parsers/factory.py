from __future__ import annotations

from urllib.parse import urlparse

from app.http_client import PlatformClient
from app.models import ParsedMedia
from app.parsers.douyin import parse_douyin
from app.parsers.kuaishou import parse_kuaishou
from app.parsers.xiaohongshu import parse_xiaohongshu
from app.parsers.weixin import parse_weixin
from app.security import extract_platform_url, host_matches


async def parse_link(text: str, client: PlatformClient) -> ParsedMedia:
    url = extract_platform_url(text)
    host = urlparse(url).hostname or ""
    if host_matches(host, {"douyin.com", "iesdouyin.com"}):
        return await parse_douyin(url, client)
    if host_matches(host, {"kuaishou.com", "kuaishou.cn", "m.chenzhongtech.com", "m.gifshow.com"}):
        return await parse_kuaishou(url, client)
    if host_matches(host, {"xhslink.com", "xhslink.cn", "xiaohongshu.com"}):
        return await parse_xiaohongshu(url, client)
    if host_matches(host, {"weixin.qq.com"}):
        return await parse_weixin(url, client)
    raise ValueError("暂不支持该平台")
