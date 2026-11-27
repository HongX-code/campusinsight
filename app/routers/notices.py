"""通知接口：录入 / 采集 / 检索 / 详情 / 修改 / 删除 / AI 解析 / 人工修正。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete as sql_delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import CATEGORIES
from app.database.connection import get_db
from app.database.models import Notice, NoticeAnalysis, UserTask
from app.serializers import AnalysisUpdate, NoticeIn, NoticeUpdate, UrlIn, notice_to_dict
from app.services import analyzer, classifier
from app.services import genre as genre_mod
from app.services.cleaner import clean_text
from app.services.crawler import CrawlError, fetch_notice
from app.services.textutil import content_hash, fix_publish, is_iso_date, normalize_text

router = APIRouter(prefix="/api/notices", tags=["通知"])

# 人工修正时允许写入的日期字段；与规则校验 validator.DATE_FIELDS 保持同一口径
EDITABLE_DATE_FIELDS = {
    "deadline": "截止日期",
    "start_time": "活动开始时间",
    "registration_start": "报名开始日期",
    "event_time": "活动举办时间",
}


def _validate_dates(payload: AnalysisUpdate) -> None:
    """人工修正的日期必须是合法且补零的 YYYY-MM-DD。

    看板 / 日程 / 匹配都会对 deadline 做日期运算，一条格式错误的日期会让
    这些接口整体 500。与其在下游到处兜底，不如在唯一的写入口就拒绝。
    """
    for key, label in EDITABLE_DATE_FIELDS.items():
        value = getattr(payload, key, None)
        if value is None:
            continue
        value = value.strip()
        if value and not is_iso_date(value):
            raise HTTPException(
                status_code=400,
                detail=f"{label}需为 YYYY-MM-DD 格式（例如 2026-11-20），当前为「{value}」",
            )


def _get_or_404(db: Session, notice_id: int) -> Notice:
    notice = db.get(Notice, notice_id)
    if not notice:
        raise HTTPException(status_code=404, detail="通知不存在")
    return notice


def run_analysis(db: Session, notice: Notice, use_llm: bool | None = None) -> dict:
    """执行解析并落库（一个通知只保留一条最新解析记录）。"""
    result = analyzer.analyze(notice.title, notice.content, notice.publish_time,
                              use_llm=use_llm, source_url=notice.source_url)

    record = notice.analysis or NoticeAnalysis(notice_id=notice.id)
    record.category = result["category"]
    record.genre = result.get("genre", "notice")
    record.actionable = bool(result.get("actionable", True))
    record.deadline = result["deadline"]
    record.deadline_kind = result.get("deadline_kind", "")
    record.deadline_source = result.get("deadline_source", "")
    record.start_time = result["start_time"]
    record.grade_requirement = json.dumps(result.get("grade_requirement") or [], ensure_ascii=False)
    record.major_requirement = json.dumps(result.get("major_requirement") or [], ensure_ascii=False)
    record.registration_start = result.get("registration_start")
    record.event_time = result.get("event_time")
    record.location = result.get("location") or ""
    record.contact = json.dumps(result.get("contact") or {}, ensure_ascii=False)
    record.checks = json.dumps(result.get("checks") or {}, ensure_ascii=False)
    record.target = json.dumps(result["target"], ensure_ascii=False)
    record.conditions = json.dumps(result["conditions"], ensure_ascii=False)
    record.materials = json.dumps(result["materials"], ensure_ascii=False)
    record.apply_method = result["apply_method"]
    record.evidence = json.dumps(result["evidence"], ensure_ascii=False)
    record.timeline = json.dumps(result.get("timeline_nodes") or [], ensure_ascii=False)
    record.confidence = result["confidence"]
    record.needs_review = result["needs_review"]
    record.engine = result["engine"]
    record.model_name = result["model_name"]
    record.review_note = result["review_note"]
    record.reviewed = False

    if notice.analysis is None:
        db.add(record)
    notice.category = result["category"]
    db.commit()
    db.refresh(notice)
    return notice_to_dict(notice)


@router.get("")
def list_notices(
    keyword: str = Query("", description="标题/正文关键词"),
    category: str = Query("", description="分类筛选"),
    review: str = Query("", description="needs_review / reviewed / unparsed"),
    genre: str = Query("", description="文体筛选 notice / publicity / news"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    stmt = select(Notice).options(selectinload(Notice.analysis))
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(Notice.title.like(like), Notice.content.like(like)))
    if category and category != "全部":
        stmt = stmt.where(Notice.category == category)

    # 只 join 一次：review 与 genre 同时出现时重复 join 会让 SQLAlchemy 报歧义
    want_join = review in ("needs_review", "reviewed") or (genre and genre != "全部")
    if want_join:
        stmt = stmt.join(NoticeAnalysis, NoticeAnalysis.notice_id == Notice.id)
        if review == "needs_review":
            stmt = stmt.where(NoticeAnalysis.needs_review.is_(True))
        elif review == "reviewed":
            stmt = stmt.where(NoticeAnalysis.reviewed.is_(True))
        if genre and genre != "全部":
            stmt = stmt.where(NoticeAnalysis.genre == genre)
    elif review == "unparsed":
        stmt = stmt.outerjoin(NoticeAnalysis, NoticeAnalysis.notice_id == Notice.id).where(
            NoticeAnalysis.id.is_(None)
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Notice.created_at.desc()).offset((page - 1) * size).limit(size)
    ).all()

    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [notice_to_dict(n) for n in rows],
        "categories": CATEGORIES,
        "genres": genre_mod.labels(),
    }


@router.get("/{notice_id}")
def get_notice(notice_id: int, db: Session = Depends(get_db)):
    return notice_to_dict(_get_or_404(db, notice_id))


@router.post("")
def create_notice(payload: NoticeIn, db: Session = Depends(get_db)):
    content = clean_text(payload.content)
    if len(content) < 10:
        raise HTTPException(status_code=400, detail="正文内容过短")
    h = content_hash(payload.title, content)
    dup = db.scalar(select(Notice).where(Notice.content_hash == h))
    if dup:
        raise HTTPException(status_code=409, detail=f"该通知已存在（编号 {dup.id}），无需重复录入")

    category = payload.category or classifier.classify(payload.title, content)[0]
    notice = Notice(
        title=normalize_text(payload.title),
        content=content,
        category=category if category in CATEGORIES else "其他",
        source_url=payload.source_url.strip(),
        source_name=payload.source_name.strip(),
        publish_time=fix_publish(payload.publish_time),
        content_hash=h,
        ingest_mode="manual",
    )
    db.add(notice)
    db.commit()
    db.refresh(notice)
    return notice_to_dict(notice)


@router.put("/{notice_id}")
def update_notice(notice_id: int, payload: NoticeUpdate, db: Session = Depends(get_db)):
    notice = _get_or_404(db, notice_id)
    old_title, old_content = notice.title, notice.content
    if payload.title is not None:
        notice.title = normalize_text(payload.title)
    if payload.content is not None:
        notice.content = clean_text(payload.content)
        notice.content_hash = content_hash(notice.title, notice.content)
    if payload.publish_time is not None:
        notice.publish_time = fix_publish(payload.publish_time)
    if payload.source_url is not None:
        notice.source_url = payload.source_url.strip()
    if payload.category is not None and payload.category in CATEGORIES:
        notice.category = payload.category

    # 原文变了，旧的解析结论与证据就不再对应当前正文：退回待复核，避免
    # 界面继续展示「已审核」的过期结论，提示用户重新解析。
    if notice.title != old_title or notice.content != old_content:
        if notice.analysis is not None:
            notice.analysis.needs_review = True
            notice.analysis.reviewed = False
            notice.analysis.review_note = "原文已修改，需重新解析后再复核"
    db.commit()
    db.refresh(notice)
    return notice_to_dict(notice)


@router.delete("/{notice_id}")
def delete_notice(notice_id: int, db: Session = Depends(get_db)):
    notice = _get_or_404(db, notice_id)
    # SQLite 默认不开启外键约束，ON DELETE CASCADE 不会生效；这里显式清理
    # 关联待办，否则前端会出现一条标题空白、永远无法完成的「僵尸任务」。
    db.execute(sql_delete(UserTask).where(UserTask.notice_id == notice_id))
    db.delete(notice)
    db.commit()
    return {"ok": True, "id": notice_id}


@router.post("/ingest/url")
def ingest_by_url(payload: UrlIn, db: Session = Depends(get_db)):
    """粘贴公开网页链接采集。采集失败不影响手动录入通道。"""
    try:
        data = fetch_notice(payload.url)
    except CrawlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    h = content_hash(data["title"], data["content"])
    dup = db.scalar(select(Notice).where(Notice.content_hash == h))
    if dup:
        raise HTTPException(status_code=409, detail=f"该通知已存在（编号 {dup.id}）")

    notice = Notice(
        title=data["title"],
        content=data["content"],
        category=classifier.classify(data["title"], data["content"])[0],
        source_url=data["source_url"],
        source_name=data["source_name"],
        publish_time=data["publish_time"],
        content_hash=h,
        ingest_mode="url",
    )
    db.add(notice)
    db.commit()
    db.refresh(notice)

    if payload.auto_analyze:
        return run_analysis(db, notice)
    return notice_to_dict(notice)


@router.post("/{notice_id}/analyze")
def analyze_notice(notice_id: int, use_llm: bool | None = None, db: Session = Depends(get_db)):
    notice = _get_or_404(db, notice_id)
    return run_analysis(db, notice, use_llm)


@router.post("/batch-analyze")
def batch_analyze(only_unparsed: bool = True, db: Session = Depends(get_db)):
    stmt = select(Notice)
    if only_unparsed:
        stmt = stmt.outerjoin(NoticeAnalysis, NoticeAnalysis.notice_id == Notice.id).where(
            NoticeAnalysis.id.is_(None)
        )
    rows = db.scalars(stmt).all()
    done, failed = 0, []
    for notice in rows:
        try:
            run_analysis(db, notice)
            done += 1
        except Exception as exc:  # 单条失败不影响批量
            failed.append({"id": notice.id, "error": str(exc)[:80]})
    return {"total": len(rows), "success": done, "failed": failed}


@router.put("/{notice_id}/analysis")
def correct_analysis(notice_id: int, payload: AnalysisUpdate, db: Session = Depends(get_db)):
    """人工修正解析结果：修正后标记为已复核，并回溯更新通知分类。"""
    _validate_dates(payload)
    notice = _get_or_404(db, notice_id)
    record = notice.analysis

    # 驳回必须早于任何字段写入：否则同一次提交里夹带的修正字段也会被
    # 一起 commit，「不覆盖字段、退回重解析」的语义就形同虚设。
    if payload.action == "reject":
        if record is not None:
            record.reviewed = False
            record.needs_review = True
            reason = (payload.reject_reason or payload.review_note or "解析结果不可用").strip()[:200]
            record.review_note = "人工驳回：%s（需重新解析）" % reason
            db.commit()
            db.refresh(notice)
        return notice_to_dict(notice)

    if record is None:
        record = NoticeAnalysis(notice_id=notice.id)
        db.add(record)

    if payload.category is not None:
        cat = classifier.normalize_category(payload.category)
        record.category = cat
        notice.category = cat
    if payload.deadline is not None:
        record.deadline = payload.deadline.strip() or None
    if payload.start_time is not None:
        record.start_time = payload.start_time.strip() or None
    if payload.target is not None:
        record.target = json.dumps(payload.target, ensure_ascii=False)
    if payload.conditions is not None:
        record.conditions = json.dumps(payload.conditions, ensure_ascii=False)
    if payload.materials is not None:
        record.materials = json.dumps(payload.materials, ensure_ascii=False)
    if payload.apply_method is not None:
        record.apply_method = payload.apply_method
    for key in ("grade_requirement", "major_requirement"):
        value = getattr(payload, key)
        if value is not None:
            setattr(record, key, json.dumps(value, ensure_ascii=False))
    for key in ("registration_start", "event_time"):
        value = getattr(payload, key)
        if value is not None:
            setattr(record, key, value.strip() or None)
    if payload.location is not None:
        record.location = payload.location
    if payload.contact is not None:
        record.contact = json.dumps(payload.contact, ensure_ascii=False)
    if payload.review_note is not None:
        record.review_note = payload.review_note

    record.reviewed = True
    record.needs_review = False
    if "人工驳回：" in (record.review_note or ""):
        # 重新通过后清掉旧的驳回记录，避免说明里同时留着「需重新解析」和「已通过」
        record.review_note = "已重新解析并通过人工审核"
    else:
        record.review_note = (record.review_note or "") + (
            "｜人工通过" if payload.action == "approve" else "｜已人工复核"
        )
    db.commit()
    db.refresh(notice)
    return notice_to_dict(notice)
