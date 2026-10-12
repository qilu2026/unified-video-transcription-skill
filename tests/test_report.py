import pytest
from app.models import ParsedMedia
from report import make_result, render_html, render_markdown
from transcribe import write_reports


def sample():
    parsed = ParsedMedia(platform="weixin", item_id="test-id", title='标题 <script>alert(1)</script>',
                         cover_url="https://example.com/cover.jpg", media_urls=[("video", "https://example.com/v.mp4?a=1&b=2")],
                         author_name="作者", counts={"likes": "0", "comments": "17", "shares": ""},
                         headers={"Cookie": "private-cookie"}, expires_at="2030-01-01T00:00:00+00:00")
    return make_result(parsed, "https://weixin.qq.com/sph/example", "第一段。\n\n第二段。")


def test_reports_keep_zero_missing_counts_and_direct_urls_distinct(tmp_path):
    result = sample()
    paths = write_reports(result, tmp_path / "文稿.html")
    assert len(paths) == 1
    html = paths[0].read_text(encoding="utf-8")
    markdown = render_markdown(result)
    assert "| 点赞 | 0 |" in markdown
    assert "| 评论 | 17 |" in markdown
    assert "| 分享 |" not in markdown
    assert "https://example.com/v.mp4?a=1&b=2" in markdown
    assert 'href="https://example.com/v.mp4?a=1&amp;b=2"' in html
    assert "2030-01-01" in html and "2030-01-01" in markdown
    assert "第一段。\n\n第二段。" in markdown
    assert "private-cookie" not in markdown + html
    # 页面自带一段复制按钮用的脚本；要确认的是标题里注入的脚本被转义了
    assert "<script>alert(1)" not in html and "&lt;script&gt;alert(1)" in html
    assert html.count("<script>") == 1


def test_every_link_has_a_copy_button_with_the_same_url():
    """预览窗口会把点击的链接改坏，所以每个链接都要能复制出原样地址。"""
    html = render_html(sample())
    for url in ("https://example.com/v.mp4?a=1&amp;b=2", "https://weixin.qq.com/sph/example", "https://example.com/cover.jpg"):
        assert f'href="{url}"' in html and f'data-copy="{url}"' in html
    assert 'data-copy="#transcript"' in html


def test_title_tags_and_large_counts_are_readable():
    result = sample()
    result["title"] = "饭局上最考验人的\n\n解释得越多越尴尬 #酒局人情世故 #饭局文化"
    result["counts"] = {"views": "16477251", "likes": "255085", "comments": "8570"}
    html = render_html(result)
    assert "<h1>饭局上最考验人的</h1>" in html
    assert "<li>#酒局人情世故</li>" in html and "#饭局文化" not in html.split("<h1>")[1].split("</h1>")[0]
    assert "<dd>1647.7万</dd>" in html and "<dd>25.5万</dd>" in html and "<dd>8570</dd>" in html


def test_reports_reject_active_url_schemes():
    result = sample()
    result["source_url"] = "javascript:alert(1)"
    result["media_urls"] = [("video", "data:text/html,bad")]
    result["cover_url"] = "javascript:alert(2)"
    for content in (render_markdown(result), render_html(result)):
        assert "javascript:" not in content
        assert "data:text/html" not in content


def test_html_only_avoids_extra_file(tmp_path):
    paths = write_reports(sample(), tmp_path / "only.html")
    assert len(paths) == 1
    assert not (tmp_path / "only.md").exists()
    with pytest.raises(ValueError, match="html"):
        write_reports(sample(), tmp_path / "invalid.md")
