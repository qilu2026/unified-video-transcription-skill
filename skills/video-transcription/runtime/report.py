"""Local reports; platform data is rendered as text, never executable markup."""
import re
from datetime import datetime
from html import escape
from urllib.parse import urlsplit, quote

PLATFORMS = {"weixin": "视频号", "douyin": "抖音", "kuaishou": "快手", "xiaohongshu": "小红书"}
COUNTS = {"views": "播放", "likes": "点赞", "favorites": "收藏", "comments": "评论", "shares": "分享"}


def safe_url(value):
    value = str(value or "")
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password else ""
    except ValueError:
        return ""


def md_text(value):
    text = escape(str(value), quote=False).replace("\r", " ").replace("\n", " ")
    for char in "\\`*_{}[]()#+.!|":
        text = text.replace(char, "\\" + char)
    return text


def md_link(label, url):
    url = safe_url(url)
    return f"[{label}](<{quote(url, safe=':/?&=%#@+;,~!$*-._')}>)" if url else ""


def metadata(result):
    rows = [("平台", PLATFORMS.get(result["platform"], result["platform"]))]
    if result.get("author_name"):
        rows.append(("作者", result["author_name"]))
    rows.append(("作品 ID", result["item_id"]))
    if result.get("captured_at"):
        rows.append(("信息获取时间", result["captured_at"]))
    if result.get("expires_at"):
        rows.append(("平台返回的链接到期时间", result["expires_at"]))
    return rows


def available_counts(result):
    return [(label, result.get("counts", {}).get(key)) for key, label in COUNTS.items()
            if result.get("counts", {}).get(key) not in (None, "")]


