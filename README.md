# 本地视频转写 Skill / Local Video Transcription Skill

把抖音、快手、小红书和视频号的分享链接，在用户自己的电脑上转成可阅读、可保存的 HTML 文稿。

Turn Douyin, Kuaishou, Xiaohongshu, and WeChat Channels share links into readable, saveable HTML transcripts on the user's own computer.

## 功能 / Features

- 固定生成本地 HTML，包含转写正文、原链接、解析后的媒体下载链接、封面链接、作者、作品 ID 和平台实际返回的数据。
- 支持 Windows 和 macOS；不需要服务器、Docker 或常驻后台服务。
- 每位用户使用自己的 MiMo API Key 和平台 Cookie，配置保存在本机，不上传到本仓库。
- 自动准备 Python 虚拟环境和 FFmpeg；首次配置后，后续只需提供视频链接。
- 长视频自动分段转写，单个视频最长约 2 小时。
- 自带环境自检，失败时给出下一步该怎么做。
- 默认不覆盖已有 HTML，避免误删之前的文稿。

- Always generates a local HTML report with the transcript, original link, resolved media download links, cover link, author, item ID, and metadata returned by the platform.
- Supports Windows and macOS without a server, Docker, or background service.
- Each user supplies their own MiMo API Key and platform cookies. Credentials stay in the local configuration and are never uploaded here.
- Automatically prepares an isolated Python environment and FFmpeg. After setup, provide only a video link.
- Long videos are transcribed in segments automatically, up to about two hours per video.
- A built-in environment check, plus failure messages that say what to do next.
- Existing HTML files are protected from accidental overwrite by default.

## 安装 / Install

将这个 Skill 文件夹交给支持从 GitHub 路径安装 Skill 的 agent：

Give the Skill folder or this link to an agent that supports GitHub Skill installation:

```text
https://github.com/zql15857082982-art/unified-video-transcription-skill/tree/main/skills/video-transcription
```

可以这样说：

You can say:

> 请安装这个 Skill，按说明帮我在本机配置，然后转写这个视频链接。
>
> Install this Skill, help me configure it locally as described, and transcribe this video link.

也可以下载仓库后，从 `skills/video-transcription` 文件夹安装。已在 Windows 上实测（2026-10，Python 3.12 和 3.13）；macOS 尚未实测。其他 agent 是否支持自动安装，取决于其自身能力。

You can also clone or download the repository and install the `skills/video-transcription` folder manually. Tested on Windows (2026-10, Python 3.12 and 3.13); macOS has not been tested yet. Automatic installation support depends on the agent.

## 凭证 / Credentials

### MiMo API Key

