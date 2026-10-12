# 来源与第三方说明

本文件记录 Skill 每一部分的技术来源。平台改版导致解析失败时，先查对应来源项目有没有跟进更新，再动自己的代码。

"核对提交"是最近一次核对时来源项目默认分支的最新提交，同时记在 `third_party/<名字>/SOURCE.json` 的 `checked_commit` 里，`watch_paths` 是我们关心的文件。

跟进更新：运行 `python scripts/check_upstream.py`，它会列出每个来源的新提交，并标出哪些改动碰了 `watch_paths`，这类改动才需要跟。跟进完后运行 `python scripts/check_upstream.py --mark-checked <目录名>` 更新基准，再改这里的"核对提交"列。改完代码用 `python scripts/smoke_test.py` 拿真实链接把各平台跑一遍。

## 各平台的技术来源

| 部分 | 现在的做法 | 来源项目 | 拿了什么 | 许可证 | 核对提交 |
| --- | --- | --- | --- | --- | --- |
| 小红书 | 抓分享页 HTML，读 `og:video`；失败时读 `window.__INITIAL_STATE__` | [hostinger-bot/rednote-api](https://github.com/hostinger-bot/rednote-api) | 思路：不需要签名，单次请求读 `og:video` | MIT | `73d60e9`（2026-01-08） |
| 小红书 | 用 `curl_cffi` 伪装 Chrome 的 TLS 指纹，且不自带 User-Agent | [JoeanAmier/XHS-Downloader](https://github.com/JoeanAmier/XHS-Downloader) | 思路：`source/module/static.py` 的 `IMPERSONATE`。没有复制代码 | GPL-3.0 | `3261312`（2026-09-16） |
| 抖音（主路径） | 匿名访客身份 + `a_bogus` + WebSign 签名，请求 `aweme/v1/web/aweme/detail/` | [vacacia/astrbot_plugin_link_resolver](https://github.com/vacacia/astrbot_plugin_link_resolver) | 代码：`core/douyin/` 下的 `abogus.py`、`guest_api.py`、`websign.py`、`errors.py` 原样复制到 `app/parsers/douyin_guest/` | **AGPL-3.0** | `c1b6c01`（2026-10-08） |
| 抖音（备用） | 解析分享页 `window._ROUTER_DATA` | [jiji262/douyin-downloader](https://github.com/jiji262/douyin-downloader)、[wwwzhouhui/video-parser](https://github.com/wwwzhouhui/video-parser) | 页面数据结构 | MIT | `9874f41`（2026-09-22）、`aa931d0`（2026-10-06） |
| 快手 | 解析分享页 `__APOLLO_STATE__` / `INIT_STATE` | 本项目自有代码 | — | — | — |
| 视频号 | 腾讯元宝 `get_parse_result` + 视频号 `get_feed_info`；可选第三方备用解析 | 本项目自有代码 | — | — | — |
| 转写 | MiMo 专用语音识别模型 `mimo-v2.5-asr`，音频按 3 分钟分段 | [MiMo 语音识别文档](https://mimo.mi.com/docs/zh-CN/api/audio/Speech-Recognition) | 接口格式 | 外部服务 | 2026-10-12 |

### 各来源要注意的地方

- **抖音访客接口**：抖音会改签名。2026-09-14 起 `aweme/detail` 加了 Argus 门禁，分享页也常常只返回不含作品数据的空壳。访客接口失效时，到来源项目看是否已跟进，然后**整份替换** `app/parsers/douyin_guest/` 下的文件，不要在里面局部修改。各文件头部记录了复制时对应的提交。
- **小红书指纹**：`IMPERSONATE` 会随 Chrome 版本过期。分享页又被重定向到 `/login` 时，先对照 XHS-Downloader 当前使用的值。
- **jiji262/douyin-downloader** 走的也是 `aweme/detail` 接口，但它要求登录状态加浏览器窗口代发请求，对本 Skill 太重，所以没有采用。它的提交说明和 `core/api_client.py` 里的注释能及时反映抖音风控的变化，适合当作参考。
- **hostinger-bot/rednote-api** 最近一次提交在 2026-01，更新不活跃，小红书的变化以 XHS-Downloader 为准。

## 许可证

本项目按 AGPL-3.0 分发，见 [LICENSE](LICENSE)。`app/parsers/douyin_guest/` 中的代码复制自 AGPL-3.0 项目 vacacia/astrbot_plugin_link_resolver。各来源项目的许可文本和核对时的许可证文件 SHA 保存在 `third_party/` 中，随 Skill 一起分发。

仓库历史没有逐行记录参考来源，上表中"思路""页面数据结构"类的条目不能据此声称某个函数来自某一仓库；只有标为"代码"的条目是原样复制。本项目不包含也不依赖 `videodl`。

## 安装依赖与外部服务

- [HTTPX](https://github.com/encode/httpx) 0.28.1：BSD-3-Clause，抖音、快手、视频号的 HTTP 请求。
- [curl_cffi](https://github.com/lexiforest/curl_cffi) 0.16.3：MIT，小红书的浏览器指纹请求。
- [gmssl](https://github.com/duanhongyi/gmssl) 3.2.2：BSD，抖音 `a_bogus` 签名使用的 SM3 哈希。
- [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) 0.6.0：BSD-2-Clause。安装时由 PyPI 获取相应平台的 FFmpeg 可执行文件；FFmpeg 的许可证取决于构建选项，请参阅 [FFmpeg 许可说明](https://ffmpeg.org/legal.html)。
- 其他 Python 间接依赖保留各自随发行包提供的许可证。仓库只包含依赖清单，不重新分发虚拟环境或 FFmpeg 二进制。
- 音频转写调用用户自己的 [Xiaomi MiMo API](https://platform.xiaomimimo.com/)；视频号解析使用用户自己的腾讯元宝登录状态。两者都是外部服务，各自的服务条款不被本项目的许可证替代。
- 视频号保留一个默认关闭的第三方备用解析选项 `WEIXIN_WORKER_FALLBACK`。启用后会把分享链接交给第三方服务；正常使用不需要启用。

本项目没有获得上述平台或参考项目的背书。开源代码许可不授予他人视频内容的版权或账号访问权限。
