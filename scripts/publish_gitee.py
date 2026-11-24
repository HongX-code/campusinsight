#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通过 Gitee REST API 创建仓库并推送（国内网络首选）。

用法：
    set GITEE_TOKEN=<你的私人令牌>
    python scripts/publish_gitee.py --owner <gitee用户名> --repo campusinsight

只使用标准库。文件内容取自当前 git 索引（git ls-files），
故 .gitignore 的排除规则自动生效（.auto-deploy / 数据库 / 私钥不会上传）。
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
import urllib.parse
import urllib.request

API = "https://gitee.com/api/v5"


def call(method: str, path: str, token: str, payload=None, retries: int = 3):
    url = path if path.startswith("http") else API + path
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"token {token}")
        req.add_header("User-Agent", "campusinsight-publisher")
        if body:
            req.add_header("Content-Type", "application/json;charset=UTF-8")
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
    return subprocess.run(
        ["git", "show", f":{path}"], capture_output=True, check=True
    ).stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", required=True, help="Gitee 用户名或空间路径")
    ap.add_argument("--repo", default="campusinsight")
    ap.add_argument("--branch", default="master", help="Gitee 默认分支通常为 master")
    ap.add_argument("--description", default="高校智讯 CampusInsight · 校园通知智能解析平台（华为 ICT 大赛作品）")
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("GITEE_TOKEN")
    files = git_files()
    print(f"[i] 待上传文件数: {len(files)}")

    if args.dry_run:
        total = sum(len(read_blob(f)) for f in files)
        print(f"[i] 总字节: {total:,} ({total / 1024 / 1024:.2f} MB)")
        print("[i] dry-run 结束，未发起任何写请求")
        return 0

    if not token:
        print("[FAIL] 未设置 GITEE_TOKEN 环境变量", file=sys.stderr)
        return 2

    # 1) 校验 token
    q = urllib.parse.urlencode({"access_token": token})
    code, me = call("GET", f"/user?{q}", token)
    if code != 200:
        print(f"[FAIL] token 无效 (HTTP {code}): {me}", file=sys.stderr)
        return 3
    print(f"[i] 已认证: {me.get('login') or me.get('name')}")
    owner = args.owner

    # 2) 创建仓库（已存在则复用）
    payload = {
        "access_token": token,
        "name": args.repo,
        "description": args.description,
        "private": "true" if args.private else "false",
        "auto_init": "false",
        "has_issues": "true",
        "has_wiki": "false",
    }
    code, repo = call("POST", "/user/repos", token, payload)
    if code in (200, 201):
        print(f"[OK] 仓库已创建: {repo.get('html_url')}")
    elif code == 400 and "already" in json.dumps(repo).lower() or code == 409:
        print(f"[i] 仓库已存在，复用: https://gitee.com/{owner}/{args.repo}")
    else:
        # 已有仓库时 Gitee 可能返回 400/403，用 GET 复核
        code2, repo2 = call("GET", f"/repos/{owner}/{args.repo}?{q}", token)
        if code2 == 200:
            print(f"[i] 仓库已存在，复用: {repo2.get('html_url')}")
        else:
            print(f"[FAIL] 创建仓库失败 (HTTP {code}): {repo}", file=sys.stderr)
            return 4

    # 3) 取父 commit
    parent = None
    code, ref = call("GET", f"/repos/{owner}/{args.repo}/git/ref/heads/{args.branch}?{q}", token)
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
            {"access_token": token,
             "content": base64.b64encode(data).decode("ascii"),
             "encoding": "base64"},
        )
        if code not in (200, 201):
            print(f"[FAIL] blob 失败 {path} (HTTP {code}): {blob}", file=sys.stderr)
            return 5
        entries.append(
            {"path": path,
             "mode": "100755" if path.endswith(".sh") else "100644",
             "type": "blob", "sha": blob["sha"]}
        )
        if i % 25 == 0 or i == len(files):
            print(f"[i] blob {i}/{len(files)}")

    # 5) tree
    code, tree = call(
        "POST", f"/repos/{owner}/{args.repo}/git/trees", token,
        {"access_token": token, "tree": entries},
    )
    if code not in (200, 201):
        print(f"[FAIL] tree 失败 (HTTP {code}): {tree}", file=sys.stderr)
        return 6
    print(f"[OK] tree: {tree['sha'][:8]}")

    # 6) commit
    cpayload = {
        "access_token": token,
        "message": "feat: 高校智讯 CampusInsight 校园通知智能解析平台\n\n"
                   "华为 ICT 大赛创新赛参赛作品。采集 → 结构化解析 → 智能问答 → 岗位匹配全链路。\n"
                   "技术栈：Python 3.11 + FastAPI + SQLAlchemy + 原生 HTML/CSS/JS。",
        "tree": tree["sha"],
    }
    if parent:
        cpayload["parents"] = [parent]
    code, commit = call("POST", f"/repos/{owner}/{args.repo}/git/commits", token, cpayload)
    if code not in (200, 201):
        print(f"[FAIL] commit 失败 (HTTP {code}): {commit}", file=sys.stderr)
        return 7
    print(f"[OK] commit: {commit['sha'][:8]}")

    # 7) ref
    if parent:
        code, out = call(
            "PATCH", f"/repos/{owner}/{args.repo}/git/refs/heads/{args.branch}", token,
            {"access_token": token, "sha": commit["sha"], "force": "true"},
        )
    else:
        code, out = call(
            "POST", f"/repos/{owner}/{args.repo}/git/refs", token,
            {"access_token": token, "ref": f"refs/heads/{args.branch}", "sha": commit["sha"]},
        )
    if code not in (200, 201):
        print(f"[FAIL] ref 失败 (HTTP {code}): {out}", file=sys.stderr)
        return 8

    print(f"\n[SUCCESS] 已上传 -> https://gitee.com/{owner}/{args.repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
