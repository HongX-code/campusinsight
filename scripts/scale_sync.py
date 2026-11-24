# -*- coding: utf-8 -*-
"""工程规模与验证口径的现场采集 + 单一数据源回灌。

    python scripts/scale_sync.py --check     # 只核对规则能否命中，不写文件
    python scripts/scale_sync.py             # 按 docs/metrics.json 回灌全部口径

背景：源码行数、自检项数这类数字以前靠人工同步，多次出现口径漂移，
最后连「容差只剩几行」都成了隐患。本模块让 docs/metrics.json
成为唯一数据源：evidence.py 现场采集 → 本模块按结构锚点回灌 → verify 现场复核。
历史叙述（说明某一轮从旧项数扩到新项数）刻意不改，只同步「当前状态」表述。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
METRICS_JSON = ROOT / "docs" / "metrics.json"

SCALE_TARGETS = (
    "README.md", "docs/architecture.md", "docs/codearts_prompts.md",
    "docs/design_compliance.md", "docs/requirements.md", "docs/test_report.md",
    "scripts/ci_gate.py", "codearts/CodeArts实操作战台.html",
    "申报材料/build_ppt.py", "申报材料/fill_form.py",
)

FRONT_FILES = {
    "html_lines": "app/templates/index.html",
    "js_lines": "app/static/js/app.js",
    "css_lines": "app/static/css/style.css",
}


def collect_scale() -> dict:
    """现场统计，不读缓存，保证任何时刻调用都反映磁盘真实状态。"""
    def lines(p: Path) -> int:
        return len(p.read_text(encoding="utf-8").splitlines())

    def py_in(folder: str) -> int:
        return sum(lines(p) for p in (ROOT / folder).rglob("*.py")
                   if "__pycache__" not in p.parts)

    front = {key: lines(ROOT / rel) for key, rel in FRONT_FILES.items()}
    py_total = sum(py_in(f) for f in ("app", "scripts", "tests"))
    endpoints = sum(
        len(re.findall(r"@(?:app|router)\.(?:get|post|put|patch|delete)\b",
                       p.read_text(encoding="utf-8")))
        for p in (ROOT / "app").rglob("*.py") if "__pycache__" not in p.parts
    )
    return {
        "python_lines": py_total,
        "frontend_lines": sum(front.values()),
        "total_lines": py_total + sum(front.values()),
        "scripts_lines": py_in("scripts"),
        **front,
        "endpoints": endpoints,
        "docs_md": len(list((ROOT / "docs").glob("*.md"))),
        "screenshots": len(list((ROOT / "docs").glob("shot-*.png"))),
        "views": 7,
    }


def num_rule(rel: str, name: str, before: str, after: str, value: int):
    """(文件, 说明, 正则, 替换)：把 before 与 after 之间的数字改成 value。"""
    pattern = "(" + re.escape(before) + r")\d{2,5}(" + re.escape(after) + ")"
    return (rel, name, pattern,
            lambda g, v=str(value): g.group(1) + v + g.group(2))


def build_rules(scale: dict, checks: dict) -> list[tuple]:
    n = int(checks["smoke_passed"])
    e = int(scale["endpoints"])
    total_approx = round(int(scale["total_lines"]) / 100) * 100
    py_approx = round(int(scale["python_lines"]) / 100) * 100
    front = int(scale["frontend_lines"])
    h, j, c = int(scale["html_lines"]), int(scale["js_lines"]), int(scale["css_lines"])
    R, A, C, D, Q, T = ("README.md", "docs/architecture.md", "docs/codearts_prompts.md",
                        "docs/design_compliance.md", "docs/requirements.md", "docs/test_report.md")
    G, H, P, F = ("scripts/ci_gate.py", "codearts/CodeArts实操作战台.html",
                  "申报材料/build_ppt.py", "申报材料/fill_form.py")
    return [
        num_rule(R, "目录树自检项数", "端到端自检（", " 项，采集用例离线可跑）", n),
        num_rule(R, "命令自检项数", "# ", " 项端到端自检（无需联网）", n),
        num_rule(A, "规模总行数", "源码约 **", " 行**", total_approx),
        num_rule(A, "Python 明细", "（Python ", " + 前端 ", py_approx),
        num_rule(A, "前端明细", "+ 前端 ", "，其中 HTML", front),
        num_rule(A, "HTML 明细", "其中 HTML ", " / JS ", h),
        num_rule(A, "JS 明细", " / JS ", " / CSS ", j),
        num_rule(A, "CSS 明细", " / CSS ", "）", c),
        num_rule(A, "路由端点数", "、**", " 个路由端点**", e),
        num_rule(A, "自检项数", "端到端自检 **", " 项全通过", n),
        num_rule(A, "文档份数", "Markdown 文档 **", " 份**", int(scale["docs_md"]) + 1),
        num_rule(A, "docs 份数明细", "（`docs/` 下 ", " 份 + 根目录 README",
                 int(scale["docs_md"])),
        num_rule(C, "P0 规模总行数", "规模：约 ", " 行源码（Python ", total_approx),
        num_rule(C, "规模段 Python", " 行源码（Python ", " + 前端 ", py_approx),
        num_rule(C, "口径清单总行数", "约 ", " 行源码（Python ", total_approx),
        num_rule(C, "全站端点", "5 个路由模块，全站 ", " 个端点", e),
        num_rule(C, "端点清单", "- ", " 个路由端点、", e),
        num_rule(C, "前端明细", "+ 前端 ", "）；", front),
        num_rule(C, "前端明细（顿号）", "+ 前端 ", "）、", front),
        num_rule(C, "P0 当前自检", "当前 ", " 项断言全通过", n),
        num_rule(C, "P4 影响面", "是否影响现有 ", " 项自检", n),
        num_rule(C, "P5 门禁链", "compileall）→ ", " 项自检 → 开发集评测", n),
        num_rule(C, "P2 复盘提问", "为什么 ", " 项自检没抓到", n),
        num_rule(C, "口径清单自检项", "、", " 项自检、约 ", n),
        num_rule(D, "规模总行数", "约 **", " 行源码**", total_approx),
        num_rule(D, "Python 明细", "（Python ", " + 前端 ", py_approx),
        num_rule(D, "前端明细", "+ 前端 ", "，不含文档", front),
        num_rule(D, "规模段端点", "当前规模：**", " 个路由端点**", e),
        num_rule(D, "端点能力", "| ", " 个路由端点，OpenAPI 自动生成 |", e),
        num_rule(D, "端点接口", "| ", " 个路由端点 | `docs/api.md`", e),
        num_rule(D, "自检项数", "**", " 项端到端自检全通过**", n),
        num_rule(D, "文档份数", "Markdown 文档 **", " 份**", int(scale["docs_md"]) + 1),
        num_rule(D, "docs 份数明细", "（`docs/` 下 ", " 份 + 根目录 README",
                 int(scale["docs_md"])),
        num_rule(Q, "需求验收", "、", " 项自动化自检与处理效率实测报告", n),
        num_rule(T, "执行结果", "结果 **", " 项全部通过，失败 0 项**", n),
        num_rule(T, "离线说明", "全部 ", " 项无需联网、无需大模型密钥", n),
        num_rule(T, "缺陷复盘现值", "现为 ", " 项）", n),
        num_rule(T, "F1 说明", "其正确性由 ", " 项自检中的 [13] 组", n),
        num_rule(T, "结论段", "**", " 项自检全部通过**", n),
        num_rule(T, "复现命令", "# 端到端自检（", " 项，离线可跑）", n),
        num_rule(G, "门禁阶段说明", '"服务层自检", [PY, "tests/test_smoke.py"], "',
                 " 项端到端断言", n),
        num_rule(H, "作战台提示词", "为什么 ", " 项自检没抓到", n),
        num_rule(P, "工程实现页副标题", "、", " 项自检，一周跑通全链路", n),
        num_rule(P, "工程实现页正文", "③ ", " 项自检里 27 项是这轮新增的", n),
        num_rule(P, "工程实现页表格", "构建门禁 + ", ' 项自检"', n),
        num_rule(P, "标题总行数", '"约 ', " 行源码、", total_approx),
        num_rule(P, "标题端点数", " 行源码、", " 个路由端点", e),
        num_rule(P, "架构页端点数", "FastAPI ", " 个路由端点 · Pydantic 校验", e),
        num_rule(P, "工程表格端点数", 'SQLAlchemy 模型", "', ' 个路由端点"]', e),
        num_rule(P, "工程页自检断言", "证明 ", " 项断言确实有效", n),
        (P, "指标卡自检数", r'"\d+\s*/\s*\d+",\s*"端到端自检',
         lambda g, v=str(n): '"' + v + ' / ' + v + '", "端到端自检'),
        num_rule(P, "工程文档份数", '"', ' 份 Markdown"],', int(scale["docs_md"]) + 1),
        num_rule(F, "代码生成规模", "项目现有约 ", " 行源码、", total_approx),
        num_rule(F, "代码生成端点", " 行源码、", " 个路由端点", e),
        num_rule(F, "问题修复自检", "补上回归用例，", " 项端到端自检脚本可一键复现", n),
        num_rule(F, "评测说明自检", "闸门拦下 25 个假字段。", " 项端到端自检覆盖录入去重", n),
    ]


def path_of(rel: str) -> Path:
    return (ROOT.parent.parent / rel) if rel.startswith("申报材料/") else (ROOT / rel)


def apply_scale(verbose: bool = True, dry_run: bool = False) -> tuple[int, list[str]]:
    """返回 (有变更的文件数, 未命中规则列表)。"""
    if not METRICS_JSON.exists():
        return 0, ["docs/metrics.json 不存在，请先跑 python scripts/evidence.py --collect"]
    data = json.loads(METRICS_JSON.read_text(encoding="utf-8"))
    rules = build_rules(data["scale"], data["checks"])
    missing: list[str] = []
    changed = 0
    for rel in SCALE_TARGETS:
        path = path_of(rel)
        if not path.exists():
            # 交付件（申报材料/*.py）可能不在当前工作区副本里：如实报告缺口，
            # 但不要因为一份材料缺失就中断其余文档的回灌。
            missing.append(f"{rel} :: 文件不存在（已跳过）")
            if verbose:
                print(f"  {rel}: 文件不存在，已跳过")
            continue
        text = path.read_text(encoding="utf-8")
        original = text
        own = [r for r in rules if r[0] == rel]
        hit = 0
        for _, name, pattern, repl in own:
            text, count = re.subn(pattern, repl, text)
            if count:
                hit += 1
                if count > 1 and verbose:
                    print(f"  ! {rel} :: {name} 同锚点 {count} 处，已全部同步")
            else:
                missing.append(f"{rel} :: {name}")
        if text != original:
            if dry_run:
                changed += 1
            else:
                try:
                    path.write_text(text, encoding="utf-8")
                    changed += 1
                except OSError as exc:
                    # 工作区外的交付件可能被只读挂载或权限限制：如实报告，不中断其余文件。
                    missing.append(f"{rel} :: 写入失败（{exc.strerror or exc}），已跳过")
        if verbose:
            tag = "（待更新）" if dry_run and text != original else ("（已更新）" if text != original else "")
            print(f"  {rel}: 命中 {hit}/{len(own)} 处{tag}")
    return changed, missing


def main() -> int:
    ap = argparse.ArgumentParser(description="按 docs/metrics.json 回灌工程规模口径")
    ap.add_argument("--check", action="store_true", help="只体检规则命中情况，不写文件")
    ap.add_argument("--collect", action="store_true",
                    help="现场重算规模并写回 docs/metrics.json（不跑自检 / 变异，项数保留原值）")
    args = ap.parse_args()

    if args.collect:
        data = json.loads(METRICS_JSON.read_text(encoding="utf-8"))
        data["scale"] = collect_scale()
        data["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        METRICS_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
        print(f"已重算规模：{data['scale']}")
        print("注意：自检项数与变异结果保留原值，需要更新时请跑 evidence.py --collect。")

    print("=== 规模口径回灌（数据源 docs/metrics.json） ===")
    changed, missing = apply_scale(dry_run=args.check)
    if missing:
        print("未命中的规则：")
        for item in missing:
            print("  -", item)
        return 1
    print(f"完成：全部规则命中，{changed} 个文件{'需要更新' if args.check else '已同步'}。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
