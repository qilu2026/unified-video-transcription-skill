"""检查参考项目自上次核对以来有没有更新。

每个参考项目在 third_party/<名字>/SOURCE.json 里记着核对时的提交（checked_commit）和
我们关心的文件（watch_paths）。这个脚本去 GitHub 查它们现在的最新提交，列出新提交，
并标出哪些改动碰了我们关心的文件 —— 那些才需要跟进。只读，不改任何代码。

用法：
    python scripts/check_upstream.py
    python scripts/check_upstream.py --mark-checked vacacia--astrbot_plugin_link_resolver
        # 跟进完某个项目后，把它的核对基准更新到当前最新提交
    python scripts/check_upstream.py --mark-checked all

不登录时 GitHub 每小时限 60 次请求，一次检查约用 10 次。频繁使用可以设置环境变量
GITHUB_TOKEN（任意只读 token 即可）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
THIRD_PARTY = ROOT / "third_party"
API = "https://api.github.com"


def load_sources() -> list[tuple[Path, dict]]:
    sources = []
    for path in sorted(THIRD_PARTY.glob("*/SOURCE.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("checked_commit"):
            sources.append((path, data))
    return sources


def repo_name(data: dict) -> str:
    return urlparse(data["repository"]).path.strip("/")


def client() -> httpx.Client:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "check-upstream"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(base_url=API, headers=headers, timeout=30, follow_redirects=True)


def check(http: httpx.Client, data: dict) -> dict:
    repo, branch, base = repo_name(data), data.get("branch", "main"), data["checked_commit"]
    response = http.get(f"/repos/{repo}/compare/{base}...{branch}")
    if response.status_code == 404:
        return {"error": "仓库或基准提交已不存在（可能被删除、改名或强推）"}
    if response.status_code == 403:
        return {"error": "GitHub 请求次数用完了，稍后再试或设置 GITHUB_TOKEN"}
    response.raise_for_status()
    compare = response.json()
    watch = data.get("watch_paths") or []
    touched = sorted({
        file["filename"] for file in compare.get("files") or []
        if any(file["filename"] == path or file["filename"].startswith(path.rstrip("/") + "/") for path in watch)
    })
    commits = [
        (item["commit"]["author"]["date"][:10], item["sha"][:7], item["commit"]["message"].splitlines()[0])
        for item in compare.get("commits") or []
    ]
    license_changed = False
    license_response = http.get(f"/repos/{repo}/contents/LICENSE", params={"ref": branch})
    if license_response.status_code == 200 and data.get("blob_sha"):
        license_changed = license_response.json().get("sha") != data["blob_sha"]
    head = compare["commits"][-1]["sha"] if compare.get("commits") else base
    return {"ahead": compare.get("ahead_by", 0), "commits": commits, "touched": touched,
            "license_changed": license_changed, "head": head}


def mark_checked(http: httpx.Client, target: str) -> int:
    count = 0
    for path, data in load_sources():
        if target not in ("all", path.parent.name):
            continue
        response = http.get(f"/repos/{repo_name(data)}/commits/{data.get('branch', 'main')}")
        response.raise_for_status()
        data["checked_commit"] = response.json()["sha"]
        data["checked_on"] = date.today().isoformat()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已把 {path.parent.name} 的核对基准更新到 {data['checked_commit'][:7]}")
        count += 1
    if not count:
        print(f"没有找到 {target}，可选值：all 或 third_party 下的目录名", file=sys.stderr)
        return 1
    print("记得再运行 scripts/package_skill.py，并在 THIRD_PARTY_NOTICES.md 里更新对应的核对提交。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="检查参考项目自上次核对以来有没有更新")
    parser.add_argument("--mark-checked", metavar="名字", help="把某个参考项目（或 all）的核对基准更新到当前最新提交")
    args = parser.parse_args()
    with client() as http:
        if args.mark_checked:
            return mark_checked(http, args.mark_checked)
        need_follow = 0
        for path, data in load_sources():
            name = repo_name(data)
            try:
                result = check(http, data)
            except httpx.HTTPError as exc:
                result = {"error": f"请求失败：{exc}"}
            print(f"\n{'=' * 60}\n{name}（{data.get('used_for', '')}）")
            print(f"  核对基准：{data['checked_commit'][:7]}，{data.get('checked_on', '')}")
            if "error" in result:
                print(f"  ❌ {result['error']}")
                continue
            if not result["ahead"]:
                print("  ✅ 没有新提交")
                continue
            if result["touched"]:
                need_follow += 1
                print(f"  ⚠️ 有 {result['ahead']} 个新提交，其中改动了我们关心的文件：")
                for filename in result["touched"]:
                    print(f"       {filename}")
            else:
                print(f"  ℹ️ 有 {result['ahead']} 个新提交，但没碰我们关心的文件，一般不用跟")
            for day, sha, title in result["commits"][-10:]:
                print(f"     {day}  {sha}  {title}")
            if len(result["commits"]) > 10:
                print(f"     ……更早的 {len(result['commits']) - 10} 个提交省略")
            if result["license_changed"]:
                print("  ⚠️ LICENSE 文件变了，请核对许可证是否仍然兼容")
        print(f"\n{'=' * 60}")
        print(f"需要跟进的项目：{need_follow} 个" if need_follow else "没有需要跟进的改动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
