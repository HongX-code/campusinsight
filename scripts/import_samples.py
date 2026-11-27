"""初始化数据库并导入演示数据。

数据来源有两部分：
1. `data/sample_notices.json` —— 24 份构造样本，覆盖竞赛/奖助/讲座/志愿/实践等
   各体裁与各种边界写法（条件写在正文中间、截止日期跨年、材料分散在多段等）；
2. `data/real_notices.json` —— 由 `scripts/crawl.py` 从武汉理工大学研究生院、
   新闻经纬等公开栏目真实抓取的通知快照（含真实 URL、发布时间与附件清单）。
   构造样本保证覆盖面，真实样本证明系统面对真实网页时同样可用。
3. `data/conflict_notices.json` —— 6 条同源多发样本（3 组），专门用来演示
   「冲突治理」：同一件事被两个单位转发时字段被改出差异，单条解析正确也发现不了。
   需显式加 `--with-conflicts` 才导入，避免影响开发集评测分母。

用法：
    python scripts/init_db.py           # 仅建表
    python scripts/import_samples.py    # 建表 + 导入构造样本与真实样本 + 解析
    python scripts/import_samples.py --real-only
    python scripts/import_samples.py --no-analyze
    python scripts/import_samples.py --with-conflicts   # 附带 6 条同源多发样本
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.database.connection import SessionLocal, init_db  # noqa: E402
from app.database.models import Notice, User  # noqa: E402
from app.services import classifier  # noqa: E402
from app.services.textutil import content_hash, normalize_text  # noqa: E402

SAMPLE_FILE = ROOT / "data" / "sample_notices.json"
REAL_FILE = ROOT / "data" / "real_notices.json"
CONFLICT_FILE = ROOT / "data" / "conflict_notices.json"

DEMO_USERS = [
    {"name": "张明", "grade": "大三", "major": "信息管理与信息系统",
     "interests": ["竞赛", "科研", "讲座"], "student_no": "0122301"},
    {"name": "李雨", "grade": "大一", "major": "机械设计制造及其自动化",
     "interests": ["志愿服务", "校园活动"], "student_no": "0124105"},
    {"name": "王浩", "grade": "研二", "major": "计算机科学与技术",
     "interests": ["科研", "实习", "讲座"], "student_no": "2024208"},
]


def load_samples() -> list[dict]:
    data = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
    return data["notices"]


def load_real_notices() -> list[dict]:
    """从采集快照还原真实通知。快照结构见 services/collector.save_snapshot。"""
    if not REAL_FILE.exists():
        return []
    data = json.loads(REAL_FILE.read_text(encoding="utf-8"))
    out: list[dict] = []
    for block in data.get("sources", []):
        source_name = (block.get("source") or {}).get("name", "站点采集")
        for item in block.get("items", []):
            if not (item.get("content") or "").strip():
                continue
            out.append({
                "title": item["title"],
                "content": item["content"],
                "source_url": item.get("url", ""),
                "source_name": source_name,
                "publish_time": item.get("publish_date", ""),
                "attachments": item.get("attachments") or [],
            })
    return out


def _created_at(publish_time: str, fallback_index: int, total: int) -> dt.datetime:
    """真实通知按发布时间落时间线；没有发布时间的按序号铺开到最近 7 天。"""
    try:
        day = dt.date.fromisoformat(publish_time[:10])
        return dt.datetime.combine(day, dt.time(9, 30))
    except ValueError:
        return dt.datetime.now() - dt.timedelta(days=(total - 1 - fallback_index) * 7 // max(total, 1))


def _insert(db, item: dict, ingest_mode: str, created_at: dt.datetime, analyze: bool) -> bool:
    from app.routers.notices import run_analysis

    content = normalize_text(item["content"])
    title = normalize_text(item["title"])
    fingerprint = content_hash(title, content)
    if db.query(Notice).filter(Notice.content_hash == fingerprint).first():
        return False

    notice = Notice(
        title=title,
        content=content,
        category=classifier.classify(title, content)[0],
        source_url=item.get("source_url", ""),
        source_name=item.get("source_name", ""),
        publish_time=item.get("publish_time", ""),
        content_hash=fingerprint,
        ingest_mode=ingest_mode,
        attachments=json.dumps(item.get("attachments") or [], ensure_ascii=False),
        created_at=created_at,
    )
    db.add(notice)
    db.commit()
    db.refresh(notice)
    if analyze:
        run_analysis(db, notice)
    return True


def import_notices(analyze: bool = True) -> tuple[int, int]:
    db = SessionLocal()
    created = skipped = 0
    samples = load_samples()
    try:
        for index, item in enumerate(samples):
            # 构造样本均匀分布到最近 7 天，便于看板展示采集趋势
            created_at = dt.datetime.now() - dt.timedelta(
                days=(len(samples) - 1 - index) * 7 // len(samples)
            )
            if _insert(db, item, "import", created_at, analyze):
                created += 1
            else:
                skipped += 1
    finally:
        db.close()
    return created, skipped


def import_real(analyze: bool = True) -> tuple[int, int]:
    db = SessionLocal()
    created = skipped = 0
    try:
        items = load_real_notices()
        for index, item in enumerate(items):
            created_at = _created_at(item.get("publish_time", ""), index, len(items))
            if _insert(db, item, "crawl", created_at, analyze):
                created += 1
            else:
                skipped += 1
    finally:
        db.close()
    return created, skipped


def load_conflicts() -> list[dict]:
    """同源多发冲突样本：同一件事被两个单位分别转发，字段被改出了差异。

    单独成文件（不并入 sample_notices.json）是为了不污染开发集评测口径——
    `scripts/evaluate.py` 按 key 对齐 gold，多出来的用例会改变分母。
    """
    if not CONFLICT_FILE.exists():
        return []
    data = json.loads(CONFLICT_FILE.read_text(encoding="utf-8"))
    return data["notices"]


def import_conflicts(analyze: bool = True) -> tuple[int, int]:
    db = SessionLocal()
    created = skipped = 0
    try:
        items = load_conflicts()
        for index, item in enumerate(items):
            created_at = _created_at(item.get("publish_time", ""), index, len(items))
            if _insert(db, item, "import", created_at, analyze):
                created += 1
            else:
                skipped += 1
    finally:
        db.close()
    return created, skipped


def import_users() -> int:
    db = SessionLocal()
    count = 0
    try:
        for u in DEMO_USERS:
            if db.query(User).filter(User.name == u["name"]).first():
                continue
            db.add(User(
                name=u["name"], grade=u["grade"], major=u["major"],
                interests=json.dumps(u["interests"], ensure_ascii=False),
                student_no=u["student_no"],
            ))
            count += 1
        db.commit()
    finally:
        db.close()
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="导入演示数据")
    parser.add_argument("--real-only", action="store_true", help="只导入真实采集样本")
    parser.add_argument("--samples-only", action="store_true", help="只导入构造样本")
    parser.add_argument("--no-analyze", action="store_true", help="只入库，不做解析")
    parser.add_argument("--with-conflicts", action="store_true",
                        help="额外导入 6 条同源多发样本，供「冲突治理」视图演示")
    args = parser.parse_args()

    init_db()
    analyze = not args.no_analyze
    parts = []

    if not args.real_only:
        created, skipped = import_notices(analyze=analyze)
        parts.append(f"构造样本：新增 {created} 条，跳过 {skipped} 条")
    if not args.samples_only:
        created, skipped = import_real(analyze=analyze)
        parts.append(f"真实采集：新增 {created} 条，跳过 {skipped} 条")
    if args.with_conflicts:
        created, skipped = import_conflicts(analyze=analyze)
        parts.append(f"冲突样本：新增 {created} 条，跳过 {skipped} 条")

    users = import_users()
    print("；".join(parts) + f"；新增演示用户 {users} 个")


if __name__ == "__main__":
    main()
