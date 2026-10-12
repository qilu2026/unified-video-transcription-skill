import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_skill", ROOT / "scripts" / "package_skill.py")
package_skill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package_skill)


def test_runtime_matches_root():
    """skill/runtime 是发布产物；它一旦和根目录漂移，用户装到的就是旧代码。

    改完根目录源码后请运行 scripts/package_skill.py，不要手工复制文件。
    """
    assert package_skill.differences() == []
    assert package_skill.extra_files() == []


def test_requirements_declare_every_runtime_dependency():
    """客户端用到 curl_cffi；漏写依赖时全新安装会在 import 阶段就失败。"""
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    for name in ("httpx", "curl_cffi", "imageio-ffmpeg"):
        assert name in requirements, f"requirements.txt 缺少 {name}"

    imported = set()
    for file in (ROOT / "app").rglob("*.py"):
        for line in file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("import curl_cffi") or line.startswith("from curl_cffi"):
                imported.add("curl_cffi")
            if line.startswith("import httpx") or line.startswith("from httpx"):
                imported.add("httpx")
    for name in sorted(imported):
        assert name in requirements, f"代码用了 {name}，但 requirements.txt 没声明"


def test_sync_copies_edits_and_reports_them(tmp_path):
    """同步是把根目录的内容真的拷过去，并且能认出多余文件。"""
    root = tmp_path / "repo"
    destination = tmp_path / "runtime"
    (root / "app").mkdir(parents=True)
    (root / "app" / "parsers").mkdir(parents=True)
    (root / "third_party" / "some-project").mkdir(parents=True)
    destination.mkdir()

    for name in package_skill.TOP_LEVEL:
        (root / name).write_text(f"content of {name}", encoding="utf-8")
    (root / "app" / "http_client.py").write_text("new version", encoding="utf-8")
    (root / "app" / "parsers" / "xiaohongshu.py").write_text("parser", encoding="utf-8")
    (root / "third_party" / "some-project" / "LICENSE").write_text("licence", encoding="utf-8")
    (root / "third_party" / "some-project" / "scraper.rs").write_text("source", encoding="utf-8")

    # 根目录改过，runtime 还是旧的；另外留一个已删除文件的残骸
    (destination / "app").mkdir()
    (destination / "app" / "http_client.py").write_text("old version", encoding="utf-8")
    (destination / "app" / "gone.py").write_text("stale", encoding="utf-8")

    stale = package_skill.differences(root, destination)
    assert any("http_client.py" in item for item in stale)
    assert any("xiaohongshu.py" in item for item in stale)
    assert package_skill.extra_files(root, destination) == ["app/gone.py"]

    updated, removed = package_skill.sync(root, destination)
    assert "app/http_client.py" in updated
    assert removed == ["app/gone.py"]
    assert not (destination / "app" / "gone.py").exists()
    assert (destination / "app" / "http_client.py").read_text(encoding="utf-8") == "new version"
    assert package_skill.differences(root, destination) == []
    assert package_skill.extra_files(root, destination) == []
    # 第三方项目只带许可与出处，不带源码
    assert not (destination / "third_party" / "some-project" / "scraper.rs").exists()


def test_importing_the_module_does_not_write_anything(tmp_path, monkeypatch):
    """这个脚本曾经在 import 时就执行复制；被测试或被别人 import 时不该动文件。"""
    before = {path.relative_to(package_skill.DESTINATION).as_posix()
              for path in package_skill.DESTINATION.rglob("*") if path.is_file()}
    spec2 = importlib.util.spec_from_file_location("package_skill_again", ROOT / "scripts" / "package_skill.py")
    module = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(module)
    after = {path.relative_to(module.DESTINATION).as_posix()
             for path in module.DESTINATION.rglob("*") if path.is_file()}
    assert before == after


def test_every_source_has_upstream_baseline():
    """scripts/check_upstream.py 只检查记了 checked_commit 的来源；漏记就会被悄悄跳过。"""
    import json

    for path in sorted((ROOT / "third_party").glob("*/SOURCE.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data.get("checked_commit", "")) == 40, f"{path.parent.name} 缺少 checked_commit"
        assert data.get("watch_paths"), f"{path.parent.name} 缺少 watch_paths"


def test_smoke_links_skip_comments(tmp_path):
    spec3 = importlib.util.spec_from_file_location("smoke_test", ROOT / "scripts" / "smoke_test.py")
    smoke = importlib.util.module_from_spec(spec3)
    spec3.loader.exec_module(smoke)
    links = tmp_path / "links.txt"
    links.write_text("# 注释\n\nhttps://v.kuaishou.com/a\n  # 缩进的注释\n7.15 复制打开抖音 https://v.douyin.com/b/ x\n", encoding="utf-8")
    assert smoke.read_links(links) == ["https://v.kuaishou.com/a", "7.15 复制打开抖音 https://v.douyin.com/b/ x"]
    assert smoke.guess_platform("7.15 复制打开抖音 https://v.douyin.com/b/ x") == "抖音"


@pytest.mark.parametrize("missing", ["app/parsers/xiaohongshu.py", "transcribe.py"])
def test_differences_reports_missing_files(tmp_path, missing):
    root = tmp_path / "repo"
    destination = tmp_path / "runtime"
    (root / "app" / "parsers").mkdir(parents=True)
    destination.mkdir()
    for name in package_skill.TOP_LEVEL:
        (root / name).write_text("x", encoding="utf-8")
    (root / "app" / "parsers" / "xiaohongshu.py").write_text("x", encoding="utf-8")

    stale = package_skill.differences(root, destination)
    assert any(item.startswith(missing) for item in stale), stale
