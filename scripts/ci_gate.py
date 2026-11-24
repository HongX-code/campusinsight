"""构建与质量门禁（对应 docs/codearts_prompts.md 的 P5 阶段表）。

用法（在工程根目录执行）：
    python scripts/ci_gate.py               # 依次执行全部阶段，任一步失败立即中止
    python scripts/ci_gate.py --dry-run     # 只打印阶段表，适合 Day 1 核对配置
    python scripts/ci_gate.py --from 3      # 从第 3 阶段开始（调试单个阶段时用）

约定：
- 不引入新依赖、不吞退出码；每个阶段独立子进程，失败时打印该阶段最后 40 行输出；
- 命令用 sys.executable，Linux 构建机可直接跑；`node` 缺失记为 SKIP 并明确写出原因，
  因为它属于环境缺失而非代码缺陷；
- 门禁只覆盖本地可验证项，云资源未开通不应阻塞构建（见 docs/codearts_prompts.md §4.2 Day 6）；
- 第 1 阶段先做环境体检：本机 `python` 可能指向未装依赖的版本，宁可早失败并给出正确前缀，
  也不要让后面 8 个阶段用错解释器、报出一串看不懂的 ModuleNotFoundError。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

STAGES: list[tuple[str, list[str], str]] = [
    ("环境体检", [PY, "scripts/codearts_tools.py", "doctor", "--strict"], "用错解释器时立即给出可用前缀"),
    ("语法编译", [PY, "-m", "compileall", "-q", "app", "scripts", "tests"], "Python 全量编译"),
    ("服务层自检", [PY, "tests/test_smoke.py"], "143 项端到端断言"),
    ("开发集评测", [PY, "scripts/evaluate.py"], "五项指标不得退化"),
    ("留出集评测", [PY, "scripts/evaluate.py", "--set", "holdout"], "泛化能力不得退化"),
    ("真实集审计", [PY, "scripts/real_audit.py"], "42 份真实快照解析质量"),
    ("前端语法", ["node", "--check", "app/static/js/app.js"], "node 缺失则 SKIP"),
    ("提示词一致性", [PY, "scripts/codearts_tools.py", "split", "--check"], "独立提示词 = 主文档"),
    ("交付件体检", [PY, "scripts/codearts_tools.py", "verify", "--strict"], "口径 / 旧值 / 页数 / 资产 / 禁止 SKIP"),
]


def run(start: int, dry_run: bool) -> int:
    print("=== 高校智讯 · 构建与质量门禁 ===")
    print(f"工作目录：{ROOT}")
    if dry_run:
        for i, (name, cmd, note) in enumerate(STAGES, 1):
            shown = [("python" if part == PY else part) for part in cmd]
            print(f"  {i}. {name:<12} {note:<24} $ {' '.join(shown)}")
        print("\n[dry-run] 未执行任何命令。去掉 --dry-run 即按顺序运行。")
        return 0

    summary: list[tuple[str, str, float, int]] = []
    for i, (name, cmd, note) in enumerate(STAGES, 1):
        if i < start:
            summary.append((name, "SKIP", 0.0, 0))
            continue
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
        except FileNotFoundError:
            elapsed = time.perf_counter() - t0
            print(f"  [{i}/{len(STAGES)}] {name:<12} SKIP  （{cmd[0]} 不存在，属环境缺失；{note}）")
            summary.append((name, "SKIP", elapsed, 0))
            continue

        elapsed = time.perf_counter() - t0
        if proc.returncode == 0:
            print(f"  [{i}/{len(STAGES)}] {name:<12} PASS  {elapsed:5.1f}s")
            summary.append((name, "PASS", elapsed, 0))
            continue

        print(f"  [{i}/{len(STAGES)}] {name:<12} FAIL  {elapsed:5.1f}s  退出码 {proc.returncode}")
        tail = (proc.stdout or "") + "\n" + (proc.stderr or "")
        lines = [ln for ln in tail.splitlines() if ln.strip()]
        for ln in lines[-40:]:
            print("      |", ln)
        summary.append((name, "FAIL", elapsed, proc.returncode))
        print(f"\n门禁在第 {i} 阶段「{name}」中止，后续阶段未执行。")
        print("修复后重跑：python scripts/ci_gate.py")
        return proc.returncode or 1

    print("\n=== 阶段摘要 ===")
    for name, status, elapsed, code in summary:
        print(f"  {status:<4} {name:<12} {elapsed:5.1f}s" + (f"  退出码 {code}" if code else ""))
    total = sum(x[2] for x in summary)
    failed = [x for x in summary if x[0] == "FAIL"]
    skipped = [x for x in summary if x[0] == "SKIP"]
    print(f"\n全部 {len(summary)} 阶段通过（跳过 {len(skipped)}），耗时 {total:.1f}s。")
    if skipped:
        print("跳过项：" + "、".join(x[0] for x in skipped) + "（如需严格门禁，请在构建机安装对应运行时）")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="构建与质量门禁")
    ap.add_argument("--dry-run", action="store_true", help="只打印阶段表")
    ap.add_argument("--from", dest="start", type=int, default=1, help="从指定阶段序号开始")
    args = ap.parse_args()
    if not 1 <= args.start <= len(STAGES):
        ap.error(f"--from 必须在 1—{len(STAGES)} 之间")
    return run(args.start, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
