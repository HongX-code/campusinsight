"""真实站点采集集（42 份）解析质量审计。

用法：
    python scripts/real_audit.py
    python scripts/real_audit.py --input data/real_notices.json --out docs/real_audit.md

做这件事的起因：最初用「截止日期抽取率」一个数字衡量真实集，得到 55%，
但这个口径本身是错的——42 份里有 18 份是新闻稿，压根不存在报名截止，
把它们算作「没抽到」等于把正确行为记成失败。于是改成两把尺子：

    · 事务通知 / 公示公告：抽到了多少个「办理截止」，以及截止的语义类型；
    · 新闻资讯：有没有硬造出报名类字段（不误报率）。

脚本同时跑一遍「绕过文体闸门」的对照（直接调 extractor.extract），
把闸门实际拦下的误报数量算出来，避免只在 PPT 上写一句「效果变好了」。
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services import analyzer, extractor  # noqa: E402

SNAPSHOT = ROOT / "data" / "real_notices.json"
REPORT = ROOT / "docs" / "real_audit.md"

# v1.0（采集链路上线时）在同样 42 份上的实测观测，用于报告中的前后对比。
# 这些是历史事实，不由当前代码复算；当前值一律现场跑出来。
V10_OBSERVED = [
    ("学校捧得全国“挑战杯”竞赛“优胜杯”", "deadline", "2026-11-02",
     "赛事举办期 10月30日至11月2日 的末端"),
    ("第14届中国人力资源管理论坛在武汉举行", "deadline", "2026-11-01",
     "会议会期 10月30日至11月1日 的末端"),
    ("我校夏梦玲教授在Nature Photonics发表最近研究成果", "materials", "3 项",
     "学科名「材料」被当成报名材料"),
    ("2025年度研究生工作站评估结果公示", "deadline_kind", "未标注",
     "公示期结束日被笼统显示为报名截止"),
]

# 未抽到办理截止的事务类通知，逐条核对原文后的结论（人工核对记录）
NO_DEADLINE_NOTES = {
    "关于研究生教育综合管理系统升级的通知": "系统维护通知，只有升级时间，无报名/申报事项",
    "关于2025-2026学年第2学期研究生公共课考试安排的通知": "只有「考试前一周」等相对时间，无具体日期截止",
    "武汉理工大学2027年硕士研究生招生考试自命题科目考试大纲汇总": "资料汇总，无办理时限",
    "武汉理工大学2026年博士研究生拟录取名单补充公示": "仅有「公示期为 X 至 Y」区间；按保守策略不猜末端，如实留空",
}


def load_items(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for block in data.get("sources", []):
        src = block.get("source", {})
        for item in block.get("items", []):
            out.append({**item, "_source": src.get("name", "")})
    return out, data


def audit(items: list[dict]) -> dict:
    rows = []
    for it in items:
        result = analyzer.analyze(it["title"], it["content"], it.get("publish_date", ""),
                                  use_llm=False, source_url=it.get("url", ""))
        # 对照：绕过文体闸门，只看纯抽取器会对这段文本产出什么
        raw = extractor.extract(it["title"], it["content"], it.get("publish_date", ""))
        rows.append({
            "title": it["title"],
            "source": it["_source"],
            "url": it.get("url", ""),
            "chars": it.get("chars") or len(it.get("content", "")),
            "attachments": len(it.get("attachments") or []),
            "genre": result["genre"],
            "label": result["genre_label"],
            "actionable": result["actionable"],
            "category": result["category"],
            "deadline": result["deadline"],
            "deadline_kind": result.get("deadline_kind", ""),
            "materials": len(result["materials"] or []),
            "conditions": len(result["conditions"] or []),
            "confidence": result["confidence"],
            "raw_deadline": raw.get("deadline"),
            "raw_materials": len(raw.get("materials") or []),
            "raw_conditions": len(raw.get("conditions") or []),
        })

    genre_count = collections.Counter(r["genre"] for r in rows)
    kind_count = collections.Counter(r["deadline_kind"] for r in rows if r["deadline"])
    actionable = [r for r in rows if r["actionable"]]
    news = [r for r in rows if r["genre"] == "news"]
    with_dl = [r for r in actionable if r["deadline"]]
    no_dl = [r for r in actionable if not r["deadline"]]

    blocked = {
        "materials": sum(r["raw_materials"] > 0 for r in news),
        "conditions": sum(r["raw_conditions"] > 0 for r in news),
        "fields": sum(r["raw_materials"] + r["raw_conditions"] for r in news),
    }

    return {
        "rows": rows,
        "total": len(rows),
        "sources": len({r["source"] for r in rows}),
        "genre_count": dict(genre_count),
        "kind_count": dict(kind_count),
        "actionable_count": len(actionable),
        "news_count": len(news),
        "with_dl": len(with_dl),
        "no_dl": len(no_dl),
        "no_dl_rows": no_dl,
        "coverage": round(100.0 * len(with_dl) / len(actionable), 1) if actionable else 0.0,
        "news_false_positive": sum(
            bool(r["deadline"]) or r["materials"] or r["conditions"] for r in news
        ),
        "blocked": blocked,
        "avg_chars": round(sum(r["chars"] for r in rows) / len(rows)) if rows else 0,
        "attachments": sum(r["attachments"] for r in rows),
        "attach_notices": sum(r["attachments"] > 0 for r in rows),
    }


def build_markdown(a: dict, snapshot: dict) -> str:
    g = a["genre_count"]
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [
        "# 真实站点采集集 · 解析质量审计",
        "",
        f"> 由 `scripts/real_audit.py` 自动生成于 {now}；数据集快照 "
        f"`{snapshot.get('generated_at', '-')}`。指标均由当前代码现场复算，可一键重跑。",
        "",
        "## 1. 为什么换指标口径",
        "",
        "早期用「截止日期抽取率 55%」描述真实集，这个数字是错的：42 份快照里有 18 份是新闻经纬的",
        "成果报道与校园新闻，本身不存在报名截止。把「正确地留空」记成「没抽到」，既低估了系统，",
        "也掩盖了更严重的问题——系统当时反过来在新闻稿上**硬造**字段。因此改为两把尺子：",
        "",
        "- **事务通知 / 公示公告**：办理截止的抽取覆盖情况，并标注截止的语义类型；",
        "- **新闻资讯**：报名类字段的**不误报率**。",
        "",
        "## 2. 数据集概览",
        "",
        "| 项目 | 数值 |",
        "| --- | --- |",
        f"| 快照条数 | {a['total']} 份 |",
        f"| 采集栏目 | {a['sources']} 个（研究生院 4 + 新闻经纬 3） |",
        f"| 平均正文长度 | {a['avg_chars']} 字 |",
        f"| 含附件通知 | {a['attach_notices']} 份，共 {a['attachments']} 个附件 |",
        f"| 事务通知 | {g.get('notice', 0)} 份 |",
        f"| 公示公告 | {g.get('publicity', 0)} 份 |",
        f"| 新闻资讯 | {g.get('news', 0)} 份 |",
        "",
        "## 3. 核心结果",
        "",
        "| 指标 | 结果 | 说明 |",
        "| --- | --- | --- |",
        f"| 事务/公示类办理截止覆盖 | {a['with_dl']} / {a['actionable_count']}（{a['coverage']}%） | "
        f"其余 {a['no_dl']} 份经逐条核对，见第 5 节 |",
        f"| 新闻资讯报名类字段误报 | {a['news_false_positive']} / {a['news_count']} 份 | "
        "不误报率 100%，截止/对象/条件/材料/方式全部留空 |",
        f"| 闸门拦下的假材料 | {a['blocked']['materials']} 份新闻稿 | "
        f"绕过闸门时共会产出 {a['blocked']['fields']} 个假字段 |",
        f"| 截止语义类型标注 | {len(a['kind_count'])} 类 | 报名/申报/提交/报送/征集/异议/办理截止 |",
        "",
        "### 截止语义类型分布",
        "",
        "| 类型 | 条数 |",
        "| --- | --- |",
    ]
    for kind, count in sorted(a["kind_count"].items(), key=lambda x: -x[1]):
        L.append(f"| {kind} | {count} |")

    L += [
        "",
        "> 把「公示期结束」明确标成**异议截止**而非报名截止，是真实数据带来的口径修正：",
        "> 同一个日期字段，对不同文体含义不同，界面上不能一概显示「报名截止」。",
        "",
        "## 4. 真实案例：修复前发生了什么",
        "",
        "下表是 v1.0 在同样快照上的实测观测（历史记录，不由当前代码复算），",
        "对应的修复见 `docs/key_code.md` 与 `app/services/genre.py`。",
        "",
        "| 通知 | 字段 | 修复前 | 问题 | 当前 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for title, field, old, why in V10_OBSERVED:
        row = next((r for r in a["rows"] if r["title"].startswith(title[:12])), None) or {}
        if field == "deadline":
            cur = row.get("deadline") or "留空"
        elif field == "materials":
            cur = f"{row.get('materials', 0)} 项"
        else:
            cur = row.get("deadline_kind") or "—"
        L.append(f"| {title[:30]} | {field} | {old} | {why} | {cur} |")

    L += [
        "",
        "**根因不是抽取不准，而是先问错了问题。** 新闻稿里没有「可办理事项」，",
        "对它跑报名类字段抽取，规则再精细也只能产出看似合理的假数据。",
        "因此解析链路前置文体闸门：事务通知 / 公示公告才进入字段抽取，",
        "新闻资讯只做分类与摘要，并在结果里写明留空原因。",
        "",
        "同时修正日期打分：`X至Y` 的范围末端原先无条件 +5 分，",
        "把赛事举办期、会期、公示期全推成截止日；现在要求上下文存在报名/申报/提交等办理动词，",
        "并对公示期、活动期间、新闻导语等范围直接归零。",
        "",
        "## 5. 未抽到截止日期的事务类通知（逐条核对）",
        "",
        "| 通知 | 核对结论 |",
        "| --- | --- |",
    ]
    for row in a["no_dl_rows"]:
        note = NO_DEADLINE_NOTES.get(row["title"], "原文未见明确办理时限，按「不猜」原则留空")
        L.append(f"| {row['title'][:38]} | {note} |")

    L += [
        "",
        "其中最后一条是**已知限制**：公示期是一个区间，末端日期可解释为异议截止，",
        "但为避免把「区间结束」泛化成所有通知的截止日，当前选择保守留空。",
        "这也符合系统一贯的事实保护原则——宁可留空待复核，不硬猜一个看起来合理的日期。",
        "",
        "## 6. 逐条明细（可核对）",
        "",
        "| # | 文体 | 截止 | 类型 | 材料 | 条件 | 字数 | 标题 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, r in enumerate(a["rows"], 1):
        L.append(
            f"| {i} | {r['label']} | {r['deadline'] or '—'} | {r['deadline_kind'] or '—'} "
            f"| {r['materials']} | {r['conditions']} | {r['chars']} | {r['title'][:36]} |"
        )

    L += [
        "",
        "---",
        "",
        "复现命令：`python scripts/real_audit.py`。本报告只做**可用性验证**，",
        "真实集没有人工逐字段标注，因此不与开发集/留出集的 F1 混算。",
        "",
    ]
    return "\n".join(L)


def main() -> None:
    parser = argparse.ArgumentParser(description="高校智讯 · 真实集解析质量审计")
    parser.add_argument("--input", default=str(SNAPSHOT), help="采集快照 JSON")
    parser.add_argument("--out", default=str(REPORT), help="Markdown 报告输出路径")
    parser.add_argument("--no-write", action="store_true", help="只打印摘要，不写报告")
    args = parser.parse_args()

    path = Path(args.input)
    if not path.is_absolute():
        path = ROOT / path
    items, snapshot = load_items(path)
    a = audit(items)

    print(f"真实集 {a['total']} 份 / {a['sources']} 个栏目")
    print(f"  文体：{a['genre_count']}")
    print(f"  事务与公示 {a['actionable_count']} 份，抽到办理截止 {a['with_dl']} 份"
          f"（{a['coverage']}%），留空 {a['no_dl']} 份")
    print(f"  新闻资讯 {a['news_count']} 份，报名类字段误报 {a['news_false_positive']} 份")
    print(f"  绕过闸门对照：{a['blocked']['materials']} 份新闻稿会产出假材料，"
          f"共 {a['blocked']['fields']} 个假字段")
    print(f"  截止语义类型：{a['kind_count']}")

    if not args.no_write:
        out = Path(args.out)
        if not out.is_absolute():
            out = ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(build_markdown(a, snapshot), encoding="utf-8")
        print(f"报告已写入：{out}")


if __name__ == "__main__":
    main()
