#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按同一套映射，就地脱敏本地演示库（`data/gaoxiao_zhixun.db`）。

为什么也要改库
--------------
`data/*.db` 已被 `.gitignore` 排除，不会进仓库，所以它本身不是泄漏源。
但它是**截图与演示的唯一数据来源**：库里还留着真实手机号和姓名，
下一次 `scripts/capture.py` 截出来的界面图就会把这些号码印在图上，
再被提交进仓库——等于绕开 `.gitignore` 泄漏一次。
所以库里的原文、分析结果（contact / evidence 的 JSON）都要一起换掉，
保持「脱敏后的库 → 截图也干净」这条链路成立。

只做字符串替换，不重跑解析：把手机号换成同样是 11 位的占位号、
把个人邮箱换成 example 域、把姓名换成「姓 + 老师」，
抽取行为与字段形态都不变，所以不需要重算分析结果。

用法：
    python scripts/sanitize_demo_db.py
    python scripts/sanitize_demo_db.py --check
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "gaoxiao_zhixun.db"

sys.path.insert(0, str(ROOT / "scripts"))
from sanitize_data import _scrub, load_map, residual  # noqa: E402

# 只保留一份替换逻辑：直接复用 sanitize_data._scrub，避免两处映射表
# 各写一遍、改了一处忘了另一处（真值本身放在 .sanitize_map.json，不进仓库）。
TABLE = load_map()
# 长串先替换：带端口的地址必须先于裸地址
REPLACEMENTS = sorted(
    [(old, new) for kind in ("mobile", "email", "name", "ip")
     for old, new in TABLE.get(kind, {}).items()],
    key=lambda kv: len(kv[0]), reverse=True,
)


def scrub(text: str) -> str:
    new, _counts = _scrub(text, TABLE)
    return new


def main() -> int:
    ap = argparse.ArgumentParser(description="演示库脱敏")
    ap.add_argument("--check", action="store_true", help="只检查，不写入")
    args = ap.parse_args()

    if not DB.exists():
        print("找不到 %s" % DB)
        return 2

    con = sqlite3.connect(str(DB))
    tables = [r[0] for r in con.execute(
        "select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
    touched = 0
    leftovers: list[str] = []

    for table in tables:
        cols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % table)]
        rows = con.execute("select rowid, * from %s" % table).fetchall()
        for row in rows:
            rowid = row[0]
            updates = []
            for idx, col in enumerate(cols, start=1):
                val = row[idx]
                if not isinstance(val, str):
                    continue
                new = scrub(val)
                if new != val:
                    updates.append((col, new))
                    leftovers.extend(residual(new))
            if updates and not args.check:
                con.execute(
                    "update %s set %s where rowid=?" % (table, ", ".join("%s=?" % c for c, _ in updates)),
                    [v for _, v in updates] + [rowid],
                )
                touched += 1

    if args.check:
        # 检查模式下不写库，只统计当前库里的残留
        for table in tables:
            cols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % table)]
            for row in con.execute("select * from %s" % table):
                for i, col in enumerate(cols):
                    if isinstance(row[i], str):
                        leftovers.extend(residual(row[i]))
        con.close()
        uniq = sorted(set(leftovers))
        print("演示库残留 %d 项" % len(uniq))
        for item in uniq[:20]:
            print("  [残留] %s" % item)
        return 1 if uniq else 0

    con.commit()
    con.close()
    print("演示库脱敏完成：%d 行被改写" % touched)
    return 0


if __name__ == "__main__":
    sys.exit(main())
