"""抖音匿名访客详情接口。

整个子包原样复制自 vacacia/astrbot_plugin_link_resolver 的 core/douyin（AGPL-3.0），
对应提交见各文件头部。抖音 2026-09-14 起给 aweme/detail 加了 Argus 门禁，分享页也常常
只返回不含作品数据的空壳；这套代码用 live.douyin.com 下发的匿名身份（ttwid、UIFID_TEMP）
加 a_bogus 与 WebSign 两层签名请求详情接口，不需要登录。

抖音改签名后这里会失效，届时去原项目看是否已跟进，再整份替换这几个文件。
"""

from .guest_api import DouyinGuestAPI
from .errors import DouyinParseError

__all__ = ["DouyinGuestAPI", "DouyinParseError"]