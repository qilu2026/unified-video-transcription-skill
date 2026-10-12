import json
from types import SimpleNamespace

import pytest

from app.parsers.douyin import parse_douyin
from app.parsers.kuaishou import parse_kuaishou
from app.parsers.xiaohongshu import parse_xiaohongshu
from app.parsers.weixin import parse_via_worker, parse_via_yuanbao


class FakeClient:
    def __init__(self, resolved, html):
        self.resolved = resolved
        self.html = html

    async def resolve(self, url, headers):
        return self.resolved

    async def get_text(self, url, headers):
        return self.html

    async def get_page(self, url, headers, browser_fingerprint=False):
        return self.resolved, self.html

    async def post_json(self, url, body, headers=None):
        return self.html


@pytest.mark.asyncio
async def test_weixin_worker_parser():
    payload = {
        "errCode": 0,
        "data": {
            "authorInfo": {"nickname": "作者"},
            "feedInfo": {
                "description": "测试视频号",
                "coverUrl": "https://img.example/wx.jpg",
                "h264VideoInfo": {"videoUrl": "https://finder.video.qq.com/video.mp4"},
            },
            "sceneInfo": {"dynamicExportId": "wx-123"},
        },
    }
    parsed = await parse_via_worker(
        "https://weixin.qq.com/sph/test", FakeClient("", payload)
    )
    assert parsed.platform == "weixin"
    assert parsed.item_id == "wx-123"
    assert parsed.media_urls[0][1] == "https://finder.video.qq.com/video.mp4"
    assert parsed.resolver == "public-worker"


@pytest.mark.asyncio
async def test_weixin_yuanbao_parser(monkeypatch):
    from app.parsers import weixin

    monkeypatch.setattr(
        weixin,
        "settings",
        SimpleNamespace(yuanbao_cookie="hy_source=web; hy_user=test; hy_token=test"),
    )

    class YuanbaoClient:
        async def post_json(self, url, body, headers=None):
            if "get_parse_result" in url:
                return {
                    "code": 0,
                    "data": {
                        "playable_url": "https://example.test/?token=token-1&eid=wx-456",
                        "author": "元宝作者",
                    },
                }
            return {
                "errCode": 0,
                "data": {
                    "authorInfo": {"nickname": "作者"},
                    "feedInfo": {
                        "description": "元宝视频",
                        "h264VideoInfo": {
                            "videoUrl": "https://finder.video.qq.com/yuanbao.mp4"
                        },
                        "likeCountFmt": "12",
                    },
                    "sceneInfo": {"dynamicExportId": "wx-456"},
                },
            }

    parsed = await parse_via_yuanbao(
        "https://weixin.qq.com/sph/test", YuanbaoClient()
    )
    assert parsed.resolver == "yuanbao"
    assert parsed.author_name == "作者"
    assert parsed.counts["likes"] == "12"


@pytest.mark.asyncio
async def test_douyin_prefers_guest_detail_api(monkeypatch):
    from app.parsers import douyin

    async def fake_guest(item_id, source_url):
        assert item_id == "1234567890123456789"
        return {"aweme_id": item_id, "desc": "访客接口", "video": {"play_addr": {"uri": "guest-uri"}},
                "author": {"nickname": "作者"}, "statistics": {"digg_count": 5}}

    monkeypatch.setattr(douyin, "fetch_guest_detail", fake_guest)
    client = FakeClient("https://www.douyin.com/video/1234567890123456789", "<html>空壳，不该被读取</html>")
    parsed = await parse_douyin("https://v.douyin.com/a", client)
    assert parsed.title == "访客接口"
    assert "guest-uri" in parsed.media_urls[0][1]
    assert parsed.counts == {"likes": "5"}


@pytest.mark.asyncio
async def test_douyin_reports_both_paths_when_guest_and_share_page_fail(monkeypatch):
    from app.parsers import douyin

    async def failing_guest(item_id, source_url):
        raise RuntimeError("signed guest detail failed: status 403")

    monkeypatch.setattr(douyin, "fetch_guest_detail", failing_guest)
    shell = '<script>window._ROUTER_DATA = {"loaderData": {"video_(id)/page": {"itemId": "1"}}}</script>'
    with pytest.raises(ValueError, match="访客接口.*403"):
        await parse_douyin("https://v.douyin.com/a", FakeClient("https://www.douyin.com/video/1234567890123456789", shell))


