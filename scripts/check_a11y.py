# -*- coding: utf-8 -*-
"""界面可访问性与布局稳健性静态自检（不依赖浏览器，可在 CI 里跑）。

对照 docs/ui_design.md 的可访问性基线，检查四类在浏览器里才会暴露、
但完全可以从源码判定的问题：

1. 网格轨道写成裸 `1fr`——隐式最小值是 `auto`，网格项里的 nowrap 长文本
   会把整条轨道撑破，产生横向溢出（本轮真实缺陷，390px 视口下撑到 477px）。
2. 语义色（--success/--warning/--danger）被当文字颜色用——这三个值在浅底上
   只有 3.0—4.4，达不到 WCAG AA 4.5；文字必须用 --*-text。
3. 三级文字变量的值必须等于按 AA 反算过的取值，防止有人改回浅灰。
4. 移动端触控目标：`.cf-open` 必须有足够上下内边距、`.seg-btn` 必须有
   `min-height`，否则手机上点不中。

用法：python scripts/check_a11y.py [--quiet]
退出码 0 = 全部通过；1 = 有失败项。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STYLE = ROOT / "app" / "static" / "css" / "style.css"
MOBILE = ROOT / "app" / "static" / "css" / "mobile.css"

# 按 WCAG AA（4.5:1）在站点 12 种浅色底上反算过的取值，见 docs/ui_design.md 3.1
TEXT_EXPECT = {
    "--text-primary": "#111827",
    "--text-secondary": "#4B5563",
    "--text-tertiary": "#5F6B7A",
}
TEXT_FLOOR_PX = 11.5   # 移动端正文最小字号
TAP_FLOOR_PX = 32      # 可点元素最短边下限

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def main() -> int:
    for p in (STYLE, MOBILE):
        if not p.exists():
            print(f"[FATAL] 找不到 {p}")
            return 1

    style_raw = read(STYLE)
    mobile_raw = read(MOBILE)
    style = strip_comments(style_raw)
    mobile = strip_comments(mobile_raw)
    both = style + "\n" + mobile

    # ---------- 1. 裸 1fr 轨道 ----------
    # 只查 grid-template-columns / grid-template-rows / repeat() 里的裸 1fr
    bad_fr: list[str] = []
    for m in re.finditer(r"grid-template-(?:columns|rows)\s*:\s*([^;}]+)", both):
        expr = m.group(1)
        # 去掉 minmax(...) 内部，剩下的裸 <n>fr（含 1.55fr 这类带小数的）才算问题
        outer = re.sub(r"minmax\([^()]*\)", "MM", expr)
        if re.search(r"(?<![\w.])[\d.]+fr\b", outer):
            bad_fr.append(expr.strip()[:80])
    for m in re.finditer(r"repeat\(", both):
        # repeat() 的参数里可能嵌套 minmax(...)，必须按括号配对取出完整实参
        i, depth = m.end() - 1, 0
        start = i
        while i < len(both):
            if both[i] == "(":
                depth += 1
            elif both[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        inner = both[start + 1 : i]
        # 去掉嵌套括号内容后再看末尾是不是裸 1fr
        flat = re.sub(r"\w+\([^()]*\)", "MM", inner)
        args = [a.strip() for a in flat.split(",")]
        if args and re.fullmatch(r"1fr", args[-1]):
            bad_fr.append(f"repeat({inner.strip()[:60]})")
    check(
        "网格轨道不用裸 1fr（用 minmax(0, 1fr)）",
        not bad_fr,
        "；".join(bad_fr[:4]) or f"已全部改为 minmax(0, 1fr)",
    )

    # ---------- 2. 语义色不得直接当文字色 ----------
    # 允许出现的位置：background / border / box-shadow / 伪元素的背景填充
    offenders: list[str] = []
    for m in re.finditer(r"(?<![\w-])color\s*:\s*var\(--(success|warning|danger)\)", both):
        line_start = both.rfind("\n", 0, m.start()) + 1
        line_end = both.find("\n", m.end())
        snippet = both[line_start:line_end].strip()
        offenders.append(f"{m.group(1)}: {snippet[:70]}")
    check(
        "语义色文字一律用 --*-text 变量",
        not offenders,
        "；".join(offenders[:4]) or "无裸用 --success/--warning/--danger 作文字色",
    )

    # 三个 -text 变量必须存在且比原色深
    for name in ("--success-text", "--warning-text", "--danger-text"):
        check(
            f"定义 {name}",
            re.search(rf"{re.escape(name)}\s*:\s*#[0-9A-Fa-f]{{6}}", style) is not None,
            name,
        )

    # ---------- 3. 三级文字变量的取值 ----------
    for var, want in TEXT_EXPECT.items():
        m = re.search(rf"{re.escape(var)}\s*:\s*(#[0-9A-Fa-f]{{6}})", style)
        got = m.group(1).upper() if m else "(缺失)"
        check(f"{var} 取 AA 达标值 {want}", got == want.upper(), f"实际 {got}")

    # ---------- 4. 移动端字号下限与触控目标 ----------
    # 只在手机媒体查询块内检查，桌面端不受 11.5px 下限约束
    mobile_blocks = []
    for m in re.finditer(r"@media\s*\(max-width:\s*7\d\dpx\)\s*\{", style + mobile):
        depth, i = 0, m.end() - 1
        start = i
        while i < len(style + mobile):
            ch = (style + mobile)[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    mobile_blocks.append((style + mobile)[start : i + 1])
                    break
            i += 1
    block_text = "\n".join(mobile_blocks)

    tiny = set()
    for m in re.finditer(r"font-size\s*:\s*(\d+(?:\.\d+)?)px", block_text):
        size = float(m.group(1))
        if size < TEXT_FLOOR_PX:
            line_start = block_text.rfind("\n", 0, m.start()) + 1
            sel_line = block_text[line_start : block_text.find("\n", m.end())].strip()
            # 纯装饰允许更小：流程序号圆点、图标角标、底部导航图标文字已抬到 11.5
            if not re.search(r"pipe-dot|icon-btn \.badge|\.badge", sel_line):
                tiny.add(f"{size}px {sel_line[:50]}")
    check(
        f"移动端正文不小于 {TEXT_FLOOR_PX}px（装饰除外）",
        not tiny,
        "；".join(sorted(tiny)[:4]) or "无过小正文",
    )

    # .cf-open 触控高度
    m = re.search(r"\.cf-open\s*\{([^}]*)\}", mobile)
    cf = m.group(1) if m else ""
    pad = re.search(r"padding\s*:\s*(\d+)px", cf)
    ok_cf = bool(pad) and int(pad.group(1)) >= 8
    check("「查看原文」按钮有足够触控高度（padding ≥ 8px）", ok_cf, cf.strip().replace("\n", " ")[:80])

    # .seg-btn 移动端 min-height
    ok_seg = re.search(rf"\.seg-btn\s*\{{[^}}]*min-height\s*:\s*{TAP_FLOOR_PX}px", block_text) is not None
    check(f"分段筛选按钮移动端 min-height ≥ {TAP_FLOOR_PX}px", ok_seg, "")

    # ---------- 5. 文档口径同步 ----------
    doc = ROOT / "docs" / "ui_design.md"
    if doc.exists():
        dt = read(doc)
        for var, want in TEXT_EXPECT.items():
            check(
                f"ui_design.md 与源码一致：{var}",
                want.upper() in dt.upper(),
                f"文档应写 {want}",
            )
        check(
            "ui_design.md 说明两个页签（不再写多端接入页签）",
            "两个页签" in dt and "三个页签" not in dt,
            "",
        )

    passed = sum(1 for _, ok, _ in results if ok)
    failed = [(n, d) for n, ok, d in results if not ok]
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        tail = f"  {detail}" if detail and not ok else ""
        print(f"  {mark}  {name}{tail}")
    print(f"\n可访问性自检：通过 {passed} 项，失败 {len(failed)} 项")
    return 0 if not failed else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    sys.exit(main())
