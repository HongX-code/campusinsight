"""解析效果评测：把系统输出与人工标注逐字段比对，产出可量化指标。

用法：
    python scripts/evaluate.py            # 规则引擎（离线）
    python scripts/evaluate.py --llm      # 走已配置的大模型（需 .env 中填 KEY）

输出：
    data/evaluation/eval_result.json     详细逐条结果
    控制台打印 markdown 表格，可直接贴进测试报告
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.analyzer import analyze  # noqa: E402

DATASETS = {
    "dev": {
        "samples": ROOT / "data" / "sample_notices.json",
        "cases": ROOT / "data" / "evaluation" / "test_cases.json",
        "result": ROOT / "data" / "evaluation" / "eval_result.json",
        "md": ROOT / "docs" / "evaluation_result.md",
        "label": "开发集（规则在其上迭代过）",
    },
    "holdout": {
        "samples": ROOT / "data" / "evaluation" / "holdout_notices.json",
        "cases": ROOT / "data" / "evaluation" / "holdout_cases.json",
        "result": ROOT / "data" / "evaluation" / "holdout_result.json",
        "md": ROOT / "docs" / "holdout_result.md",
        "label": "留出集（未参与调参）",
    },
}

LIST_FIELDS = ("target", "conditions", "materials")


def _norm(s: str) -> str:
    return re.sub(r"[\s\u3000]", "", str(s))


def match_items(pred: list[str], gold: list[str]) -> tuple[int, int, int]:
    """双向包含匹配，返回 (tp, pred_len, gold_len)。"""
    used: set[int] = set()
    tp = 0
    for g in gold:
        gn = _norm(g)
        if not gn:
            continue
        for idx, p in enumerate(pred):
            if idx in used:
                continue
            pn = _norm(p)
            if gn in pn or pn in gn:
                used.add(idx)
                tp += 1
                break
    return tp, len(pred), len(gold)


def run(use_llm: bool, split: str = "dev") -> dict:
    conf = DATASETS[split]
    samples = {n["key"]: n for n in json.loads(conf["samples"].read_text(encoding="utf-8"))["notices"]}
    cases = json.loads(conf["cases"].read_text(encoding="utf-8"))["cases"]

    rows = []
    cat_ok = 0
    dl_ok = 0
    dl_gold_nonnull = 0
    dl_hit_nonnull = 0
    list_stat = {f: {"tp": 0, "pred": 0, "gold": 0} for f in LIST_FIELDS}
    conf_sum = 0.0
    review_count = 0

    for case in cases:
        key = case["key"]
        sample = samples[key]
        gold = case["gold"]
        pred = analyze(sample["title"], sample["content"], sample.get("publish_time", ""),
                       use_llm=use_llm, source_url=sample.get("source_url", ""))

        cat_hit = pred["category"] == gold["category"]
        cat_ok += int(cat_hit)

        dl_hit = (pred.get("deadline") or None) == (gold.get("deadline") or None)
        dl_ok += int(dl_hit)
        if gold.get("deadline"):
            dl_gold_nonnull += 1
            dl_hit_nonnull += int(dl_hit)

        field_hits = {}
        for field in LIST_FIELDS:
            tp, p_len, g_len = match_items(pred.get(field) or [], gold.get(field) or [])
            list_stat[field]["tp"] += tp
            list_stat[field]["pred"] += p_len
            list_stat[field]["gold"] += g_len
            field_hits[field] = tp

        conf_sum += pred.get("confidence", 0)
        review_count += int(pred.get("needs_review", False))

        rows.append({
            "key": key,
            "category": {"gold": gold["category"], "pred": pred["category"], "hit": cat_hit},
            "deadline": {"gold": gold.get("deadline"), "pred": pred.get("deadline"), "hit": dl_hit},
            **{f: {"gold": gold.get(f, []), "pred": pred.get(f, []), "tp": field_hits[f]} for f in LIST_FIELDS},
            "engine": pred.get("engine"),
            "confidence": pred.get("confidence"),
            "needs_review": pred.get("needs_review"),
        })

    total = len(cases)
    summary = {
        "engine": rows[0]["engine"] if rows else "rule",
        "cases": total,
        "category_accuracy": round(cat_ok / total, 4),
        "deadline_accuracy": round(dl_ok / total, 4),
        "deadline_recall_nonnull": round(dl_hit_nonnull / dl_gold_nonnull, 4) if dl_gold_nonnull else None,
        "avg_confidence": round(conf_sum / total, 4),
        "needs_review_rate": round(review_count / total, 4),
    }
    for field, st in list_stat.items():
        prec = st["tp"] / st["pred"] if st["pred"] else 0.0
        rec = st["tp"] / st["gold"] if st["gold"] else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        summary[f"{field}_precision"] = round(prec, 4)
        summary[f"{field}_recall"] = round(rec, 4)
        summary[f"{field}_f1"] = round(f1, 4)

    return {"summary": summary, "rows": rows}


def markdown(summary: dict) -> str:
    def pct(x):
        return "—" if x is None else f"{x * 100:.1f}%"

    lines = [
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 评测样本数 | {summary['cases']} |",
        f"| 解析引擎 | {'大模型' if summary['engine'] == 'llm' else '规则引擎'} |",
        f"| 分类准确率 | {pct(summary['category_accuracy'])} |",
        f"| 截止日期准确率（含空值判定） | {pct(summary['deadline_accuracy'])} |",
        f"| 截止日期召回率（仅非空样本） | {pct(summary['deadline_recall_nonnull'])} |",
        f"| 适用对象 精确率 / 召回率 / F1 | {pct(summary['target_precision'])} / {pct(summary['target_recall'])} / {pct(summary['target_f1'])} |",
        f"| 资格条件 精确率 / 召回率 / F1 | {pct(summary['conditions_precision'])} / {pct(summary['conditions_recall'])} / {pct(summary['conditions_f1'])} |",
        f"| 报名材料 精确率 / 召回率 / F1 | {pct(summary['materials_precision'])} / {pct(summary['materials_recall'])} / {pct(summary['materials_f1'])} |",
        f"| 平均置信度 | {summary['avg_confidence']:.2f} |",
        f"| 需人工复核比例 | {pct(summary['needs_review_rate'])} |",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", action="store_true", help="使用大模型解析（需配置 API Key）")
    parser.add_argument("--set", dest="split", choices=list(DATASETS), default="dev",
                        help="dev=开发集，holdout=留出集")
    args = parser.parse_args()
    conf = DATASETS[args.split]

    result = run(use_llm=args.llm or None, split=args.split)
    conf["result"].write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    table = markdown(result["summary"])
    print(f"== {conf['label']} ==")
    print(table)
    print(f"\n详细结果已写入：{conf['result'].relative_to(ROOT)}")

    conf["md"].write_text(
        "# 解析效果评测结果（自动生成）\n\n"
        f"> 生成时间：{__import__('datetime').datetime.now():%Y-%m-%d %H:%M}\n"
        f"> 数据集：{conf['label']}\n\n"
        f"{table}\n",
        encoding="utf-8",
    )
    print(f"表格已写入：{conf['md'].relative_to(ROOT)}")


if __name__ == "__main__":
    main()
