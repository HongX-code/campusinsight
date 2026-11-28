"""用户接口：画像管理、个性化匹配、个人待办。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete as sql_delete, select
from sqlalchemy.orm import Session, selectinload

from app.database.connection import get_db
from app.database.models import Notice, NoticeAnalysis, User, UserTask
from app.serializers import TaskIn, TaskUpdate, UserIn, analysis_to_dict, task_to_dict
from app.services.interest import build_signals, describe_signals
from app.services.matcher import match_one
from app.services.recommender import (
    ALGORITHM_VERSION,
    EXPLORE_BONUS,
    FUSION_WEIGHTS,
    explain,
    fusion_explain,
    pipeline,
    recommend,
)
from app.services.textutil import safe_iso_date

router = APIRouter(prefix="/api", tags=["用户"])


def _user_dict(u: User) -> dict:
    return {
        "id": u.id,
        "name": u.name,
        "grade": u.grade,
        "major": u.major,
        "student_no": u.student_no,
        "interests": json.loads(u.interests or "[]"),
    }


def _get_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    rows = db.scalars(select(User).order_by(User.id)).all()
    return [_user_dict(u) for u in rows]


@router.post("/users")
def create_user(payload: UserIn, db: Session = Depends(get_db)):
    user = User(
        name=payload.name.strip(),
        grade=payload.grade.strip(),
        major=payload.major.strip(),
        student_no=payload.student_no.strip(),
        interests=json.dumps(payload.interests, ensure_ascii=False),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_dict(user)


@router.get("/users/{user_id}")
def get_user(user_id: int, db: Session = Depends(get_db)):
    return _user_dict(_get_user(db, user_id))


@router.put("/users/{user_id}")
def update_user(user_id: int, payload: UserIn, db: Session = Depends(get_db)):
    user = _get_user(db, user_id)
    user.name = payload.name.strip() or user.name
    user.grade = payload.grade.strip()
    user.major = payload.major.strip()
    user.interests = json.dumps(payload.interests, ensure_ascii=False)
    user.student_no = payload.student_no.strip()
    db.commit()
    db.refresh(user)
    return _user_dict(user)


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db)):
    user = _get_user(db, user_id)
    # 显式清理待办，保证 SQLite（外键默认关闭）与 RDS 表现一致
    db.execute(sql_delete(UserTask).where(UserTask.user_id == user_id))
    db.delete(user)
    db.commit()
    return {"ok": True}


@router.get("/users/{user_id}/matches")
def get_matches(
    user_id: int,
    level: str = Query("", description="match / maybe / no"),
    only_open: bool = Query(False, description="仅显示未截止"),
    db: Session = Depends(get_db),
):
    """个性化匹配：对全部已解析通知打分，返回三级结论与判断依据。"""
    import datetime as dt

    user = _user_dict(_get_user(db, user_id))
    notices = db.scalars(
        select(Notice).options(selectinload(Notice.analysis)).order_by(Notice.created_at.desc())
    ).all()

    results = []
    for n in notices:
        analysis = analysis_to_dict(n.analysis)
        if analysis is None:
            continue
        # 新闻资讯没有可办理事项，不该给出「未识别到截止日期，请核对原文」这类误导性建议
        if not analysis.get("actionable", True):
            continue
        item = match_one(
            user,
            {
                "id": n.id,
                "category": n.category,
                "publish_time": n.publish_time,
                "created_at": n.created_at,
            },
            analysis,
        )
        if only_open and analysis.get("deadline"):
            d = safe_iso_date(analysis["deadline"])
            if d is not None and d < dt.date.today():
                continue
        if level and item["level"] != level:
            continue
        item["notice"] = {
            "id": n.id,
            "title": n.title,
            "category": n.category,
            "source_url": n.source_url,
            "summary": n.content.replace("\n", " ")[:100],
        }
        results.append(item)

    # 先按得分降序；得分相同时把临期的排在前面，再按编号稳定排序，
    # 保证同一用户多次请求看到相同顺序（前端不会"刷新一下就换一批"）。
    results.sort(key=lambda x: (-x["score"], -x.get("fit", 0), x["notice_id"]))
    counts = {"match": 0, "maybe": 0, "no": 0}
    for r in results:
        counts[r["level"]] += 1
    return {"user": user, "counts": counts, "items": results}


@router.get("/users/{user_id}/recommendations")
def get_recommendations(
    user_id: int,
    limit: int = Query(6, ge=1, le=20, description="返回条数"),
    include_expired: bool = Query(False, description="是否包含已截止（默认不推已截止）"),
    db: Session = Depends(get_db),
):
    """精准推荐位：召回 → 画像 → 融合 → 探索 → 重排，返回「为什么推给你」与分项依据。"""
    user = _user_dict(_get_user(db, user_id))
    notices = db.scalars(
        select(Notice).options(selectinload(Notice.analysis)).order_by(Notice.created_at.desc())
    ).all()

    rows = []
    for n in notices:
        analysis = analysis_to_dict(n.analysis)
        if analysis is None:
            continue
        rows.append({
            "id": n.id,
            "title": n.title,
            "category": n.category,
            "source_url": n.source_url,
            "content": (n.content or "")[:600],
            "publish_time": n.publish_time,
            "created_at": n.created_at,
            "analysis": analysis,
        })

    # 行为 / 协同 / 热度信号来自待办表，现算不缓存；没有数据时自动降级为内容 + 语义两路
    signals = build_signals(db, user_id)
    result = recommend(user, rows, limit=limit, include_expired=include_expired,
                       signals=signals)
    # 补上展示所需的通知摘要字段；推荐位只给前 100 字，点开再看全文
    by_id = {r["id"]: r for r in rows}
    for item in result["items"]:
        row = by_id.get(item["notice_id"]) or {}
        item["notice"] = {
            "id": row.get("id"),
            "title": row.get("title"),
            "category": row.get("category"),
            "source_url": row.get("source_url"),
            "summary": (row.get("title") or "")[:100],
        }
        analysis = row.get("analysis") or {}
        item["deadline"] = analysis.get("deadline")
    result["user"] = user
    result["signals_meta"] = describe_signals(signals)
    return result


@router.get("/recommendation-model")
def recommendation_model():
    """推荐模型说明：七因子 + 五路融合 + 五段流程 + 冷启动策略，界面可据此展示「算法怎么看」。"""
    from app.services.matcher import MATCH_THRESHOLD
    from app.services.recommender import DIVERSITY_LAMBDA, MAX_PER_CATEGORY

    return {
        "algorithm": ALGORITHM_VERSION,
        "factors": explain(),
        "fusion": fusion_explain(),
        "pipeline": pipeline(),
        "threshold": MATCH_THRESHOLD,
        "max_per_category": MAX_PER_CATEGORY,
        "diversity_lambda": DIVERSITY_LAMBDA,
        "explore_bonus": EXPLORE_BONUS,
        "fusion_weights": dict(FUSION_WEIGHTS),
        "cold_start": "未填写兴趣且没有待办行为时，兴趣与协同两路归零，由适用对象、专业限制、时效与全站热度主导排序",
    }


@router.get("/users/{user_id}/tasks")
def list_tasks(user_id: int, db: Session = Depends(get_db)):
    _get_user(db, user_id)
    rows = db.scalars(
        select(UserTask)
        .options(selectinload(UserTask.notice).selectinload(Notice.analysis))
        .where(UserTask.user_id == user_id)
        .order_by(UserTask.created_at.desc())
    ).all()
    return [task_to_dict(t) for t in rows]


@router.post("/users/{user_id}/tasks")
def create_task(user_id: int, payload: TaskIn, db: Session = Depends(get_db)):
    _get_user(db, user_id)
    if not db.get(Notice, payload.notice_id):
        raise HTTPException(status_code=404, detail="通知不存在")
    # 新闻资讯无截止日、无办理事项，加进待办只会制造一条永远无法完成的噪音
    flag = db.scalar(
        select(NoticeAnalysis.actionable).where(NoticeAnalysis.notice_id == payload.notice_id)
    )
    if flag is False:
        raise HTTPException(status_code=400, detail="新闻资讯类内容无办理事项，不能加入待办")
    exists = db.scalar(
        select(UserTask).where(UserTask.user_id == user_id, UserTask.notice_id == payload.notice_id)
    )
    if exists:
        raise HTTPException(status_code=409, detail="该通知已在待办中")
    task = UserTask(
        user_id=user_id,
        notice_id=payload.notice_id,
        status=payload.status,
        remind_time=payload.remind_time,
        note=payload.note,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task_to_dict(task)


@router.patch("/tasks/{task_id}")
def update_task(task_id: int, payload: TaskUpdate, db: Session = Depends(get_db)):
    task = db.get(UserTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="待办不存在")
    if payload.status is not None:
        task.status = payload.status if payload.status in ("todo", "doing", "done") else task.status
    if payload.note is not None:
        task.note = payload.note
    if payload.remind_time is not None:
        task.remind_time = payload.remind_time
    db.commit()
    db.refresh(task)
    return task_to_dict(task)


@router.delete("/tasks/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(UserTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="待办不存在")
    db.delete(task)
    db.commit()
    return {"ok": True}


@router.get("/users/{user_id}/agenda")
def agenda(user_id: int, days: int = Query(30, ge=1, le=180), db: Session = Depends(get_db)):
    """时间管理：返回未来 N 天内截止的通知，按剩余天数升序排列。"""
    import datetime as dt

    _get_user(db, user_id)
    today = dt.date.today()
    limit = today + dt.timedelta(days=days)
    rows = db.scalars(select(Notice).options(selectinload(Notice.analysis))).all()
    items = []
    for n in rows:
        a = n.analysis
        if not a:
            continue
        # 统一走安全解析：格式不合法的 deadline 直接跳过，而不是抛 ValueError
        d = safe_iso_date(a.deadline)
        if d is None or not (today <= d <= limit):
            continue
        items.append(
            {
                "notice_id": n.id,
                "title": n.title,
                "category": n.category,
                "deadline": d.isoformat(),
                "days_left": (d - today).days,
            }
        )
    items.sort(key=lambda x: x["deadline"])
    return items
