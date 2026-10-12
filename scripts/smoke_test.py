"""用真实链接把每个平台完整跑一遍，最后输出通过/失败表。

改完代码、发布新版本之前，或者怀疑某个平台改版时运行。链接放在仓库根目录的
smoke_links.txt（不进 git：链接会过期，也属于个人数据），格式见 smoke_links.example.txt。

用法：
    python scripts/smoke_test.py                 # 解析 + 下载音频 + MiMo 转写（按音频时长计费，一轮约几分钱）
    python scripts/smoke_test.py --parse-only    # 只解析出视频地址，不转写、不花钱
    python scripts/smoke_test.py --links 其他文件.txt --config 其他配置.env

需要用安装了依赖的 Python 运行（例如本机安装目录里的 .venv）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import transcribe  # noqa: E402

PLATFORM_NAMES = {"xiaohongshu": "小红书", "douyin": "抖音", "kuaishou": "快手", "weixin": "视频号"}
HOST_PLATFORMS = (
    (("xiaohongshu.com", "xhslink.com", "xhslink.cn"), "xiaohongshu"),
    (("douyin.com", "iesdouyin.com"), "douyin"),
    (("kuaishou.com", "kuaishou.cn", "chenzhongtech.com", "gifshow.com"), "kuaishou"),
    (("weixin.qq.com",), "weixin"),
)


def read_links(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def guess_platform(text: str) -> str:
    try:
        from app.security import extract_platform_url

        host = (urlparse(extract_platform_url(text)).hostname or "").lower()
    except Exception:
        return "未知"
    for suffixes, platform in HOST_PLATFORMS:
        if any(host == suffix or host.endswith("." + suffix) for suffix in suffixes):
            return PLATFORM_NAMES[platform]
    return "未知"


async def parse_only(text: str) -> str:
    from app.http_client import PlatformClient
    from app.parsers import parse_link

    client = PlatformClient()
    try:
        parsed = await parse_link(text, client)
    finally:
        await client.close()
    if not any(kind == "video" and url for kind, url in parsed.media_urls):
        raise RuntimeError("解析结果没有视频地址")
    return f"标题：{parsed.title[:24]}"


async def full_run(text: str) -> str:
    result = await transcribe.process(text)
    transcript = result.get("transcript") or ""
    return f"{len(transcript)} 字：{transcript[:24]}"


def main() -> int:
    parser = argparse.ArgumentParser(description="用真实链接把每个平台完整跑一遍")
    parser.add_argument("--links", type=Path, default=ROOT / "smoke_links.txt")
    parser.add_argument("--config", type=Path, default=transcribe.DEFAULT_CONFIG)
    parser.add_argument("--parse-only", action="store_true", help="只解析链接，不转写（不花钱）")
    args = parser.parse_args()

    if not args.links.exists():
        print(f"找不到链接清单 {args.links}：请照 smoke_links.example.txt 新建 smoke_links.txt", file=sys.stderr)
        return 1
    links = read_links(args.links)
    if not links:
        print(f"{args.links} 里没有链接", file=sys.stderr)
        return 1
    transcribe.load_config(args.config.expanduser())

    rows = []
    for index, text in enumerate(links, start=1):
        platform = guess_platform(text)
        print(f"[{index}/{len(links)}] {platform} ...", file=sys.stderr, flush=True)
        start = time.time()
        try:
            detail = asyncio.run(parse_only(text) if args.parse_only else full_run(text))
            ok = True
        except Exception as exc:
            detail, ok = str(exc), False
        rows.append((platform, ok, time.time() - start, detail))

    mode = "只解析" if args.parse_only else "完整转写"
    print(f"\n实测结果（{mode}）")
    for platform, ok, seconds, detail in rows:
        print(f"{platform:<4} {'✅' if ok else '❌'} {seconds:>5.0f}s  {detail[:110]}")
    failed = sum(1 for _, ok, _, _ in rows if not ok)
    print(f"\n共 {len(rows)} 条，失败 {failed} 条。" if failed else f"\n共 {len(rows)} 条，全部通过。")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
