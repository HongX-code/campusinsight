"""日历订阅（Capability: Calendar Subscription over ICS）。

## 为什么需要它

「截止提醒」若不进入学生真正会看的地方，就只是系统里的一个数字。
手机系统日历（iOS / Android / HarmonyOS）和 Outlook、钉钉日历都支持 ICS 订阅，
这意味着一份 URL 就能把本系统的截止日期送进用户原本就在用的日程里，
不需要装任何 App，也不需要推送权限。

同时它解决了 PWA / 小程序之外的第三条通路：**系统级提醒**。
对小程序的「一次性订阅消息」是很好的补充——订阅消息可能因用户未授权而失败，
日历提醒却没有这个前提条件。

## 实现要点

- 一律 UTC 输出（DTSTAMP/DTEND 用 Z 结尾），避免客户端时区解释差异；
- 全天事件用 DATE 值类型（VALUE=DATE），截止当天显示为「全天」；
- 每个事件的 UID 由 notice_id 派生且稳定，客户端重复订阅不会产生重复条目；
- 文本字段按 RFC 5545 转义（反斜杠、逗号、分号、换行），否则解析器会截断。
"""
from __future__ import annotations

import datetime as dt

from app.config import settings
from app.services.textutil import safe_iso_date


def _escape(text: str) -> str:
    """RFC 5545 文本转义：反斜杠必须最先替换，否则会把后续转义再转一次。"""
    out = (text or "").replace("\\", "\\\\")
    out = out.replace(";", "\\;").replace(",", "\\,")
    out = out.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return out


def _fold(line: str) -> str:
    """长行折行：RFC 5545 要求每行不超过 75 字节，超出用「空格」续行。

    中文按 UTF-8 一个字 3 字节，因此必须按字节切而不是按字符切。
    两个容易踩的坑，都在下面这版里正面处理了：

    1. 空行必须原样返回。若直接进 while 循环，循环体一次都不执行会返回空串，
       把整行内容静默吞掉——表现是超长标题行从 ICS 里凭空消失，且不报任何错。
    2. 切点不能落在多字节字符中间。判断依据是**断点后的第一个字节**是否为续字节
       （0b10xxxxxx）。不能拿「切下的最后一段的末字节」判断：任一完整多字节字符
       的末字节都是续字节，那样会把一个完整的字误裁掉。
    """
    if not line:
        return line
    data = line.encode("utf-8")
    if len(data) <= 75:
        return line

    chunks: list[str] = []
    first = True
    pos = 0
    total = len(data)

    while pos < total:
        limit = 75 if first else 74  # 续行首部有一个空格，少 1 字节
        cut = min(pos + limit, total)
        # 若断点刚好落在一个字符中间，向前回退到该字符的起始字节
        while cut < total and cut > pos and (data[cut] & 0xC0) == 0x80:
            cut -= 1
        chunks.append(data[pos:cut].decode("utf-8", errors="ignore"))
        pos = cut
        first = False

    return "\r\n ".join(chunks)


def _stamp() -> str:
    return dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def build_calendar(items: list[dict], calendar_name: str = "高校智讯 · 截止提醒") -> str:
    """把 [{notice_id,title,category,deadline,days_left}] 渲染成完整 ICS 文本。"""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//CampusInsight//Gaoxiao Zhixun//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(calendar_name)}",
        "X-WR-TIMEZONE:Asia/Shanghai",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]

    stamp = _stamp()
    for item in items:
        d = safe_iso_date(item.get("deadline"))
        if d is None:
            continue
        uid = f"notice-{item.get('notice_id')}@{settings.CALENDAR_UID_DOMAIN}"
        title = item.get("title") or "校园通知截止"
        desc = (
            f"分类：{item.get('category') or '其他'}\n"
            f"截止类型：{item.get('deadline_kind') or '报名/提交'}\n"
            f"来源：高校智讯（{settings.APP_SUBTITLE}）"
        )
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{stamp}",
            # 全天事件的 DTEND 是「不含」的，所以要 +1 天，否则事件会消失
            f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(d + dt.timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{_escape(title[:120])}",
            f"DESCRIPTION:{_escape(desc)}",
            "TRANSP:TRANSPARENT",
            # 提前 3 天与当天各提醒一次
            "BEGIN:VALARM",
            "TRIGGER:-P3D",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_escape('距离截止还有 3 天')}",
            "END:VALARM",
            "BEGIN:VALARM",
            "TRIGGER:-PT9H",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_escape('今天截止')}",
            "END:VALARM",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def describe() -> dict:
    return {
        "capability": "日历订阅",
        "format": "ICS (RFC 5545)",
        "uid_domain": settings.CALENDAR_UID_DOMAIN,
        "alarms": ["提前 3 天", "当天 09:00（UTC+8）"],
        "refresh_interval": "PT12H",
    }
