"""统一的序列化与请求模型。"""
from __future__ import annotations

import json

from pydantic import BaseModel, Field

from app.database.models import Notice, NoticeAnalysis, UserTask
from app.services.genre import GENRE_LABELS


def loads(text: str, default):
    try:
        value = json.loads(text) if text else default
        return value if value is not None else default
    except (json.JSONDecodeError, TypeError):
        return default


def analysis_to_dict(a: NoticeAnalysis | None) -> dict | None:
    if a is None:
        return None
    return {
        "notice_id": a.notice_id,
        "category": a.category,
        "genre": a.genre or "notice",
        "genre_label": GENRE_LABELS.get(a.genre or "notice", "事务通知"),
        "actionable": bool(a.actionable) if a.actionable is not None else True,
        "deadline": a.deadline,
        "deadline_kind": a.deadline_kind or "",
        "deadline_source": a.deadline_source or "",
        "start_time": a.start_time,
        "grade_requirement": loads(a.grade_requirement, []),
        "major_requirement": loads(a.major_requirement, []),
        "registration_start": a.registration_start,
        "event_time": a.event_time,
        "location": a.location or "",
        "contact": loads(a.contact, {}),
        "checks": loads(a.checks, {}),
        "target": loads(a.target, []),
        "conditions": loads(a.conditions, []),
        "materials": loads(a.materials, []),
        "apply_method": a.apply_method,
        "evidence": loads(a.evidence, {}),
        "confidence": a.confidence,
        "needs_review": a.needs_review,
        "engine": a.engine,
        "model_name": a.model_name,
        "review_note": a.review_note,
        "reviewed": a.reviewed,
        "updated_at": a.updated_at.isoformat(timespec="seconds") if a.updated_at else None,
    }


def notice_to_dict(n: Notice, with_analysis: bool = True) -> dict:
    data = {
        "id": n.id,
        "title": n.title,
        "content": n.content,
        "category": n.category,
        "source_url": n.source_url,
        "source_name": n.source_name,
        "publish_time": n.publish_time,
        "ingest_mode": n.ingest_mode,
        "attachments": loads(n.attachments, []),
        "created_at": n.created_at.isoformat(timespec="seconds") if n.created_at else None,
        "summary": (n.content or "").replace("\n", " ")[:110],
    }
    if with_analysis:
        data["analysis"] = analysis_to_dict(n.analysis)
    return data


def task_to_dict(t: UserTask) -> dict:
    return {
        "id": t.id,
        "user_id": t.user_id,
        "notice_id": t.notice_id,
        "status": t.status,
        "remind_time": t.remind_time,
        "note": t.note,
        "notice_title": t.notice.title if t.notice else "",
        "deadline": t.notice.analysis.deadline if (t.notice and t.notice.analysis) else None,
    }


# ---------------- 请求模型 ----------------
class NoticeIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    content: str = Field(min_length=10)
    publish_time: str = ""
    source_url: str = ""
    source_name: str = ""
    category: str = ""


class NoticeUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    publish_time: str | None = None
    source_url: str | None = None
    category: str | None = None


class UrlIn(BaseModel):
    url: str
    auto_analyze: bool = True


class AnalysisUpdate(BaseModel):
    category: str | None = None
    deadline: str | None = None
    start_time: str | None = None
    target: list[str] | None = None
    conditions: list[str] | None = None
    materials: list[str] | None = None
    apply_method: str | None = None
    grade_requirement: list[str] | None = None
    major_requirement: list[str] | None = None
    registration_start: str | None = None
    event_time: str | None = None
    location: str | None = None
    contact: dict | None = None
    review_note: str | None = None
    # 人工审核动作（技术文档1 第 47 章）：approve 通过 / reject 驳回 / 空=修正字段
    action: str = ""
    reject_reason: str = ""


class UserIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    grade: str = ""
    major: str = ""
    interests: list[str] = []
    student_no: str = ""


class TaskIn(BaseModel):
    notice_id: int
    status: str = "todo"
    remind_time: str = ""
    note: str = ""


class TaskUpdate(BaseModel):
    status: str | None = None
    note: str | None = None
    remind_time: str | None = None


class AskIn(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    top_k: int = Field(default=3, ge=1, le=10)


class PreviewIn(BaseModel):
    title: str = ""
    content: str = Field(min_length=10)
    publish_time: str = ""
    source_url: str = ""
    use_llm: bool | None = None


class CrawlPreviewIn(BaseModel):
    """站点批量采集：抓取预览（不入库）。"""

    source_ids: list[str] = []
    limit: int = Field(default=8, ge=1, le=50)
    max_pages: int = Field(default=1, ge=1, le=5)
    fetch_detail: bool = True
    # 增量抓取：跳过已收录条目，翻页找新内容；连续 N 条已收录即停止该源。
    incremental: bool = True
    stop_after_known: int = Field(default=3, ge=1, le=10)


class CrawlItemIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    content: str = ""
    url: str = ""
    publish_date: str = ""
    unit: str = ""
    attachments: list[dict] = []


class CrawlImportIn(BaseModel):
    """把预览中勾选的条目入库；items 为空时按 source_ids 现场抓取后直接入库。"""

    items: list[CrawlItemIn] = []
    source_ids: list[str] = []
    limit: int = Field(default=8, ge=1, le=50)
    auto_analyze: bool = False
