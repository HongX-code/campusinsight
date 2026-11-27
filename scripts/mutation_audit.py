# -*- coding: utf-8 -*-
"""业务自检的变异测试（mutation testing）：验证「103 项」不是装饰。

    python scripts/mutation_audit.py            # 人类可读报告
    python scripts/mutation_audit.py --json      # 机器可读，供 evidence.py 采集

做法：把整个工程复制到临时目录，逐个注入真实业务缺陷，重新跑
tests/test_smoke.py；每个缺陷都必须让自检变红（退出码非 0 或出现 FAIL）。
跑完自动删除临时副本，**主工程不会被写入任何脏数据**。

背景：光看「103 项全通过」无法区分「代码没问题」和「断言太弱」。
2026-11-08 的首次实测：9 组缺陷只检出 5 组，漏报 top_k 透传、
检索截断与去重指纹三项；补断言后复测 11/11。此脚本把该结论变成
每次都能重跑的一条证据，而不是一次性口头结论。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (编号, 说明, 相对路径, 原始片段, 变异片段)
# 每条都是「真实会发生、且曾经发生过」的回归：锚点必须唯一命中，
# 命中数不为 1 时该条判为失败，避免源码重构后探针静默失效。
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    ("M1", "日期容错放宽（脏日期重新流入）", "app/services/textutil.py",
     "if not ISO_DATETIME_RE.fullmatch(text):", "if not ISO_DATETIME_RE.match(text):"),
    ("M2", "规则校验恒通过（C1—C11 形同虚设）", "app/services/validator.py",
     '"ok": not issues}', '"ok": True}'),
    ("M3", "硬条件冲突不再判暂不符合", "app/services/matcher.py",
     '        level = "no"\n    elif score >= 72:', '        level = "match"\n    elif score >= 72:'),
    ("M4", "删除通知不清理个人待办", "app/routers/notices.py",
     "    db.execute(sql_delete(UserTask).where(UserTask.notice_id == notice_id))",
     "    pass  # MUTATION"),
    ("M5", "问答 top_k 不透传", "app/routers/ai.py",
     "qa.answer(payload.query, corpus, top_k=payload.top_k)",
     "qa.answer(payload.query, corpus, top_k=3)"),
    ("M6", "人工修正不再校验日期格式", "app/routers/notices.py",
     "if value and not is_iso_date(value):", "if False and value and not is_iso_date(value):"),
    ("M7", "新闻稿可加入待办 / 进入匹配", "app/routers/users.py",
     'if not analysis.get("actionable", True):',
     'if False and not analysis.get("actionable", True):'),
    ("M8", "检索结果不再按 top_k 截断", "app/services/qa.py",
     "scored[:top_k]", "scored[:10]"),
    ("M9", "去重指纹失效（正文不参与）", "app/services/textutil.py",
     'payload = re.sub(r"\\s+", "", (title or "") + "|" + (content or ""))',
     'payload = (title or "")  # MUTATION'),
    ("M10", "自检 check() 恒真化", "tests/test_smoke.py",
     "    if cond:\n        PASSED += 1", "    if True:\n        PASSED += 1"),
    ("M11", "断言名重复导致项数虚高", "tests/test_smoke.py",
     'check("健康检查返回 ok",', 'check("同标题不同正文不算重复（正文参与指纹）",'),
]

IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".pytest_cache",
                                "*.db", "*.db-journal", ".env")


def run_smoke(root: Path) -> tuple[int, int, int, list[str]]:
    out = subprocess.run([sys.executable, "tests/test_smoke.py"], cwd=root,
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=240)
    text = out.stdout + out.stderr
    m = re.search(r"通过 (\d+) 项，失败 (\d+) 项", text)
    passed, failed = (int(m.group(1)), int(m.group(2))) if m else (-1, -1)
    names = re.findall(r"\[FAIL\] ([^\n]+)", text)
    if "[FATAL]" in text:
        names.insert(0, re.search(r"\[FATAL\][^\n]+", text).group(0))
    return out.returncode, passed, failed, names


def audit(root: Path, verbose: bool = True) -> dict:
    rc, passed, failed, names = run_smoke(root)
    baseline_ok = rc == 0 and failed == 0 and passed > 0
    result = {"total": len(MUTATIONS), "detected": 0, "baseline_ok": baseline_ok,
              "baseline": {"exit_code": rc, "passed": passed, "failed": failed,
                           "triggered": names[:5]},
              "items": []}
    if verbose:
        print(f"基线：退出码 {rc} | 通过 {passed} / 失败 {failed}")
        print("-" * 72)
    if not baseline_ok:
        # 基线本身失败时，全部变异都会「退出码非 0」，把检出率算成 11/11 是纯粹的假阳性。
        # 这里直接停止，不给出任何检出率结论。
        if verbose:
            print("[FATAL] 基线自检未通过：变异结论不可信，先修复基线再重跑。")
            if names:
                print("        基线失败项：" + "、".join(names[:5]))
        return result
    for mid, label, rel, old, new in MUTATIONS:
        p = root / rel
        src = p.read_text(encoding="utf-8")
        n = src.count(old)
        if n != 1:
            item = {"id": mid, "label": label, "detected": False, "reason": f"锚点命中 {n} 次（期望 1）"}
            result["items"].append(item)
            if verbose:
                print(f"[FAIL ] {mid} {label} -> {item['reason']}")
            continue
        p.write_text(src.replace(old, new, 1), encoding="utf-8")
        try:
            rc, passed, failed, names = run_smoke(root)
            hit = rc != 0 or failed > 0
            result["detected"] += bool(hit)
            item = {"id": mid, "label": label, "detected": hit,
                    "exit_code": rc, "passed": passed, "failed": failed,
                    "triggered": names[:3]}
            if verbose:
                print(f"[{'检出' if hit else '漏报'}] {mid} {label} "
                      f"| 退出码 {rc} | 失败 {failed}"
                      + (f" | 触发 {names[0][:40]}" if names else ""))
        except Exception as exc:                                   # noqa: BLE001
            item = {"id": mid, "label": label, "detected": False,
                    "reason": f"{type(exc).__name__}: {exc}"}
            if verbose:
                print(f"[ERROR] {mid} {label} -> {item['reason']}")
        finally:
            p.write_text(src, encoding="utf-8")
        result["items"].append(item)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="业务自检变异测试（临时副本，主工程只读）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--keep", action="store_true", help="保留临时副本用于排查")
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="zhixun_mutation_"))
    root = tmp / "project"
    try:
        shutil.copytree(ROOT, root, ignore=IGNORE)
        result = audit(root, verbose=not args.json)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        if not result["baseline_ok"]:
            if not args.json:
                print("-" * 72)
                print("结论：基线自检未通过，未执行变异注入；本次不产出检出率。")
            return 1
        if not args.json:
            print("-" * 72)
            print(f"检出率：{result['detected']} / {result['total']}"
                  f"（{'全部检出，自检结论可信' if result['detected'] == result['total'] else '存在漏报，自检结论不可信'}）")
        return 0 if result["detected"] == result["total"] else 1
    finally:
        if args.keep:
            print(f"临时副本保留在：{root}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
