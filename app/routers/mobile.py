"""多端接入层：微信小程序登录、订阅消息、移动端聚合接口。

设计上不做「又一套后端」：小程序端复用的是同一份解析结果、同一份匹配逻辑、
同一个用户画像。这一层只做三件事——

1. **身份桥接**：把微信 openid 映射到本系统已有的 users.id，
   于是学生在小程序里加的待办，回到 Web 端原样可见；
2. **流量整形**：小程序弱网首屏只允许一次请求，`/api/mobile/bootstrap`
   把首页需要的数据一次打包返回，避免五六次串行请求把首屏拖到 3 秒以上；
3. **能力补齐**：订阅消息授权、办理清单、冲突清单、日历订阅这些
   在 Web 端不好表达的能力，集中在这一层暴露。
"""
from __future__ import annotations

import datetime as dt
import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.database.connection import get_db
from app.database.models import (
    Notice,
    NoticeAnalysis,
    NoticeConflict,
    PushSubscription,
    User,
    UserTask,
    WechatBinding,
)
from app.serializers import analysis_to_dict, loads as _load, notice_to_dict
from app.services import checklist as checklist_mod
from app.services import conflict as conflict_mod
from app.services import ical as ical_mod
from app.services import timeline as timeline_mod
from app.services import wechat as wx
from app.services.matcher import match_one
from app.services.textutil import safe_iso_date

logger = logging.getLogger("zhixun.mobile")

router = APIRouter(prefix="/api", tags=["多端接入"])


# ---------------------------------------------------------------- 请求模型
class WechatLoginIn(BaseModel):
    code: str = ""
    device_key: str = ""
    nickname: str = ""
    avatar: str = ""


class SubscribeIn(BaseModel):
    template_id: str = ""
    notice_id: int = 0
    count: int = Field(default=1, ge=1, le=10)


class ConflictResolveIn(BaseModel):
    resolution: str = Field(description="use_a / use_b / both_wrong / ignored")


