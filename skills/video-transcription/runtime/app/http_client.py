from __future__ import annotations

import os
from urllib.parse import urljoin, urlparse

import httpx
from curl_cffi.requests import AsyncSession as CurlAsyncSession

from app.config import settings
from app.security import validate_platform_url, host_matches


# ============================================================================
# 参考项目：https://github.com/JoeanAmier/XHS-Downloader
#
# 小红书的抓取方式照这个项目改的，以后出问题先去读它的源码再动手：
#   source/module/static.py          IMPERSONATE = "chrome146"；HEADERS 故意不设 User-Agent
#   source/application/request.py    抓页面 HTML，不用 x-s 签名、不用浏览器自动化
#   source/module/manager.py         AsyncSession(impersonate=...)、代理、超时、重试
#
# 关键结论：它抓的是和我们同一个页面 HTML（读 window.__INITIAL_STATE__），
# 两边只差一件事 —— 用 curl_cffi + impersonate 让 TLS/HTTP2 握手指纹
# （JA3/JA4）看起来像真实 Chrome。用 httpx/requests 时 Python 自己的握手
# 指纹会被小红书认成脚本，页面直接 302 到 /login；实测同一个出口 IP、同一个
# 链接、同一份 Cookie，只换成该指纹就立刻 200。
#
# 还有一条实测结论：小红书要求「不要自带 User-Agent」。impersonate 会设一个和
# 指纹配套的 UA，调用方再写一个就对不上（不带 UA 是 200，自带 Chrome/131 的 UA
# 立刻退回 /login）。所以走指纹路径时会摘掉调用方的 UA。
#
# 这两条只对小红书成立，别的平台不要照搬：抖音/快手的页面本来就靠手机 UA 才拿
# 得到数据，摘掉会直接解析失败（已实测）。
#
# IMPERSONATE 会随 Chrome 版本过期。被挡时优先换这个值（或先看参考项目用的是
# 哪个值），而不是去改解析逻辑。
# ============================================================================
IMPERSONATE = os.getenv("IMPERSONATE", "chrome")

REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 6
USER_AGENT_HEADERS = ("user-agent",)


PLATFORM_GROUPS = (
    {"douyin.com", "iesdouyin.com"},
    {"kuaishou.com", "kuaishou.cn", "m.chenzhongtech.com", "m.gifshow.com"},
    {"xiaohongshu.com", "xhslink.com"},
    {"weixin.qq.com"},
)


