"""把根目录的公开运行文件同步进 Skill 的 runtime 目录。

这是更新 skills/video-transcription/runtime 的唯一方式：改完根目录的源码后跑一次
这个脚本，不要把文件逐个手工复制进去 —— 那样三份副本会各自漂移，而且没人会发现。

链条：
    根目录 app/ + transcribe.py  --package_skill.py-->  skill/runtime
                                 --scripts/setup.py---->  本机安装目录

用法：
    python scripts/package_skill.py            # 同步（会删除 runtime 里的多余文件）
    python scripts/package_skill.py --check    # 只检查，不一致时退出码为 1（CI/测试用）
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "skills" / "video-transcription" / "runtime"

# 根目录下需要进入发布包的顶层文件
TOP_LEVEL = (
    "transcribe.py",
    "report.py",
    "requirements.txt",
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
)
# third_party 下只带许可与出处说明，不带别人的源码
THIRD_PARTY_FILES = {"LICENSE", "SOURCE.json", "ORIGINAL_NOTICES.md"}


def _is_generated(path: Path) -> bool:
    return "__pycache__" in path.parts or path.name.endswith(".pyc")


def packaged_files(root: Path = ROOT, destination: Path = DESTINATION) -> list[tuple[Path, Path]]:
    """发布包里应有的每一对 (源文件, 目标文件)。"""
    items = [(root / name, destination / name) for name in TOP_LEVEL]
    items += [
        (file, destination / file.relative_to(root))
        for file in sorted((root / "app").rglob("*.py"))
    ]
    items += [
        (file, destination / file.relative_to(root))
        for file in sorted((root / "third_party").rglob("*"))
        if file.is_file() and file.name in THIRD_PARTY_FILES
    ]
    return items


def differences(root: Path = ROOT, destination: Path = DESTINATION) -> list[str]:
    """runtime 与根目录不一致的项（内容不同或缺失）。"""
    stale = []
    for source, target in packaged_files(root, destination):
        relative = source.relative_to(root).as_posix()
        if not target.is_file():
            stale.append(f"{relative}：runtime 里缺失")
        elif not filecmp.cmp(source, target, shallow=False):
            stale.append(f"{relative}：内容与根目录不同")
    return stale


def extra_files(root: Path = ROOT, destination: Path = DESTINATION) -> list[str]:
    """停留在 runtime 里、但根目录已经不再产出的文件（例如改名后的残骸）。"""
    expected = {target for _, target in packaged_files(root, destination)}
    found = []
    for path in sorted(destination.rglob("*")):
        if not path.is_file() or _is_generated(path):
            continue
        if path not in expected:
            found.append(path.relative_to(destination).as_posix())
    return found


def sync(root: Path = ROOT, destination: Path = DESTINATION) -> tuple[list[str], list[str]]:
    """把根目录的内容镜像到 runtime，返回 (已更新, 已删除多余)。"""
    updated = []
    for source, target in packaged_files(root, destination):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        updated.append(target.relative_to(destination).as_posix())

    removed = extra_files(root, destination)
    for relative in removed:
        (destination / relative).unlink()
    return updated, removed


def main() -> int:
    parser = argparse.ArgumentParser(description="把根目录的公开运行文件同步进 Skill 的 runtime 目录")
    parser.add_argument("--check", action="store_true", help="只检查一致性，不写入文件")
    args = parser.parse_args()

    if args.check:
        stale, extra = differences(), extra_files()
        if not stale and not extra:
            print("runtime 与根目录一致。")
            return 0
        for item in stale:
            print(f"不一致：{item}", file=sys.stderr)
        for item in extra:
            print(f"多余：{item}", file=sys.stderr)
        print("请运行 scripts/package_skill.py 同步（不要手工复制文件）。", file=sys.stderr)
        return 1

    updated, removed = sync()
    print(f"已更新 {len(updated)} 个公开运行文件，不含本机配置。")
    for relative in removed:
        print(f"已删除 runtime 中多余的文件：{relative}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