# ---------------------------------------------------------------- 身份解析
def _token_from(headers: dict, query_token: str) -> str:
    """票据可以放请求头也可以放查询参数。

    小程序里 `wx.request` 带自定义头没有问题，但 `wx.downloadFile`（下载 ICS）
    不能带自定义头，所以这两条通道都必须支持。
    """
    token = (headers.get("x-token") or "").strip()
    if token:
        return token
    auth = (headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (query_token or "").strip()


def current_binding(db: Session, token: str) -> WechatBinding:
    """校验本地票据，返回绑定记录；过期与不存在一并按 401 处理。"""
    if not token:
        raise HTTPException(status_code=401, detail="缺少登录票据，请重新进入小程序")
    binding = db.scalar(select(WechatBinding).where(WechatBinding.token == token))
    if binding is None:
        raise HTTPException(status_code=401, detail="登录票据无效，请重新登录")
    expire = binding.token_expire_at
    if expire and expire < dt.datetime.now():
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return binding


def _binding_payload(binding: WechatBinding, user: User) -> dict:
    return {
        "token": binding.token,
        "expire_at": binding.token_expire_at.isoformat(timespec="seconds"),
        "openid_mode": "guest" if binding.openid.startswith("mock_") else "wechat",
        "user": {
            "id": user.id,
            "name": user.name,
            "grade": user.grade,
            "major": user.major,
            "interests": _load(user.interests, []),
            "student_no": user.student_no,
        },
    }


# ---------------------------------------------------------------- 微信登录
@router.post("/wechat/login")
def wechat_login(payload: WechatLoginIn, db: Session = Depends(get_db)):
    """小程序登录：真实模式走 code2session，未配置时走游客演示模式。

    两种模式返回同一个结构，前端小程序不需要写两套分支。
    """
    if settings.wechat_enabled:
        if not payload.code:
            raise HTTPException(status_code=400, detail="缺少 wx.login 的 code")
        try:
            session = wx.code2session(payload.code)
        except wx.WechatError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        openid = session["openid"]
        unionid = session.get("unionid", "")
        mode = "wechat"
    else:
        # 游客模式：没有小程序账号也能完整演示，但数据一眼可辨（openid 前缀 mock_）
        openid = wx.guest_openid(payload.device_key or payload.nickname)
        unionid = ""
        mode = "guest"

    binding = db.scalar(select(WechatBinding).where(WechatBinding.openid == openid))
    if binding is None:
        # 首次进入：自动建一个画像空壳，学生随后在小程序「我的」里补充年级专业
        user = User(
            name=(payload.nickname or "同学").strip()[:40] or "同学",
            grade="",
            major="",
            interests="[]",
            student_no="",
        )
        db.add(user)
        db.flush()  # 拿到自增 id，后面建绑定要用

        token, expire = wx.issue_token()
        binding = WechatBinding(
            openid=openid,
            unionid=unionid,
            user_id=user.id,
            nickname=payload.nickname or "",
            avatar=payload.avatar or "",
            token=token,
            token_expire_at=expire,
        )
        db.add(binding)
    else:
        user = db.get(User, binding.user_id)
        if user is None:  # 用户被删除但绑定残留：重建画像，避免整条链路 500
            user = User(name=payload.nickname or "同学", interests="[]")
            db.add(user)
            db.flush()
            binding.user_id = user.id
        token, expire = wx.issue_token()
        binding.token = token
        binding.token_expire_at = expire
        binding.last_login_at = dt.datetime.now()
        if unionid:
            binding.unionid = unionid
        if payload.nickname:
            binding.nickname = payload.nickname

    db.commit()
    db.refresh(binding)
    db.refresh(user)

    data = _binding_payload(binding, user)
    data["mode"] = mode
    data["mode_label"] = "微信账号登录" if mode == "wechat" else "游客演示模式（未配置 AppID/Secret）"
    return data


@router.get("/wechat/status")
def wechat_status():
    """多端接入状态：配置是否齐备、推送能不能用、当前是哪种登录模式。"""
    return {
        **wx.describe(),
        "platforms": {
            "web": {"available": True, "entry": "/"},
            "pwa": {"available": True, "manifest": "/manifest.webmanifest"},
            "miniprogram": {"available": True, "entry": "miniprogram/"},
            "calendar": {"available": True, "subscription": "/api/calendar.ics"},
        },
        "capabilities": [
            {"key": "checklist", "name": "办理清单拆解", **checklist_mod.describe()},
            {"key": "conflict", "name": "跨来源冲突检测", "strategy": "标题 2-gram + 同分类 + 时间窗"},
            {"key": "calendar", "name": "日历订阅", "format": "ICS"},
        ],
    }


@router.get("/wechat/me")
def wechat_me(
    token: str = Query(""),
    x_token: str = Header(default=""),
    db: Session = Depends(get_db),
):
    binding = current_binding(db, _token_from({"x-token": x_token}, token))
    user = db.get(User, binding.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="账号不存在，请重新登录")
    return _binding_payload(binding, user)


# ---------------------------------------------------------------- 订阅消息
@router.post("/wechat/subscribe")
def wechat_subscribe(
    payload: SubscribeIn,
    token: str = Query(""),
    x_token: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """记录用户的订阅授权。

    微信一次性订阅的语义是「用户点一次允许，只能下发一条」，所以这里
    按「模板 + 通知」累加 count，而不是简单置一个布尔开关。
    """
    binding = current_binding(db, _token_from({"x-token": x_token}, token))
    template_id = (payload.template_id or settings.WECHAT_TEMPLATE_DEADLINE).strip()

    if not template_id:
        # 没配模板不算错误：降级为站内提醒，把原因原样告诉前端
        return {
            "ok": False,
            "code": "no_template",
            "message": "未配置订阅消息模板，已降级为小程序内提醒",
            "remain_count": 0,
        }

    record = db.scalar(
        select(PushSubscription).where(
            PushSubscription.user_id == binding.user_id,
            PushSubscription.template_id == template_id,
            PushSubscription.notice_id == payload.notice_id,
        )
    )
    if record is None:
        record = PushSubscription(
            user_id=binding.user_id,
            openid=binding.openid,
            template_id=template_id,
            notice_id=payload.notice_id,
            remain_count=payload.count,
            status="granted",
        )
        db.add(record)
    else:
        record.remain_count = min(99, (record.remain_count or 0) + payload.count)
        record.status = "granted"
        record.last_error = ""
        record.openid = binding.openid

    db.commit()
    db.refresh(record)
    return {
        "ok": True,
        "code": "granted",
        "message": f"已记录 {record.remain_count} 次提醒授权",
        "remain_count": record.remain_count,
        "push_ready": settings.wechat_push_ready,
    }


@router.get("/wechat/subscriptions")
def list_subscriptions(
    token: str = Query(""),
    x_token: str = Header(default=""),
    db: Session = Depends(get_db),
):
    binding = current_binding(db, _token_from({"x-token": x_token}, token))
    rows = db.scalars(
        select(PushSubscription)
        .where(PushSubscription.user_id == binding.user_id)
        .order_by(PushSubscription.created_at.desc())
    ).all()
    return {
        "push_ready": settings.wechat_push_ready,
        "template_configured": bool(settings.WECHAT_TEMPLATE_DEADLINE),
        "items": [
            {
                "id": r.id,
                "template_id": r.template_id,
                "notice_id": r.notice_id,
                "remain_count": r.remain_count,
                "status": r.status,
                "last_error": r.last_error,
                "last_sent_at": r.last_sent_at.isoformat(timespec="seconds") if r.last_sent_at else None,
            }
            for r in rows
        ],
    }


@router.post("/wechat/push/{task_id}")
def push_task_reminder(
    task_id: int,
    token: str = Query(""),
    x_token: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """对某条待办立即下发一次截止提醒，用于演示与联调。

    无论成功失败都写回 push_subscriptions（last_error / last_sent_at），
    这样「为什么没收到提醒」在页面上可查，而不是只能翻服务器日志。
    """
    binding = current_binding(db, _token_from({"x-token": x_token}, token))
    task = db.get(UserTask, task_id)
    if task is None or task.user_id != binding.user_id:
        raise HTTPException(status_code=404, detail="待办不存在")

    notice = db.get(Notice, task.notice_id)
    if notice is None:
        raise HTTPException(status_code=404, detail="通知不存在")

    analysis = analysis_to_dict(notice.analysis)
    deadline = (analysis or {}).get("deadline") or ""
    d = safe_iso_date(deadline)
    days_left = (d - dt.date.today()).days if d else 0

    record = db.scalar(
        select(PushSubscription).where(
            PushSubscription.user_id == binding.user_id,
            PushSubscription.template_id == settings.WECHAT_TEMPLATE_DEADLINE,
            PushSubscription.notice_id == task.notice_id,
        )
    )

    if record is None or (record.remain_count or 0) <= 0:
        return {
            "ok": False,
            "code": "no_quota",
            "message": "尚未获得该通知的提醒授权，请先在小程序内点击「允许提醒」",
        }

    data = wx.deadline_message_data(notice.title, deadline, days_left, notice.category)
    # 注意：这里的页面路径必须和 miniprogram/app.json 的 pages 完全一致，
    # 写错不会报错，只会在用户点开订阅消息时白屏——自检里有一条断言钉住它。
    result = wx.send_subscribe_message(binding.openid, record.template_id, data,
                                       page=f"pages/notice-detail/notice-detail?id={notice.id}")

    record.last_sent_at = dt.datetime.now()
    if result["ok"]:
        record.remain_count = max(0, record.remain_count - 1)
        record.last_error = ""
        if record.remain_count == 0:
            record.status = "exhausted"
    else:
        record.last_error = f"{result['code']}: {result['message']}"[:200]
        if result["code"] == "no_quota":
            record.status = "revoked"
    db.commit()

    return {
        "ok": result["ok"],
        "code": result["code"],
        "message": result["message"],
        "remain_count": record.remain_count,
        "payload": data,
    }


# ---------------------------------------------------------------- 移动端首屏
@router.get("/mobile/bootstrap")
def mobile_bootstrap(
    token: str = Query(""),
    x_token: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """小程序首屏一次拿全：身份、统计、近期截止、推荐机会。

    串行五六次请求在弱网下很容易把首屏拖过 3 秒，所以打包成一个响应。
    响应体积仍然受控（列表上限 5 条），不会因为「打包」而变慢。
    """
    binding = current_binding(db, _token_from({"x-token": x_token}, token))
    user = db.get(User, binding.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="账号不存在，请重新登录")

    today = dt.date.today()
    rows = db.scalars(select(Notice).options(selectinload(Notice.analysis))).all()

    agenda, matched = [], []
    total_open = 0
    for n in rows:
        a = n.analysis
        if a is None:
            continue
        analysis = analysis_to_dict(a)
        if not analysis.get("actionable", True):
            continue
        d = safe_iso_date(a.deadline)
        if d is not None and d >= today:
            total_open += 1
            if len(agenda) < 5:
                agenda.append({
                    "notice_id": n.id,
                    "title": n.title,
                    "category": n.category,
                    "deadline": d.isoformat(),
                    "days_left": (d - today).days,
                    "deadline_kind": analysis.get("deadline_kind", ""),
                })

        if len(matched) < 5:
            prof = {
                "id": user.id,
                "grade": user.grade,
                "major": user.major,
                "interests": _load(user.interests, []),
            }
            item = match_one(prof, {"id": n.id, "category": n.category}, analysis)
            if item["level"] == "match":
                item["notice"] = {
                    "id": n.id,
                    "title": n.title,
                    "category": n.category,
                    "deadline": analysis.get("deadline"),
                }
                matched.append(item)

    tasks = db.scalars(
        select(UserTask)
        .options(selectinload(UserTask.notice).selectinload(Notice.analysis))
        .where(UserTask.user_id == user.id)
    ).all()
    task_todo = sum(1 for t in tasks if t.status != "done")

    agenda.sort(key=lambda x: x["deadline"])
    matched.sort(key=lambda x: -x["score"])

    return {
        "user": {
            "id": user.id,
            "name": user.name,
            "grade": user.grade,
            "major": user.major,
            "interests": _load(user.interests, []),
        },
        "stats": {
            "open_notices": total_open,
            "todo_tasks": task_todo,
            "matched": len(matched),
            "overdue_soon": sum(1 for x in agenda if x["days_left"] <= 3),
        },
        "agenda": agenda,
        "matched": matched,
        "push_ready": settings.wechat_push_ready,
        "server_time": dt.datetime.now().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------- 办理清单
@router.get("/notices/{notice_id}/checklist")
def get_checklist(notice_id: int, db: Session = Depends(get_db)):
    """办理清单：把「材料名词」升级为「可勾选的顺序步骤」。"""
    notice = db.get(Notice, notice_id)
    if notice is None:
        raise HTTPException(status_code=404, detail="通知不存在")
    return checklist_mod.build(notice_to_dict(notice))


# ---------------------------------------------------------------- 多轨道时间轴
@router.get("/notices/{notice_id}/timeline")
def get_timeline(
    notice_id: int,
    role: str = Query("", description="身份筛选，留空返回全部"),
    track: str = Query("", description="轨道筛选：秋季/冬季/春季/夏季"),
    db: Session = Depends(get_db),
):
    """单条通知的多轨道时间轴。

    与单值 `deadline` 的区别：`deadline` 回答「最晚什么时候」，
    这里回答「我是谁、我该在哪天做什么」。管理侧节点（学院审议、学院报送）
    不会出现在个人视图里，否则学生会为不属于自己的事焦虑。
    """
    notice = db.get(Notice, notice_id)
    if notice is None:
        raise HTTPException(status_code=404, detail="通知不存在")
    a = notice.analysis
    nodes = _load(a.timeline, []) if a is not None else []
    if role:
        nodes = timeline_mod.for_role(nodes, role, track)
    elif track:
        nodes = [n for n in nodes if (n or {}).get("track") == track]

    tracks: list[str] = []
    for node in _load(a.timeline, []) if a is not None else []:
        t = (node or {}).get("track") or ""
        if t and t not in tracks:
            tracks.append(t)

    return {
        "notice_id": notice.id,
        "title": notice.title,
        "deadline": a.deadline if a is not None else None,
        "role": role,
        "track": track,
        "tracks": tracks,
        "nodes": nodes,
        "stats": {
            "total": len(nodes),
            "earliest": nodes[0]["date"] if nodes else "",
            "latest": nodes[-1]["date"] if nodes else "",
        },
    }


@router.get("/timeline")
def my_timeline(
    token: str = Query(""),
    x_token: str = Header(default=""),
    user_id: int = Query(0, description="Web 端直接传登录角色；小程序走票据"),
    track: str = Query("", description="轨道筛选"),
    days: int = Query(180, ge=1, le=730),
    back: int = Query(30, ge=0, le=730),
    db: Session = Depends(get_db),
):
    """个人时间轴：按用户年级自动归一到身份词，只返回该身份要办的节点。

    这是「多轨道时间轴」真正落地的地方——同一份学位授予通知，博士生看到
    预答辩与评阅材料审核，硕士生看到答辩意向申请与评阅申请，学院审议这类
    管理节点两边都不出现。原来单值 deadline 做不到这件事：它只有一个日期，
    谁看到都是同一个数。

    `back` 是向前回看的窗口：刚过期的节点也要出现（days_left 为负），否则
    学生一旦错过就再也看不见，反而不知道发生了什么。默认回看 30 天。

    身份取值有两条路：小程序带票据（`x-token` / `?token=`），Web 端没有
    票据体系、登录只是本地选角色，所以允许直接传 `user_id`。两者都不给时
    才报未登录，避免 Web 端看到一片空白却不知道原因。
    """
    user = None
    if user_id:
        user = db.get(User, user_id)
    else:
        binding = current_binding(db, _token_from({"x-token": x_token}, token))
        user = db.get(User, binding.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="未识别到登录身份，请先登录")

    role = timeline_mod.role_from_user(user.grade, user.major)
    today = dt.date.today()
    limit = today + dt.timedelta(days=days)
    floor = today - dt.timedelta(days=back)

    rows = db.scalars(select(Notice).options(selectinload(Notice.analysis))).all()
    items: list[dict] = []
    for n in rows:
        a = n.analysis
        if a is None or not (a.actionable if a.actionable is not None else True):
            continue
        nodes = timeline_mod.for_role(_load(a.timeline, []), role, track)
        for node in nodes:
            d = safe_iso_date(node.get("date"))
            if d is None or not (floor <= d <= limit):
                continue
            items.append({
                "notice_id": n.id,
                "notice_title": n.title,
                "category": n.category,
                "track": node.get("track", ""),
                "role": node.get("role", ""),
                "action": node.get("action", ""),
                "date": d.isoformat(),
                "days_left": (d - today).days,
                "evidence": node.get("evidence", ""),
            })
    items.sort(key=lambda x: (x["date"], x["track"]))

    return {
        "role": role,
        "grade": user.grade,
        "track": track,
        "back": back,
        "count": len(items),
        "items": items,
    }


# ---------------------------------------------------------------- 冲突检测
@router.get("/conflicts")
def list_conflicts(
    severity: str = Query("", description="blocker / warn / info"),
    resolved: str = Query("", description="true / false"),
    db: Session = Depends(get_db),
):
    stmt = select(NoticeConflict).order_by(NoticeConflict.created_at.desc())
    if severity:
        stmt = stmt.where(NoticeConflict.severity == severity)
    if resolved in ("true", "false"):
        stmt = stmt.where(NoticeConflict.resolved.is_(bool(resolved == "true")))
    rows = db.scalars(stmt).all()

    titles = {}
    need = {r.notice_a_id for r in rows} | {r.notice_b_id for r in rows}
    if need:
        for n in db.scalars(select(Notice).where(Notice.id.in_(need))).all():
            titles[n.id] = n.title

    items = []
    for r in rows:
        items.append({
            "id": r.id,
            "notice_a_id": r.notice_a_id,
            "notice_b_id": r.notice_b_id,
            "title_a": titles.get(r.notice_a_id, ""),
            "title_b": titles.get(r.notice_b_id, ""),
            "field": r.field,
            "field_label": conflict_mod.FIELD_LABELS.get(r.field, r.field),
            "value_a": r.value_a,
            "value_b": r.value_b,
            "severity": r.severity,
            "severity_label": conflict_mod.SEVERITY_LABELS.get(r.severity, r.severity),
            "reason": r.reason,
            "resolved": r.resolved,
            "resolution": r.resolution,
        })
    return {
        "items": items,
        "summary": conflict_mod.summarize([
            {"severity": i["severity"], "field": i["field"], "resolved": i["resolved"]} for i in items
        ]),
    }


@router.post("/conflicts/scan")
def scan_conflicts(db: Session = Depends(get_db)):
    """全量重扫：先清掉未处理的旧结论，再按当前数据重新生成。

    已处理（resolved）的记录保留，避免把人工裁决过的结论冲掉。
    """
    db.query(NoticeConflict).filter(NoticeConflict.resolved.is_(False)).delete()
    db.commit()

    rows = db.scalars(select(Notice).options(selectinload(Notice.analysis))).all()
    payload = [notice_to_dict(n) for n in rows]
    found = conflict_mod.detect(payload)

    # 标题在 detect 里被带出来用于展示，入库时不需要，去掉免得污染字段
    for rec in found:
        rec.pop("similarity", None)
        rec.pop("title_a", None)
        rec.pop("title_b", None)

    for rec in found:
        db.add(NoticeConflict(**rec))
    db.commit()

    return {
        "scanned": len(payload),
        "found": len(found),
        "blocker": sum(1 for r in found if r["severity"] == "blocker"),
        "warn": sum(1 for r in found if r["severity"] == "warn"),
        "info": sum(1 for r in found if r["severity"] == "info"),
    }


@router.patch("/conflicts/{conflict_id}")
def resolve_conflict(conflict_id: int, payload: ConflictResolveIn, db: Session = Depends(get_db)):
    """人工裁决：保留记录但标记已处理，让「谁改的、怎么改的」可追溯。"""
    record = db.get(NoticeConflict, conflict_id)
    if record is None:
        raise HTTPException(status_code=404, detail="冲突记录不存在")
    if payload.resolution not in ("use_a", "use_b", "both_wrong", "ignored"):
        raise HTTPException(status_code=400, detail="resolution 只能是 use_a/use_b/both_wrong/ignored")
    record.resolved = True
    record.resolution = payload.resolution
    db.commit()
    db.refresh(record)
    return {
        "ok": True,
        "id": record.id,
        "resolved": record.resolved,
        "resolution": record.resolution,
    }


# ---------------------------------------------------------------- 日历订阅
@router.get("/calendar.ics", response_class=PlainTextResponse)
def calendar_all(days: int = Query(120, ge=1, le=730), db: Session = Depends(get_db)):
    """全站截止日历：粘到系统日历里即可获得原生提醒。"""
    today = dt.date.today()
    limit = today + dt.timedelta(days=days)
    rows = db.scalars(select(Notice).options(selectinload(Notice.analysis))).all()

    items = []
    for n in rows:
        a = n.analysis
        if a is None or not (a.actionable if a.actionable is not None else True):
            continue
        d = safe_iso_date(a.deadline)
        if d is None or not (today <= d <= limit):
            continue
        items.append({
            "notice_id": n.id,
            "title": n.title,
            "category": n.category,
            "deadline": d.isoformat(),
            "deadline_kind": a.deadline_kind,
        })
    items.sort(key=lambda x: x["deadline"])
    body = ical_mod.build_calendar(items)
    return PlainTextResponse(
        body,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'inline; filename="campusinsight.ics"'},
    )


@router.get("/users/{user_id}/calendar.ics", response_class=PlainTextResponse)
def calendar_user(user_id: int, days: int = Query(120, ge=1, le=730), db: Session = Depends(get_db)):
    """按个人待办导出日历：只包含该用户主动加进来的通知。"""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    today = dt.date.today()
    limit = today + dt.timedelta(days=days)
    tasks = db.scalars(
        select(UserTask)
        .options(selectinload(UserTask.notice).selectinload(Notice.analysis))
        .where(UserTask.user_id == user_id)
    ).all()

    items = []
    for t in tasks:
        if t.status == "done" or t.notice is None or t.notice.analysis is None:
            continue
        d = safe_iso_date(t.notice.analysis.deadline)
        if d is None or not (today <= d <= limit):
            continue
        items.append({
            "notice_id": t.notice_id,
            "title": t.notice.title,
            "category": t.notice.category,
            "deadline": d.isoformat(),
            "deadline_kind": t.notice.analysis.deadline_kind,
        })
    items.sort(key=lambda x: x["deadline"])
    body = ical_mod.build_calendar(items, calendar_name=f"高校智讯 · {user.name} 的截止提醒")
    return PlainTextResponse(
        body,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'inline; filename="campusinsight-user-{user_id}.ics"'},
    )
