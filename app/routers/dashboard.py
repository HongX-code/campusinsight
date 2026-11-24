"""数据看板：分类分布、解析质量、截止提醒、录入趋势。"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import CATEGORIES
from app.database.connection import get_db
from app.database.models import Notice, NoticeAnalysis, User, UserTask
from app.services.textutil import safe_iso_date

router = APIRouter(prefix="/api/dashboard", tags=["看板"])


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    today = dt.date.today()

    total = db.scalar(select(func.count(Notice.id))) or 0
    analyzed = db.scalar(select(func.count(NoticeAnalysis.id))) or 0
    need_review = db.scalar(
        select(func.count(NoticeAnalysis.id)).where(NoticeAnalysis.needs_review.is_(True))
    ) or 0
    reviewed = db.scalar(
        select(func.count(NoticeAnalysis.id)).where(NoticeAnalysis.reviewed.is_(True))
    ) or 0
    avg_conf = db.scalar(select(func.avg(NoticeAnalysis.confidence))) or 0.0

    # 分类分布
    cat_rows = db.execute(select(Notice.category, func.count(Notice.id)).group_by(Notice.category)).all()
    cat_map = {c: 0 for c in CATEGORIES}
    for name, count in cat_rows:
        cat_map[name or "其他"] = count

    # 引擎分布
    engine_rows = db.execute(
        select(NoticeAnalysis.engine, func.count(NoticeAnalysis.id)).group_by(NoticeAnalysis.engine)
    ).all()
    engines = [{"engine": e or "rule", "count": c} for e, c in engine_rows]

    # 采集渠道构成：真实站点抓取 / 链接采集 / 手动录入 / 样本导入
    MODE_LABELS = {"crawl": "站点批量采集", "url": "粘贴链接采集",
                   "manual": "手动录入", "import": "离线样本导入"}
    mode_rows = db.execute(
        select(Notice.ingest_mode, func.count(Notice.id)).group_by(Notice.ingest_mode)
    ).all()
    modes = [
        {"name": MODE_LABELS.get(m or "manual", "其他"), "value": c}
        for m, c in sorted(mode_rows, key=lambda x: -x[1])
    ]

    # 文体构成：新闻资讯不参与报名类字段抽取，也不该混进「待办/匹配」口径
    GENRE_LABELS = {"notice": "事务通知", "publicity": "公示公告", "news": "新闻资讯"}
    genre_rows = db.execute(
        select(NoticeAnalysis.genre, func.count(NoticeAnalysis.id)).group_by(NoticeAnalysis.genre)
    ).all()
    genres = [
        {"name": GENRE_LABELS.get(g or "notice", "其他"), "value": c}
        for g, c in sorted(genre_rows, key=lambda x: -x[1])
    ]

    # 截止提醒：一律经 safe_iso_date 解析，历史脏日期（人工录入或旧版本遗留）
    # 只会被跳过，绝不能让一条坏数据把整个看板打成 500。
    upcoming7 = upcoming30 = overdue = 0
    days7, days30 = dt.timedelta(days=7), dt.timedelta(days=30)
    # 只需要 deadline 一列：整行加载会把 evidence/target 等大字段一并读进内存，
    # 在通知量增长后这几项统计会无谓占用大量内存。
    deadline_values = db.scalars(select(NoticeAnalysis.deadline)).all()
    for raw_deadline in deadline_values:
        d = safe_iso_date(raw_deadline)
        if d is None:
            continue
        delta = d - today
        if delta.days < 0:
            overdue += 1
        elif delta <= days7:
            upcoming7 += 1
        elif delta <= days30:
            upcoming30 += 1
    near_rows = db.scalars(
        select(Notice)
        .options(selectinload(Notice.analysis))
        .join(NoticeAnalysis, NoticeAnalysis.notice_id == Notice.id)
        .where(NoticeAnalysis.deadline.is_not(None))
        .order_by(NoticeAnalysis.deadline.asc())
    ).all()
    deadlines_today = []
    for n in near_rows:
        raw = n.analysis.deadline
        d = safe_iso_date(raw)
        if d is None:
            continue
        delta = d - today
        if dt.timedelta(0) <= delta <= days7:
            deadlines_today.append(
                {
                    "notice_id": n.id,
                    "title": n.title,
                    "category": n.category,
                    "deadline": d.isoformat(),
                    "days_left": delta.days,
                }
            )

    # 近 7 天录入趋势：一次 GROUP BY 取整周统计，替代逐日 7 次独立查询（N+1）。
    # 用曲线的时间窗而不是逐日 IN，兼容 SQLite 的 date() 与 MySQL 的 DATE()。
    week_start = today - dt.timedelta(days=6)
    trend_rows = db.execute(
        select(func.date(Notice.created_at).label("day"), func.count(Notice.id).label("n"))
        .where(Notice.created_at >= dt.datetime.combine(week_start, dt.time.min))
        .group_by(func.date(Notice.created_at))
    ).all()
    trend_counts = {str(day): n for day, n in trend_rows if day}
    trend = []
    for i in range(6, -1, -1):
        day = today - dt.timedelta(days=i)
        key = day.isoformat()
        trend.append({"date": day.strftime("%m-%d"), "count": trend_counts.get(key, 0)})

    return {
        "overview": {
            "total_notices": total,
            "analyzed": analyzed,
            "unanalyzed": total - analyzed,
            "need_review": need_review,
            "reviewed": reviewed,
            "avg_confidence": round(float(avg_conf), 3),
            "users": db.scalar(select(func.count(User.id))) or 0,
            "tasks": db.scalar(select(func.count(UserTask.id))) or 0,
            "upcoming_7d": upcoming7,
            "upcoming_30d": upcoming30,
            "overdue": overdue,
        },
        "categories": [{"name": k, "value": v} for k, v in cat_map.items()],
        "engines": engines,
        "modes": modes,
        "genres": genres,
        "deadlines": deadlines_today,
        "trend": trend,
    }
