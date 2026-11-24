# -*- coding: utf-8 -*-
"""把 docs/benchmark.md 的现场实测值回灌到全部 Markdown 材料。

    python scripts/benchmark.py     # 重新计时，生成 docs/benchmark.md
    python scripts/sync_metrics.py  # 把报告里的数字同步到 README / 测试报告 / 视频脚本

背景：benchmark.py 每次运行都重新计时，数字会随机波动。若文档里手写指标，
材料与报告必然脱节。本脚本让 benchmark.md 成为唯一数据源，可反复运行（幂等）。
申报材料 build_ppt.py / fill_form.py 已在生成时直接读取报告，无需在此处理。

每条规则绑定一个目标文件，避免跨文件误替换；规则用结构锚点匹配任意旧数字，
因此不依赖上一轮的值。新增指标时，在 build_rules() 里追加一条即可。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "docs" / "benchmark.md"

TARGETS = {
    "README.md": 4,
    "docs/test_report.md": 6,
    "docs/video_script.md": 5,
}


def read_report() -> dict[str, str]:
    text = REPORT.read_text(encoding="utf-8")

    def cell(label: str, unit: str) -> str:
        m = re.search(r"\|\s*" + re.escape(label) + r"\s*\|\s*([\d.]+)\s*" + re.escape(unit) + r"\s*\|", text)
        if not m:
            raise SystemExit(f"benchmark.md 缺少指标：{label}（{unit}）")
        return m.group(1)

    def api(label: str) -> str:
        m = re.search(r"\|\s*" + re.escape(label) + r"\s*\|\s*([\d.]+) ms\s*\|", text)
        if not m:
            raise SystemExit(f"benchmark.md 缺少接口平均耗时：{label}")
        return m.group(1)

    return {
        "median": cell("中位数", "ms"),
        "avg": cell("平均值", "ms"),
        "p95": cell("P95", "ms"),
        "slowest": cell("最慢", "ms"),
        "batch": cell("总耗时", "s"),
        "per_item": cell("平均每条", "ms"),
        "throughput": cell("吞吐量", "条/秒"),
        "health": api("GET /api/health"),
        "notices": api("GET /api/notices?size=20"),
        "dashboard": api("GET /api/dashboard/stats"),
        "db": api("SELECT COUNT(*) FROM notices"),
    }


def build_rules(m: dict[str, str]) -> list[tuple[str, str, str, object]]:
    """返回 (目标文件, 说明, 正则, 替换函数)。正则锚定结构、数字用通配，故可幂等重跑。"""
    n = r"[\d.]+"
    R, T, V = "README.md", "docs/test_report.md", "docs/video_script.md"
    rules: list[tuple[str, str, str, object]] = [
        (R, "单条耗时三值",
         r"(\| 单条解析中位数 / 平均 / P95 \| )" + n + r" ms / " + n + r" ms / " + n + r" ms",
         lambda g: g.group(1) + f"{m['median']} ms / {m['avg']} ms / {m['p95']} ms"),
        (R, "批量",
         r"(\| 100 条连续解析 \| )" + n + r" s，吞吐 " + n + r" 条/秒",
         lambda g: g.group(1) + f"{m['batch']} s，吞吐 {m['throughput']} 条/秒"),
        (R, "API 三接口",
         r"(\| API（TestClient 进程内） \| `/api/health` )" + n + r"( ms、`/api/notices\?size=20` )" + n
         + r"( ms、`/api/dashboard/stats` )" + n + r" ms",
         lambda g: g.group(1) + m["health"] + g.group(2) + m["notices"] + g.group(3) + m["dashboard"] + " ms"),
        (R, "数据库查询",
         r"(\| 数据库 `SELECT COUNT\(\*\)` \| )" + n + r" ms",
         lambda g: g.group(1) + m["db"] + " ms"),

        (T, "单条耗时四值",
         r"(\| 单条解析耗时 \| 中位数 \*\*)" + n + r"( ms\*\* / 平均 )" + n + r"( ms / P95 )" + n
         + r"( ms / 最慢 )" + n + r" ms",
         lambda g: g.group(1) + m["median"] + g.group(2) + m["avg"] + g.group(3) + m["p95"]
                   + g.group(4) + m["slowest"] + " ms"),
        (T, "批量",
         r"(\| 100 条连续解析 \| )" + n + r"( s，平均 )" + n + r"( ms/条，吞吐 \*\*)" + n + r" 条/秒",
         lambda g: g.group(1) + m["batch"] + g.group(2) + m["per_item"] + g.group(3) + m["throughput"] + " 条/秒"),
        (T, "API 三接口",
         r"(\| API（TestClient 进程内，20 次） \| `/api/health` )" + n + r"( ms、`/api/notices\?size=20` )" + n
         + r"( ms、`/api/dashboard/stats` )" + n + r" ms",
         lambda g: g.group(1) + m["health"] + g.group(2) + m["notices"] + g.group(3) + m["dashboard"] + " ms"),
        (T, "数据库查询",
         r"(\| 数据库查询 \| `SELECT COUNT\(\*\) FROM notices` 平均 )" + n + r" ms",
         lambda g: g.group(1) + m["db"] + " ms"),
        (T, "结论段",
         r"(处理效率由脚本现场实测：单条解析中位数 )" + n + r"( ms、100 条 )" + n + r"( s、)" + n + r" 条/秒",
         lambda g: g.group(1) + m["median"] + g.group(2) + m["batch"] + g.group(3) + m["throughput"] + " 条/秒"),

        (V, "操作行三值",
         r"(停在「中位数 )" + n + r"( ms / 平均 )" + n + r"( ms / P95 )" + n + r" ms",
         lambda g: g.group(1) + m["median"] + g.group(2) + m["avg"] + g.group(3) + m["p95"] + " ms"),
        (V, "操作行批量",
         r"(移到「100 条 )" + n + r"( s、)" + n + r" 条/秒",
         lambda g: g.group(1) + m["batch"] + g.group(2) + m["throughput"] + " 条/秒"),
        (V, "旁白三值",
         r"(单条解析中位数 )" + n + r"( 毫秒，平均 )" + n + r"( 毫秒，P95 )" + n + r" 毫秒",
         lambda g: g.group(1) + m["median"] + g.group(2) + m["avg"] + g.group(3) + m["p95"] + " 毫秒"),
        (V, "旁白批量",
         r"(连续解析 100 条用 )" + n + r"( 秒，约合 )" + n + r" 条每秒",
         lambda g: g.group(1) + m["batch"] + g.group(2) + m["throughput"] + " 条每秒"),
        (V, "字幕",
         r"(单条解析中位数 )" + n + r" ms",
         lambda g: g.group(1) + m["median"] + " ms"),
    ]
    return rules


def main() -> int:
    metrics = read_report()
    rules = build_rules(metrics)
    print("实测值：中位数 %s ms / 平均 %s ms / P95 %s ms / 100 条 %s s / %s 条每秒"
          % (metrics["median"], metrics["avg"], metrics["p95"], metrics["batch"], metrics["throughput"]))

    total = 0
    missing: list[str] = []
    for rel, expected in TARGETS.items():
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        original = text
        own = [r for r in rules if r[0] == rel]
        hit = 0
        for _, name, pattern, repl in own:
            text, count = re.subn(pattern, repl, text)  # 全量：同锚点第二处不得静默留旧值
            if count:
                hit += 1
                if count > 1:
                    print(f"  ! {rel} :: {name} 同锚点 {count} 处，已全部同步")
            else:
                missing.append(f"{rel} :: {name}")
        if text != original:
            path.write_text(text, encoding="utf-8")
        print(f"  {rel}: 命中 {hit}/{len(own)} 处")
        total += hit

    if missing:
        print("未命中的规则（请检查文档结构是否改动，或该规则已被上一条覆盖）：")
        for item in missing:
            print("  -", item)
        return 1
    print(f"完成，共同步 {total} 处；指标唯一数据源为 docs/benchmark.md。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