class PlatformClient:
    """所有平台的 HTTP 请求入口。

    默认走 httpx，行为与引入指纹之前完全一致，抖音/快手/视频号都在这条路上。

    只有小红书需要 TLS 指纹伪装，它调用时显式传 browser_fingerprint=True，
    这时改走 curl_cffi（Chrome 握手 + 由它自己决定 User-Agent）。两条路径并存是
    有意的：指纹这条路要摘 User-Agent，而抖音/快手依赖手机 UA，不能全局套用。
    """

    def __init__(self) -> None:
        # 默认路径：httpx，与引入指纹之前完全相同
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.request_timeout),
            verify=True,
            follow_redirects=False,
        )
        # 指纹路径：只给小红书用。延迟到真正需要时再建 —— 它会在事件循环上注册
        # 额外的 selector 线程，不碰小红书的那些路径（含测试里直接替换 self.client
        # 的用法）不该被它影响。
        self._curl_session: CurlAsyncSession | None = None

    def _curl(self) -> CurlAsyncSession:
        if self._curl_session is None:
            self._curl_session = CurlAsyncSession(
                timeout=settings.request_timeout,
                verify=True,
                allow_redirects=False,
                impersonate=IMPERSONATE,
            )
        return self._curl_session

    async def close(self) -> None:
        await self.client.aclose()
        if self._curl_session is not None:
            await self._curl_session.close()

    async def resolve(self, url: str, headers: dict[str, str]) -> str:
        current = validate_platform_url(url)
        group = next(group for group in PLATFORM_GROUPS if host_matches(urlparse(current).hostname, group))
        for _ in range(MAX_REDIRECTS):
            response = await self.client.get(current, headers=headers)
            if response.status_code not in REDIRECT_STATUSES:
                return str(response.url)
            location = response.headers.get("location")
            if not location:
                return current
            current = validate_platform_url(urljoin(current, location))
            if not host_matches(urlparse(current).hostname, group):
                raise ValueError("分享链接跳转到另一个平台，已停止传递登录状态")
        raise ValueError("分享链接重定向次数过多")

    async def get_text(self, url: str, headers: dict[str, str]) -> str:
        async with self.client.stream("GET", validate_platform_url(url), headers=headers) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > settings.max_html_bytes:
                    raise ValueError("页面内容超过解析限制")
            return content.decode(response.encoding or "utf-8", errors="replace")

    async def get_page(
        self, url: str, headers: dict[str, str], browser_fingerprint: bool = False
    ) -> tuple[str, str]:
        """一次请求拿到 (最终 URL, 正文)。

        browser_fingerprint=True 时走 curl_cffi 的 Chrome 指纹，并摘掉调用方的
        User-Agent（小红书必须这样，原因见文件顶部的说明）。
        默认 False 走 httpx。

        另外，不要用 resolve + get_text 各取一样：那会对同一个地址发两遍请求，
        在被按次数限流的站点上等于自己触发风控。
        """
        if browser_fingerprint:
            # 让 impersonate 设 User-Agent：调用方自己写的那个和 TLS 指纹对不上
            headers = {
                key: value
                for key, value in headers.items()
                if key.lower() not in USER_AGENT_HEADERS
            }
            return await self._follow_curl(url, headers)
        return await self._follow_httpx(url, headers)

    async def _follow_httpx(self, url: str, headers: dict[str, str]) -> tuple[str, str]:
        current = validate_platform_url(url)
        group = next(group for group in PLATFORM_GROUPS if host_matches(urlparse(current).hostname, group))
        for _ in range(MAX_REDIRECTS):
            response = await self.client.get(current, headers=headers)
            if response.status_code not in REDIRECT_STATUSES:
                response.raise_for_status()
                if len(response.content) > settings.max_html_bytes:
                    raise ValueError("页面内容超过解析限制")
                return str(response.url), response.content.decode(response.encoding or "utf-8", errors="replace")
            location = response.headers.get("location")
            if not location:
                return current, ""
            current = validate_platform_url(urljoin(current, location))
            if not host_matches(urlparse(current).hostname, group):
                raise ValueError("分享链接跳转到另一个平台，已停止传递登录状态")
        raise ValueError("分享链接重定向次数过多")

    async def _follow_curl(self, url: str, headers: dict[str, str]) -> tuple[str, str]:
        """手动跟随重定向，每一跳一次请求，正文取自已经拿到的那次响应。"""
        current = validate_platform_url(url)
        group = next(group for group in PLATFORM_GROUPS if host_matches(urlparse(current).hostname, group))
        for _ in range(MAX_REDIRECTS):
            response = await self._curl().get(current, headers=headers)
            if response.status_code not in REDIRECT_STATUSES:
                response.raise_for_status()
                body = response.content or b""
                if len(body) > settings.max_html_bytes:
                    raise ValueError("页面内容超过解析限制")
                return str(response.url), body.decode(response.encoding or "utf-8", errors="replace")
            location = response.headers.get("location")
            if not location:
                return current, ""
            current = validate_platform_url(urljoin(current, location))
            if not host_matches(urlparse(current).hostname, group):
                raise ValueError("分享链接跳转到另一个平台，已停止传递登录状态")
        raise ValueError("分享链接重定向次数过多")

    async def post_json(
        self, url: str, body: dict, headers: dict[str, str] | None = None
    ) -> dict:
        response = await self.client.post(url, json=body, headers=headers)
        response.raise_for_status()
        return response.json()