@pytest.mark.asyncio
async def test_douyin_parser(monkeypatch):
    from app.parsers import douyin

    async def failing_guest(item_id, source_url):
        raise RuntimeError("guest unavailable")

    monkeypatch.setattr(douyin, "fetch_guest_detail", failing_guest)
    data = {"loaderData": {"item": {"aweme_id": "1234567890123456789", "desc": "测试抖音", "video": {"play_addr": {"uri": "video-uri"}, "cover": {"url_list": ["https://img.example/cover.jpg"]}}}}}
    data["loaderData"]["item"].update({"author": {"nickname": "测试作者"}, "statistics": {"digg_count": 0, "comment_count": 37, "collect_count": 177, "share_count": 42}})
    html = f"<script>window._ROUTER_DATA = {json.dumps(data)}</script>"
    parsed = await parse_douyin("https://v.douyin.com/a", FakeClient("https://www.douyin.com/video/1234567890123456789", html))
    assert parsed.platform == "douyin"
    assert "video-uri" in parsed.media_urls[0][1]
    assert parsed.author_name == "测试作者"
    assert parsed.counts == {"likes": "0", "comments": "37", "favorites": "177", "shares": "42"}


@pytest.mark.asyncio
async def test_kuaishou_parser():
    data = {"defaultClient": {"photo": {"__typename": "VisionVideoDetailPhoto", "id": "abc", "caption": "测试快手", "coverUrl": "https://img.example/k.jpg", "videoResource": {"json": {"h264": {"adaptationSet": [{"representation": [{"url": "https://cdn.example/k.mp4", "width": 1920, "height": 1080, "maxBitrate": 1000}]}]}}}}}}
    html = f"<script>window.__APOLLO_STATE__ = {json.dumps(data)};</script>"
    parsed = await parse_kuaishou("https://v.kuaishou.com/a", FakeClient("https://www.kuaishou.com/short-video/abc", html))
    assert parsed.platform == "kuaishou"
    assert parsed.media_urls[0][1] == "https://cdn.example/k.mp4"


@pytest.mark.asyncio
async def test_kuaishou_parser_new_init_state_manifest():
    data = {"photo": {"photoId": "abc", "caption": "新版快手", "manifest": {"playInfo": {"adaptationSet": [{"representation": [{"url": "https://cdn.example/new.mp4", "width": 720, "height": 1280, "maxBitrate": 2200}]}]}}, "coverUrls": [{"url": "https://img.example/new.jpg"}]}}
    data["photo"].update({"userName": "作者", "likeCount": 123, "viewCount": 4567, "commentCount": 8, "forwardCount": 0})
    html = f"<script>window.INIT_STATE = {json.dumps(data)};</script>"
    parsed = await parse_kuaishou("https://v.kuaishou.com/a", FakeClient("https://v.m.chenzhongtech.com/fw/photo/abc", html))
    assert parsed.title == "新版快手"
    assert parsed.media_urls[0][1] == "https://cdn.example/new.mp4"
    assert parsed.cover_url == "https://img.example/new.jpg"
    assert parsed.author_name == "作者"
    assert parsed.counts == {"likes": "123", "views": "4567", "comments": "8", "shares": "0"}


@pytest.mark.asyncio
async def test_kuaishou_parser_explains_risk_control():
    client = FakeClient("https://www.kuaishou.com/short-video/abc", '{"result":2,"error_msg":null}')
    with pytest.raises(ValueError, match="KUAISHOU_COOKIE"):
        await parse_kuaishou("https://v.kuaishou.com/a", client)


@pytest.mark.asyncio
async def test_kuaishou_parser_explains_expired_short_link():
    client = FakeClient("https://kuaishou.com/", "")
    with pytest.raises(ValueError, match="可能已失效"):
        await parse_kuaishou("https://v.kuaishou.com/a", client)


