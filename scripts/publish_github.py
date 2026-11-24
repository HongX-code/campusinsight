#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通过 GitHub REST API 上传仓库（绕开 git 协议，适用于 github.com:443 不通但 api.github.com 可达的网络）。

用法：
    set GITHUB_TOKEN=github_pat_xxx
    python scripts/publish_github.py --owner HongX-code --repo campusinsight

只使用标准库。所有文件内容来自当前 git 索引（git ls-files），
因此 .gitignore 的排除规则自动生效（.auto-deploy / 数据库 / 私钥都不会上传）。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"


def call(method: str, path: str, token: str, payload=None, retries: int = 3):
    url = path if path.startswith("http") else API + path
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "campusinsight-publisher")
        if body:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                data = json.loads(raw)
            except Exception:
                data = {"raw": raw.decode("utf-8", "replace")}
            # 4xx（除限流）不重试，直接抛
            if exc.code < 500 and exc.code != 429:
                return exc.code, data
            if attempt == retries:
                return exc.code, data
        except urllib.error.URLError as exc:
            if attempt == retries:
                raise SystemExit(f"[FAIL] 网络错误 {method} {url}: {exc.reason}")
        time.sleep(2 * attempt)
    return 0, {}


def git_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, check=True
    ).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def read_blob(path: str) -> bytes:
    out = subprocess.run(
        ["git", "show", f":{path}"], capture_output=True, check=True
    ).stdout
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", default="HongX-code")
    ap.add_argument("--repo", default="campusinsight")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--description", default="高校智讯 CampusInsight · 校园通知智能解析平台（华为 ICT 大赛作品）")
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    files = git_files()
    print(f"[i] 待上传文件数: {len(files)}")

    if args.dry_run:
        total = 0
        for f in files:
            n = len(read_blob(f))
            total += n
        print(f"[i] 总字节: {total:,} ({total / 1024 / 1024:.2f} MB)")
        print("[i] dry-run 结束，未发起任何写请求")
        return 0

    if not token:
        print("[FAIL] 未设置 GITHUB_TOKEN / GH_TOKEN 环境变量", file=sys.stderr)
        return 2

    # 1) 确认 token 有效 + 取登录名
    code, me = call("GET", "/user", token)
    if code != 200:
        print(f"[FAIL] token 无效 (HTTP {code}): {me.get('message')}", file=sys.stderr)
        return 3
    owner = args.owner or me["login"]
    print(f"[i] 已认证: {me['login']}")

    # 2) 创建仓库（已存在则复用）
    code, repo = call(
        "POST",
        "/user/repos",
        token,
        {
            "name": args.repo,
            "description": args.description,
            "private": bool(args.private),
            "has_issues": True,
            "has_wiki": False,
            "auto_init": False,
        },
    )
    if code == 201:
        print(f"[OK] 仓库已创建: {repo['html_url']}")
    elif code == 422:
        print(f"[i] 仓库已存在，复用: https://github.com/{owner}/{args.repo}")
    else:
        print(f"[FAIL] 创建仓库失败 (HTTP {code}): {repo}", file=sys.stderr)
        return 4

    # 3) 取父 commit（仓库非空时）
    parent = None
    code, ref = call("GET", f"/repos/{owner}/{args.repo}/git/ref/heads/{args.branch}", token)
    if code == 200:
        parent = ref["object"]["sha"]
        print(f"[i] 分支已存在，父 commit: {parent[:8]}")

    # 4) 逐个创建 blob
    entries = []
    for i, path in enumerate(files, 1):
        data = read_blob(path)
        code, blob = call(
            "POST",
            f"/repos/{owner}/{args.repo}/git/blobs",
            token,
            {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"},
        )
        if code != 201:
            print(f"[FAIL] blob 失败 {path} (HTTP {code}): {blob}", file=sys.stderr)
            return 5
        entries.append(
            {"path": path, "mode": "100755" if path.endswith(".sh") else "100644",
             "type": "blob", "sha": blob["sha"]}
        )
        if i % 25 == 0 or i == len(files):
            print(f"[i] blob {i}/{len(files)}")

    # 5) 创建 tree
    code, tree = call(
        "POST", f"/repos/{owner}/{args.repo}/git/trees", token, {"tree": entries}
    )
    if code != 201:
        print(f"[FAIL] tree 失败 (HTTP {code}): {tree}", file=sys.stderr)
        return 6
    print(f"[OK] tree: {tree['sha'][:8]}")

    # 6) 创建 commit
    commit_payload = {
        "message": "feat: 高校智讯 CampusInsight 校园通知智能解析平台\n\n"
                   "华为 ICT 大赛创新赛参赛作品。采集 → 结构化解析 → 智能问答 → 岗位匹配全链路。\n"
                   "技术栈：Python 3.11 + FastAPI + SQLAlchemy + 原生 HTML/CSS/JS。",
        "tree": tree["sha"],
    }
    if parent:
        commit_payload["parents"] = [parent]
    code, commit = call(
        "POST", f"/repos/{owner}/{args.repo}/git/commits", token, commit_payload
    )
    if code != 201:
        print(f"[FAIL] commit 失败 (HTTP {code}): {commit}", file=sys.stderr)
        return 7
    print(f"[OK] commit: {commit['sha'][:8]}")

    # 7) 创建/更新 ref
    if parent:
        code, out = call(
            "PATCH",
            f"/repos/{owner}/{args.repo}/git/refs/heads/{args.branch}",
            token,
            {"sha": commit["sha"], "force": True},
        )
    else:
        code, out = call(
            "POST",
            f"/repos/{owner}/{args.repo}/git/refs",
            token,
            {"ref": f"refs/heads/{args.branch}", "sha": commit["sha"]},
        )
    if code not in (200, 201):
        print(f"[FAIL] ref 失败 (HTTP {code}): {out}", file=sys.stderr)
        return 8

    print(f"\n[SUCCESS] 已上传 -> https://github.com/{owner}/{args.repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