def make_result(parsed, source_url, transcript):
    return {"platform": parsed.platform, "item_id": parsed.item_id, "title": parsed.title,
            "author_name": parsed.author_name, "source_url": source_url, "transcript": transcript,
            "cover_url": parsed.cover_url, "media_urls": parsed.media_urls, "counts": parsed.counts,
            "expires_at": parsed.expires_at, "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "needs_headers": bool(parsed.headers)}


def link_note(result):
    note = "解析后的地址可能过期；失效时需重新解析原链接。"
    if result.get("needs_headers"):
        note += "此平台下载可能需要原站访问来源或登录状态，浏览器直接打开不一定成功。"
    return note


def render_markdown(result):
    rows = ["# 视频转写", "", "## 视频信息", "", md_text(result["title"]), ""]
    rows.extend(f"- {label}：{md_text(value)}" for label, value in metadata(result))
    rows.extend(["", "## 视频数据", ""])
    counts = available_counts(result)
    if counts:
        rows.extend(["| 指标 | 数值 |", "| --- | --- |"])
        rows.extend(f"| {label} | {md_text(value)} |" for label, value in counts)
    else:
        rows.append("平台未返回互动数据。")
    rows.extend(["", "仅列出平台实际返回的数据；未返回的项目不按 0 计算。数据为获取时的快照。", "", "## 链接与下载", ""])
    rows.append("- " + md_link("打开原视频页面", result["source_url"]))
    for index, (kind, url) in enumerate(result.get("media_urls", []), 1):
        if safe_url(url):
            rows.append("- " + md_link(f"{'视频' if kind == 'video' else '媒体'}地址 {index}（打开 / 下载）", url))
    if safe_url(result.get("cover_url")):
        rows.append("- " + md_link("查看 / 下载原视频封面", result["cover_url"]))
    rows.extend(["", link_note(result), "", "## 文稿", "", result["transcript"], ""])
    return "\n".join(rows)


# 每个平台自己的品牌色，只用在顶部色带和平台标记上，让人一眼认出视频来自哪里
PLATFORM_COLORS = {"douyin": "#FE2C55", "kuaishou": "#FF5000", "xiaohongshu": "#FF2442", "weixin": "#FA9D3B"}

# 预览窗口（例如 Claude 的 HTML 预览）把链接转交给浏览器时会把地址改坏，所以每个链接都配一个复制按钮
COPY_SCRIPT = """
document.addEventListener("click", function (event) {
  var button = event.target.closest("[data-copy]");
  if (!button) return;
  var value = button.getAttribute("data-copy");
  if (value === "#transcript") value = document.getElementById("transcript").innerText.trim();
  var done = function (ok) {
    var label = button.textContent;
    button.textContent = ok ? "已复制" : "复制失败，请手动选中";
    button.classList.toggle("is-done", ok);
    setTimeout(function () { button.textContent = label; button.classList.remove("is-done"); }, 1600);
  };
  var fallback = function () {
    var area = document.createElement("textarea");
    area.value = value;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (error) { ok = false; }
    document.body.removeChild(area);
    done(ok);
  };
  if (navigator.clipboard && window.isSecureContext !== false) {
    navigator.clipboard.writeText(value).then(function () { done(true); }, fallback);
  } else {
    fallback();
  }
});
"""

STYLE = """
:root{--paper:#FBFBF9;--panel:#F2F3F4;--ink:#1E2328;--muted:#5F6670;--rule:#E1E3E6;
--serif:"Source Han Serif SC","Noto Serif CJK SC","Noto Serif SC","Songti SC","STSong","SimSun",serif;
--sans:"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",system-ui,-apple-system,"Segoe UI",sans-serif;color-scheme:light}
*{box-sizing:border-box}
html{background:var(--paper)}
body{margin:0;color:var(--ink);font:15px/1.7 var(--sans);border-top:5px solid var(--brand)}
.page{max-width:1120px;margin:0 auto;padding:56px 32px 40px}
.head{max-width:44em;margin-bottom:44px}
.byline{display:flex;flex-wrap:wrap;align-items:center;gap:6px 14px;margin:0 0 14px;color:var(--muted);font-size:14px}
.platform{display:inline-flex;align-items:center;gap:7px;color:var(--ink);font-weight:600}
.platform::before{content:"";width:9px;height:9px;border-radius:50%;background:var(--brand)}
h1{margin:0;font:700 clamp(26px,3.4vw,38px)/1.32 var(--sans);letter-spacing:-.01em;text-wrap:balance}
.caption{margin:14px 0 0;max-width:40em;color:var(--muted);font-size:15px;white-space:pre-line}
.tags{display:flex;flex-wrap:wrap;gap:6px 12px;margin:14px 0 0;padding:0;list-style:none;color:var(--muted);font-size:14px}
.layout{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:72px;align-items:start}
.reading-head{display:flex;align-items:center;gap:12px;margin-bottom:22px;padding-bottom:12px;border-bottom:1px solid var(--rule)}
h2{margin:0;font-size:15px;font-weight:600}
.reading-head span{margin-left:auto;color:var(--muted);font-size:13px}
.reading-head button{border-color:var(--rule)}
#transcript{max-width:34em;font:19px/2.05 var(--serif);text-align:justify;hanging-punctuation:allow-end}
#transcript p{margin:0 0 1.15em;text-indent:2em}
#transcript p:last-child{margin-bottom:0}
aside{position:sticky;top:28px;display:grid;gap:30px}
aside section{border-top:1px solid var(--rule);padding-top:14px}
aside h2{margin-bottom:12px}
.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:14px 18px;margin:0}
.stats div{display:flex;flex-direction:column-reverse}
.stats dt{color:var(--muted);font-size:13px}
.stats dd{margin:0;font-size:22px;font-weight:600;font-variant-numeric:tabular-nums;line-height:1.3}
.note{margin:10px 0 0;color:var(--muted);font-size:12.5px;line-height:1.6}
.links{margin:0;padding:0;list-style:none;display:grid;gap:8px}
.links li{display:flex;align-items:center;gap:8px;padding:10px 12px;background:var(--panel);border-radius:10px}
.links .name{flex:1;min-width:0;font-size:14px}
.links li.main{background:var(--ink);color:#fff}
a.open,button{font:inherit;font-size:13px;border-radius:7px;padding:4px 10px;cursor:pointer;white-space:nowrap;text-decoration:none}
a.open{color:inherit;border:1px solid currentColor}
button{border:1px solid transparent;background:#fff;color:var(--ink)}
.main button{background:rgba(255,255,255,.14);color:#fff}
button.is-done{background:#1F7A4D;color:#fff}
a.open:hover,button:hover{text-decoration:underline}
:focus-visible{outline:2px solid var(--ink);outline-offset:2px}
.main :focus-visible{outline-color:#fff}
.meta{display:grid;grid-template-columns:auto 1fr;gap:6px 14px;margin:0;font-size:13px}
.meta dt{color:var(--muted)}
.meta dd{margin:0;overflow-wrap:anywhere}
footer{margin-top:64px;padding-top:14px;border-top:1px solid var(--rule);color:var(--muted);font-size:12.5px}
@media (max-width:860px){.page{padding:36px 20px}.layout{grid-template-columns:1fr;gap:44px}aside{position:static}#transcript{font-size:18px}}
@media print{body{border-top:0}.page{padding:0}.layout{grid-template-columns:1fr}aside{position:static}button,a.open{display:none}.links li{background:none;padding:0;color:var(--ink)}}
"""


def _readable_count(value):
    text = str(value).strip()
    if not text.isdigit() or int(text) < 10000:
        return text
    number = int(text)
    if number >= 100_000_000:
        return f"{number / 100_000_000:.1f}".rstrip("0").rstrip(".") + "亿"
    return f"{number / 10000:.1f}".rstrip("0").rstrip(".") + "万"


def _split_title(title):
    """把话题标签从标题里拆出来；第一行做大标题，其余放在下面。"""
    text = str(title or "")
    tags = list(dict.fromkeys(re.findall(r"#([^#\s]+)", text)))
    cleaned = re.sub(r"#[^#\s]+", "", text)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    headline = lines[0] if lines else ""
    return headline, "\n".join(lines[1:]), tags


def _captured_label(value):
    try:
        return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return str(value or "")


def render_html(result):
    platform = PLATFORMS.get(result["platform"], result["platform"])
    brand = PLATFORM_COLORS.get(result["platform"], "#1E2328")
    headline, caption, tags = _split_title(result.get("title"))
    headline = headline or result.get("author_name") or "视频文稿"

    def link_row(name, url, main=False):
        url = safe_url(url)
        if not url:
            return ""
        attr = escape(url, quote=True)
        row_class = ' class="main"' if main else ""
        return (f'<li{row_class}><span class="name">{escape(name)}</span>'
                f'<a class="open" href="{attr}" target="_blank" rel="noopener noreferrer">打开</a>'
                f'<button type="button" data-copy="{attr}">复制链接</button></li>')

    links = ""
    for index, (kind, url) in enumerate(result.get("media_urls", []), 1):
        name = "视频文件" if kind == "video" else "媒体文件"
        links += link_row(name if len(result.get("media_urls", [])) == 1 else f"{name} {index}", url, main=index == 1)
    links += link_row("原视频页面", result.get("source_url"))
    links += link_row("视频封面", result.get("cover_url"))

    counts = available_counts(result)
    stats = ("<dl class=\"stats\">" + "".join(
        f"<div><dt>{label}</dt><dd>{escape(_readable_count(value))}</dd></div>" for label, value in counts) + "</dl>"
             if counts else '<p class="note">平台没有返回互动数据。</p>')

    meta_rows = [(label, value) for label, value in metadata(result) if label not in ("平台", "作者")]
    meta = "".join(f"<dt>{escape(label)}</dt><dd>{escape(str(value))}</dd>" for label, value in meta_rows)

    byline = [f'<span class="platform">{escape(platform)}</span>']
    if result.get("author_name"):
        byline.append(f"<span>{escape(result['author_name'])}</span>")
    if result.get("captured_at"):
        byline.append(f"<span>转写于 {escape(_captured_label(result['captured_at']))}</span>")

    paragraphs = "".join(f"<p>{escape(p.strip()).replace(chr(10), '<br>')}</p>"
                         for p in result["transcript"].split("\n\n") if p.strip())
    char_count = len(re.sub(r"\s", "", result["transcript"]))
    tag_list = ('<ul class="tags">' + "".join(f"<li>#{escape(tag)}</li>" for tag in tags) + "</ul>") if tags else ""
    caption_html = f'<p class="caption">{escape(caption)}</p>' if caption else ""

    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(headline)} · {escape(platform)}转写</title>
<style>:root{{--brand:{brand}}}{STYLE}</style></head><body>
<div class="page">
<header class="head">
<p class="byline">{"".join(byline)}</p>
<h1>{escape(headline)}</h1>{caption_html}{tag_list}
</header>
<div class="layout">
<article>
<div class="reading-head"><h2>转写正文</h2><span>{char_count} 字</span><button type="button" data-copy="#transcript">复制全文</button></div>
<div id="transcript">{paragraphs}</div>
</article>
<aside>
<section><h2>互动数据</h2>{stats}<p class="note">只列平台实际返回的数据，是转写时的快照。</p></section>
<section><h2>链接</h2><ul class="links">{links}</ul>
<p class="note">{escape(link_note(result))}在 Claude 等应用的预览窗口里点“打开”可能失败，请点“复制链接”后粘贴到浏览器，或直接用浏览器打开这份文稿。</p></section>
<section><h2>来源</h2><dl class="meta">{meta}</dl></section>
</aside>
</div>
<footer>这份文稿在你的电脑上生成，正文保留转写原文；视频和封面链接需要联网才能打开。</footer>
</div>
<script>{COPY_SCRIPT}</script>
</body></html>'''