@pytest.mark.asyncio
async def test_xiaohongshu_parser():
    data = {"note": {"noteDetailMap": {"abc": {"note": {"noteId": "abc", "title": "测试小红书", "video": {"media": {"stream": {"h264": [{"masterUrl": "http://cdn.example/path/low.mp4", "width": 720, "height": 1280, "videoBitrate": 500}, {"masterUrl": "http://cdn.example/path/high.mp4", "width": 1080, "height": 1920, "videoBitrate": 900}], "h265": [{"masterUrl": "http://cdn.example/path/h265.mp4", "width": 2160, "height": 3840, "videoBitrate": 2000}]}}}, "imageList": [{"urlDefault": "http://img.example/x.jpg"}]}}}}}
    html = f"<script>window.__INITIAL_STATE__ = {json.dumps(data)}</script>"
    data["note"]["noteDetailMap"]["abc"]["note"].update({"user": {"nickname": "笔记作者"}, "interactInfo": {"likedCount": "1716", "collectedCount": "391", "commentCount": "17", "shareCount": "40"}})
    html = f"<script>window.__INITIAL_STATE__ = {json.dumps(data)}</script>"
    parsed = await parse_xiaohongshu("https://xhslink.com/a", FakeClient("https://www.xiaohongshu.com/explore/abc", html))
    assert parsed.platform == "xiaohongshu"
    assert parsed.media_urls[0][1] == "https://cdn.example/path/high.mp4"
    assert parsed.cover_url == "https://img.example/x.jpg"
    assert parsed.author_name == "笔记作者"
    assert parsed.counts == {"likes": "1716", "favorites": "391", "comments": "17", "shares": "40"}


@pytest.mark.asyncio
async def test_xiaohongshu_app_short_link_domain_is_supported():
    """小红书 App 分享出来的是 xhslink.cn；作品 ID 要取跳转后的地址，不是短链接的分享码。"""
    from app.security import extract_platform_url

    text = "Opus5.5档位越高越好吗？... https://xhslink.cn/o/2MIOGXkdWZD 把这段话复制下来，打开【小红书】查看。"
    assert extract_platform_url(text) == "https://xhslink.cn/o/2MIOGXkdWZD"
    page = '<meta property="og:video" content="https://sns-video.example/v.mp4"><meta property="og:title" content="标题">'
    client = FakeClient("https://www.xiaohongshu.com/discovery/item/6ac8e0dd0000000018006539?xsec_token=x", page)
    parsed = await parse_xiaohongshu("https://xhslink.cn/o/2MIOGXkdWZD", client)
    assert parsed.item_id == "6ac8e0dd0000000018006539"


@pytest.mark.asyncio
async def test_xiaohongshu_expired_link_says_reshare_not_cookie():
    """xsec_token 过期会跳到 /404；提示要让用户重新分享，而不是去配 Cookie。"""
    client = FakeClient("https://www.xiaohongshu.com/404?source=note", "<html>你访问的页面不见了</html>")
    with pytest.raises(ValueError, match="重新点“分享”") as caught:
        await parse_xiaohongshu("https://xhslink.com/a", client)
    assert "XHS_COOKIE" not in str(caught.value)


@pytest.mark.asyncio
async def test_kuaishou_share_count_prefers_share_count():
    data = {"photo": {"photoId": "abc", "caption": "标题", "shareCount": 9542, "forwardCount": 0,
                      "manifest": {"playInfo": {"adaptationSet": [{"representation": [{"url": "https://cdn.example/k.mp4"}]}]}}}}
    html = f"<script>window.INIT_STATE = {json.dumps(data)};</script>"
    parsed = await parse_kuaishou("https://v.kuaishou.com/a", FakeClient("https://v.m.chenzhongtech.com/fw/photo/abc", html))
    assert parsed.counts["shares"] == "9542"


@pytest.mark.asyncio
async def test_xiaohongshu_parser_reports_rate_limit(monkeypatch):
    """A share page parked on /login must raise a rate-limit error, not a parse error."""
    monkeypatch.setenv("XHS_RETRY_DELAYS", "0,0,0")
    client = FakeClient("https://www.xiaohongshu.com/login?redirectPath=%2Fexplore%2Fabc", "")
    with pytest.raises(ValueError, match="登录页"):
        await parse_xiaohongshu("https://xhslink.com/a", client)

