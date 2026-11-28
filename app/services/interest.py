"""兴趣信号采集：把「用户做过什么」变成推荐可用的信号。

推荐只靠用户填写的兴趣太单薄——填了「科研」的同学，实际加入待办的可能全是竞赛。
这里从三处取信号，全部可解释、可降级：

    行为信号（engaged）    加入待办的通知，越近的权重越高（半衰期 90 天）
    协同信号（co_engaged） 和你关注过同一条通知的同学，还关注了哪些通知
    热度信号（popular）    全站加入待办的人次，用于冷启动时兜底排序

三个信号都由数据库现算，不落缓存、不加新表，因此不会因为历史数据缺失而失效；
数据为空时返回空字典，推荐层自动退化为「内容 + 语义」两路，行为上无感。
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import UserTask

# 行为半衰期：90 天前加入的待办权重降到一半。学期长度量级，符合校园通知的生命周期。
HALF_LIFE_DAYS = 90.0


def _decay(created_at: dt.datetime | None, today: dt.date) -> float:
    if not created_at:
        return 1.0
    day = created_at.date() if isinstance(created_at, dt.datetime) else created_at
    age = max(0, (today - day).days)
    return 0.5 ** (age / HALF_LIFE_DAYS)


def build_signals(db: Session, user_id: int, today: dt.date | None = None) -> dict:
    """汇总该用户的行为、协同与热度信号。任何一步为空都不报错，只是少一路信号。"""
    today = today or dt.date.today()

    mine_rows = db.execute(
        select(UserTask.notice_id, UserTask.created_at, UserTask.status)
        .where(UserTask.user_id == user_id)
    ).all()
    engaged: list[int] = []
    engaged_weights: dict[int, float] = {}
    for notice_id, created_at, status in mine_rows:
        weight = _decay(created_at, today)
        # 已办结的待办比「只加进来还没动」更能说明真实兴趣，但不要过度放大
        if status == "done":
            weight *= 1.15
        engaged.append(notice_id)
        engaged_weights[notice_id] = round(weight, 3)

    # 协同：与你有关注交集的同学，他们的其它关注按交集大小加权
    others = db.execute(
        select(UserTask.user_id, UserTask.notice_id).where(UserTask.user_id != user_id)
    ).all()
    by_user: dict[int, set[int]] = defaultdict(set)
    for uid, notice_id in others:
        by_user[uid].add(notice_id)

    mine = set(engaged)
    co: Counter[int] = Counter()
    if mine:
        for items in by_user.values():
            overlap = len(items & mine)
            if overlap <= 0:
                continue
            for notice_id in items - mine:
                co[notice_id] += overlap

    popular: Counter[int] = Counter(notice_id for _, notice_id in others)

    return {
        "engaged": engaged,
        "engaged_weights": engaged_weights,
        "co_engaged": dict(co),
        "popular": dict(popular),
        "peer_users": len(by_user),
    }


def describe_signals(signals: dict) -> dict:
    """给界面用的信号摘要：不暴露任何他人身份，只给数量。"""
    return {
        "engaged": len(signals.get("engaged") or []),
        "co_engaged": len(signals.get("co_engaged") or {}),
        "popular": len(signals.get("popular") or {}),
        "peer_users": int(signals.get("peer_users") or 0),
        "half_life_days": HALF_LIFE_DAYS,
    }