打开 [Xiaomi MiMo API 开放平台](https://platform.xiaomimimo.com/)，登录小米账号，进入 **控制台 → API Keys**，创建并复制按量调用的 `sk-` API Key。

Open the [Xiaomi MiMo API Open Platform](https://platform.xiaomimimo.com/), sign in with a Xiaomi account, go to **Console → API Keys**, and create a pay-as-you-go `sk-` API Key.

### 腾讯元宝 Cookie（视频号需要） / Tencent Yuanbao Cookie (required for WeChat Channels)

1. 打开腾讯元宝并登录。
2. 在页面空白处点 **右键 → 检查**，切换到 **Network（网络）**。
3. 在下方请求列表点击 **list**。如果没有，保持 Network 打开，给元宝发几句话，再查看新出现的请求。
4. 在请求详情的 **Headers → Request Headers** 中找到 `Cookie`，复制 `Cookie:` 后面的完整值。
5. 只在本机安装提示或本机 `config.env` 中填写，不要发到聊天、截图或 GitHub。

1. Open Tencent Yuanbao and sign in.
2. Right-click the page, choose **Inspect**, and switch to **Network**.
3. Click a request named **list**. If it is absent, keep Network open and send Yuanbao a few messages, then inspect the new requests.
4. In **Headers → Request Headers**, find `Cookie` and copy the complete value after `Cookie:`.
5. Enter it only in the local installer prompt or local `config.env`; never paste it into chat, screenshots, or GitHub.

不需要寻找 `get_parse_result`，也不要复制 `Set-Cookie`。Cookie 过期后重新获取。

Do not search for `get_parse_result` and do not copy `Set-Cookie`. Refresh the Cookie when it expires.

小红书或快手在遇到登录验证、风控或画质限制时，才需要配置对应 Cookie。

Xiaohongshu or Kuaishou cookies are needed only when those platforms require login, trigger risk control, or return limited quality.

## 手动运行 / Manual setup

在可交互终端运行：

Run this in an interactive terminal:

```text
python skills/video-transcription/scripts/setup.py --with-yuanbao
python skills/video-transcription/scripts/run.py --doctor
python skills/video-transcription/scripts/run.py "视频链接"
```

文稿默认存到 `下载/视频转写/平台_标题.html`，重名时自动加序号；需要别的位置时加 `--output "路径/文件名.html"`。

Transcripts are saved to `Downloads/视频转写/<platform>_<title>.html` by default, with a number appended on name clashes. Add `--output "path/name.html"` to choose another location.

`--doctor` 检查依赖、FFmpeg、MiMo 密钥（会用 1 秒静音真调一次，费用可忽略）和各平台 Cookie。安装后先运行一次；转写失败又看不出原因时也先运行它。

`--doctor` checks dependencies, FFmpeg, the MiMo key (with one real call on a one-second silent clip, at negligible cost), and platform cookies. Run it after setup, and whenever a transcription fails for no obvious reason.

默认安装到 `~/.video-transcription`，也可以给安装脚本加 `--install-dir "自定义目录"`。脚本会记住安装位置，后续不重复索要配置。

The default installation directory is `~/.video-transcription`. Add `--install-dir "custom directory"` to choose another location. The installer remembers the location and does not repeatedly ask for credentials.

配置文件示例见 [config.example.env](config.example.env)。不要把真实 Key、Cookie、配置文件、音频或视频加入 Git。

See [config.example.env](config.example.env) for the configuration shape. Never commit real keys, cookies, config files, audio, or video files.

## 输出内容 / Output

HTML 报告包含：

- 转写正文
- 原始分享链接
- 解析后的媒体下载链接和封面链接
- 作者、作品 ID、获取时间和平台返回的互动数据
- 下载链接可能的到期时间及使用提示

The HTML report includes:

- The transcript
- The original share link
- Resolved media and cover links
- Author, item ID, capture time, and platform-provided interaction data
- Link expiry information when available and download notes

下载地址可能过期，部分平台的直链需要特定来源或登录状态。HTML 是本地生成的，外部视频链接仍需要联网打开。

Resolved media links may expire, and some platforms require a specific referrer or login state. The HTML is generated locally; external media links still require an Internet connection.

## 已知限制 / Known limitations

- 小红书分享链接里的 `xsec_token` 会过期。提示链接已失效时，请从 App 或网页重新分享。
- 抖音和小红书依赖平台的签名或浏览器指纹。平台改版后可能需要更新本 Skill。
- 视频号依赖腾讯元宝的登录状态，Cookie 过期后需要重新获取。
- 目前只在 Windows 上实测过。

- Xiaohongshu share links carry an `xsec_token` that expires. When the link is reported as expired, share it again from the app or website.
- Douyin and Xiaohongshu depend on platform signatures or browser fingerprints. Platform changes may require updating this Skill.
- WeChat Channels relies on a Tencent Yuanbao login; refresh the Cookie when it expires.
- Only Windows has been tested so far.

## 维护 / Maintenance

- `python scripts/check_upstream.py`：检查各参考项目自上次核对以来有没有新提交，并标出改动了我们所用文件的项目。
- `python scripts/smoke_test.py`：用本地 `smoke_links.txt` 里的真实链接把各平台跑一遍（格式见 `smoke_links.example.txt`；加 `--parse-only` 只解析、不花钱）。
- 改完 `app/` 或 `transcribe.py` 后运行 `python scripts/package_skill.py`，同步到 `skills/video-transcription/runtime/`。

跟进流程见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

- `python scripts/check_upstream.py`: lists new commits in each reference project since it was last checked, flagging changes to files this project uses.
- `python scripts/smoke_test.py`: runs every platform end to end with real links from a local `smoke_links.txt` (see `smoke_links.example.txt`; add `--parse-only` to skip paid transcription).
- After editing `app/` or `transcribe.py`, run `python scripts/package_skill.py` to sync `skills/video-transcription/runtime/`.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the follow-up process.

## 来源与许可证 / Sources and licensing

本项目参考了 [douyin-downloader](https://github.com/jiji262/douyin-downloader)、[video-parser](https://github.com/wwwzhouhui/video-parser)、[rednote-api](https://github.com/hostinger-bot/rednote-api) 和 [XHS-Downloader](https://github.com/JoeanAmier/XHS-Downloader)，并复制了 [astrbot_plugin_link_resolver](https://github.com/vacacia/astrbot_plugin_link_resolver) 的抖音访客接口代码。每个平台用了哪个项目的什么方法、核对时对应的提交，以及各项目的许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 及 `third_party/`。

This project draws on [douyin-downloader](https://github.com/jiji262/douyin-downloader), [video-parser](https://github.com/wwwzhouhui/video-parser), [rednote-api](https://github.com/hostinger-bot/rednote-api), and [XHS-Downloader](https://github.com/JoeanAmier/XHS-Downloader), and copies the Douyin guest-API code from [astrbot_plugin_link_resolver](https://github.com/vacacia/astrbot_plugin_link_resolver). [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and `third_party/` list, per platform, which method came from which project, the commit last checked, and each license.

本项目采用 [AGPL-3.0](LICENSE) 许可证。外部服务（MiMo、腾讯元宝和视频平台）各自有自己的服务条款。请只处理你有权使用的内容，并自行承担账号、用量和平台合规责任。

This project is released under the [AGPL-3.0](LICENSE). External services (MiMo, Tencent Yuanbao, and the video platforms) have their own terms. Process only content you are authorized to use and remain responsible for your accounts, usage, and compliance.
