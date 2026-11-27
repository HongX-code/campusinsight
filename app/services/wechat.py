"""微信小程序服务端能力：登录换 openid、签发本地票据、下发订阅消息。

设计原则与全站一致——**任何外部依赖都必须可降级**：

1. 没配 WECHAT_APPID/SECRET：登录接口自动进入「游客演示模式」，按前端传来的
   device_key 派生一个固定的演示账号。答辩现场没有小程序账号也能完整演示，
   而接口契约与真实模式完全一致，切换只需填两个环境变量。
2. 配了 AppID 但微信接口不通（网络/额度/被限流）：登录直接返回明确错误码，
   绝不静默伪造 openid —— 伪造的 openid 会让后续推送永远失败且查不出原因。
3. 没配模板 ID：推送降级为「站内提醒」，只记录不发送，接口仍返回明确原因。

本地票据（token）是自己签发的，不依赖微信 session_key。原因：微信 session_key
两小时过期且可能因用户重新授权而失效，直接拿它当业务登录态会导致用户莫名掉线。
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
import urllib.parse
from typing import Any

import requests

from app.config import settings

logger = logging.getLogger("zhixun.wechat")

WECHAT_API = "https://api.weixin.qq.com"
TIMEOUT = 8.0


class WechatError(Exception):
    """微信侧明确失败（非网络偶发），携带可展示给用户的原因。"""

    def __init__(self, message: str, code: str = "wechat_error") -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- 登录
def code2session(code: str) -> dict:
    """用 wx.login 的 code 换 openid。仅在配置了 AppID/Secret 时调用。"""
    if not settings.wechat_enabled:
        raise WechatError("未配置微信 AppID/Secret", "not_configured")
    params = {
        "appid": settings.WECHAT_APPID,
        "secret": settings.WECHAT_SECRET,
        "js_code": code,
        "grant_type": "authorization_code",
    }
    url = f"{WECHAT_API}/sns/jscode2session?{urllib.parse.urlencode(params)}"
    try:
        resp = requests.get(url, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise WechatError(f"连接微信服务器失败：{exc}", "network") from exc
    try:
        data = resp.json()
    except ValueError as exc:
        raise WechatError("微信返回了非 JSON 内容", "bad_response") from exc
    if data.get("errcode"):
        raise WechatError(
            f"微信登录失败（{data.get('errcode')}）：{data.get('errmsg', '')}",
            "wechat_reject",
        )
    if not data.get("openid"):
        raise WechatError("微信未返回 openid", "no_openid")
    return data


def guest_openid(device_key: str) -> str:
    """游客模式：从设备标识派生稳定 openid，同一台设备每次登录都是同一个账号。

    加前缀 mock_ 是刻意的——数据库里一眼就能看出这条记录不是真实微信用户，
    不会在答辩演示数据里造成「以为有 300 个真实用户」的误判。
    """
    seed = (device_key or "").strip() or "anonymous-device"
    digest = hashlib.sha256(f"guest::{seed}".encode("utf-8")).hexdigest()
    return f"mock_{digest[:28]}"


def issue_token() -> tuple[str, Any]:
    """签发本地票据，返回 (token, 过期时间)。"""
    import datetime as dt

    token = secrets.token_urlsafe(32)
    expire = dt.datetime.now() + dt.timedelta(days=max(1, settings.WECHAT_TOKEN_TTL_DAYS))
    return token, expire


# ---------------------------------------------------------------- 订阅消息
def send_subscribe_message(
    openid: str,
    template_id: str,
    data: dict[str, dict[str, str]],
    page: str = "pages/index/index",
) -> dict:
    """下发一条订阅消息。返回 {ok, code, message}，从不抛异常。

    调用方通常是后台任务，抛异常只会污染日志、解决不了问题；
    失败信息落库（push_subscriptions.last_error）后可追踪。
    """
    if not settings.wechat_enabled:
        return {"ok": False, "code": "not_configured", "message": "未配置微信 AppID/Secret"}
    if not template_id:
        return {"ok": False, "code": "no_template", "message": "未配置订阅消息模板 ID"}

    access_token, err = _access_token()
    if err:
        return {"ok": False, "code": err, "message": f"获取 access_token 失败：{err}"}

    payload = {
        "touser": openid,
        "template_id": template_id,
        "page": page,
        "data": data,
        # developer/trial/formal 三态由部署环境决定：开发版小程序只能收 developer
        "miniprogram_state": settings.WECHAT_MP_STATE,
        "lang": "zh_CN",
    }
    url = f"{WECHAT_API}/cgi-bin/message/subscribe/send?access_token={access_token}"
    try:
        resp = requests.post(url, json=payload, timeout=TIMEOUT)
        body = resp.json()
    except (requests.RequestException, ValueError) as exc:
        return {"ok": False, "code": "network", "message": f"推送请求失败：{exc}"}

    if body.get("errcode") in (0, None) and body.get("errmsg") in ("ok", None):
        return {"ok": True, "code": "ok", "message": "已下发"}

    errcode = body.get("errcode")
    errmsg = body.get("errmsg", "")
    # 43101: 用户拒收或没有可下发次数（订阅消息最常见的正常失败，不该当异常）
    if errcode == 43101:
        return {"ok": False, "code": "no_quota", "message": "用户未授权或可下发次数已用完"}
    if errcode in (40001, 42001):
        _invalidate_token_cache()
        return {"ok": False, "code": "token_invalid", "message": "access_token 失效，已清除缓存"}
    return {"ok": False, "code": f"wechat_{errcode}", "message": errmsg}


_token_cache: dict[str, Any] = {"token": "", "expire_at": 0.0}


def _access_token() -> tuple[str, str]:
    """获取接口调用凭证，进程内缓存（微信有效期为 7200 秒，这里保守留 300 秒余量）。"""
    now = time.time()
    if _token_cache["token"] and _token_cache["expire_at"] > now:
        return _token_cache["token"], ""

    params = {
        "grant_type": "client_credential",
        "appid": settings.WECHAT_APPID,
        "secret": settings.WECHAT_SECRET,
    }
    url = f"{WECHAT_API}/cgi-bin/token?{urllib.parse.urlencode(params)}"
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        body = resp.json()
    except (requests.RequestException, ValueError) as exc:
        return "", str(exc)

    token = body.get("access_token")
    if not token:
        return "", body.get("errmsg") or "微信未返回 access_token"

    _token_cache["token"] = token
    _token_cache["expire_at"] = now + max(60, int(body.get("expires_in", 7200)) - 300)
    return token, ""


def _invalidate_token_cache() -> None:
    _token_cache["token"] = ""
    _token_cache["expire_at"] = 0.0


# ---------------------------------------------------------------- 消息体构建
def deadline_message_data(notice_title: str, deadline: str, days_left: int, category: str) -> dict:
    """按「截止提醒」模板组装 data 字段。

    微信要求 thing 类型 ≤20 字符、date 类型必须 YYYY-MM-DD，超长会直接报 47003。
    这里统一截断，避免通知标题太长导致整条推送失败。
    """
    when = deadline if len(deadline or "") == 10 else ""
    left = "今天截止" if days_left <= 0 else f"还剩 {days_left} 天"
    return {
        "thing1": {"value": _clip(notice_title or "校园通知")},
        "time2": {"value": when or "—"},
        "thing3": {"value": _clip(category or "其他")},
        "thing4": {"value": _clip(left)},
    }


def _clip(text: str, limit: int = 20) -> str:
    text = (text or "—").strip() or "—"
    return text[: limit - 1] + "…" if len(text) > limit else text


def parse_push_result(result: dict) -> dict:
    """把 send_subscribe_message 的返回值规整成可直接入库/展示的结构。"""
    return {
        "ok": bool(result.get("ok")),
        "code": result.get("code", ""),
        "message": result.get("message", ""),
    }


def describe() -> dict:
    """供 /api/wechat/status 与系统信息页展示，说明当前处于哪种运行模式。"""
    if settings.wechat_enabled:
        mode = "对接微信开放接口"
        mode_label = "微信登录"
    else:
        mode = "游客演示模式（未配置 AppID/Secret）"
        mode_label = "游客演示"
    return {
        "mode": mode,
        "mode_label": mode_label,
        "enabled": settings.wechat_enabled,
        "push_ready": settings.wechat_push_ready,
        "template_configured": bool(settings.WECHAT_TEMPLATE_DEADLINE),
        # 小程序端 requestSubscribeMessage 需要模板 ID，模板 ID 本身就是公开标识符，
        # 不含密钥，所以随状态一起下发，避免前端再维护一份配置。
        "template_id": settings.WECHAT_TEMPLATE_DEADLINE,
        "token_ttl_days": settings.WECHAT_TOKEN_TTL_DAYS,
        "login": "wx.login code2session" if settings.wechat_enabled else "device_key 派生演示账号",
    }


def dumps(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)
