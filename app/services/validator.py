"""规则校验层：AI 负责理解，程序负责约束，原文负责证明。

对应设计文档：
  · 技术文档1 第 26 章「deadline >= publish_time，否则标记待审核」
  · 技术文档1 第 46 章「数据质量控制：异常时间标记异常」
  · 技术文档2 第 20 章「时间统一为 YYYY-MM-DD」
  · 技术文档2 第 24、57 章「AI 结果可解释、可验证」
  · 技术文档2 第 26 章「confidence < 0.7 可以设置 pending_review」

校验结果分三档，避免一刀切：
    issues   —— 硬异常，一定置 needs_review；
    warnings —— 软提示，只写进复核说明，不改状态（规则引擎置信度天然偏低，
                 若把「置信度 < 0.7」当硬拦截，会把几乎全部通知标红，反而失去复核意义）；
    passed   —— 通过项，前端可展示「本次通过 N 项校验」。
"""
from __future__ import annotations

import re

from app.services.textutil import safe_iso_date as _iso

DATE_FIELDS = ("deadline", "start_time", "registration_start", "event_time")
MAX_SPAN_DAYS = 730


def _norm(text: str) -> str:
    return re.sub(r"[\s\u3000]", "", text or "")


def _grounded(fragment: str, text: str) -> bool:
    head = _norm(fragment)[:8]
    return not head or head in _norm(text)


def audit(result: dict, text: str, publish: str) -> dict:
    """对一份解析结果做规则校验，返回 {passed, issues, warnings, ok}。"""
    passed: list[str] = []
    issues: list[str] = []
    warnings: list[str] = []

    # C1 日期格式与真实性
    bad_fmt = [k for k in DATE_FIELDS if result.get(k) and _iso(result[k]) is None]
    if bad_fmt:
        issues.append("日期字段格式非法：%s" % "、".join(bad_fmt))
    else:
        passed.append("C1 日期格式与真实性")

    pub = _iso(publish)
    ddl = _iso(result.get("deadline"))
    start = _iso(result.get("registration_start"))
    event = _iso(result.get("event_time"))

    # C2 截止不得早于发布时间（技术文档1 第 26 章）
    if ddl and pub and ddl < pub:
        issues.append("截止日期 %s 早于发布时间 %s" % (ddl, pub))
    elif pub is None and ddl:
        warnings.append("缺少发布时间，未做时间先后校验")
        passed.append("C2 时间先后（部分跳过）")
    else:
        passed.append("C2 截止 ≥ 发布")

    # C3 报名起止顺序 + 合理跨度
    if start and ddl and start > ddl:
        issues.append("报名开始 %s 晚于截止 %s" % (start, ddl))
    else:
        passed.append("C3 报名起止顺序")
    if ddl and pub and (ddl - pub).days > MAX_SPAN_DAYS:
        issues.append("截止日期距发布超过 %d 天，疑似年份推断错误" % MAX_SPAN_DAYS)
    else:
        passed.append("C4 时间跨度合理")

    # C5 原文证据接地
    ev = result.get("evidence") or {}
    ungrounded = [k for k, v in ev.items() if v and not _grounded(str(v), text)]
    if ungrounded:
        issues.append("证据片段无法在原文定位：%s" % "、".join(ungrounded))
    else:
        passed.append("C5 证据原文接地")

    # C6 关键日期必须能在原文找到对应写法。
    # 相对时间（「本周五前」）是结合发布时间算出来的，原文里自然没有数字日期，
    # 它的依据是 resolve_relative 命中的那一整行，已由 C5 接地校验覆盖。
    if ddl and result.get("deadline_source") == "relative":
        passed.append("C6 截止日期由相对时间换算（附原文行）")
    elif ddl:
        cands = (f"{ddl.year}年{ddl.month}月{ddl.day}日", ddl.isoformat(),
                 f"{ddl.month}月{ddl.day}日", f"{ddl.year}/{ddl.month}/{ddl.day}")
        if not any(c in _norm(text) for c in cands):
            issues.append("截止日期 %s 未能在原文定位，已置空待复核" % ddl)
            result["deadline"] = None
            result["deadline_kind"] = ""
        else:
            passed.append("C6 截止日期原文可定位")
    else:
        passed.append("C6 截止日期原文可定位（空值）")

    # C7 活动时间与报名截止同日 —— 常见于把举办时间误当截止时间
    if event and ddl and event == ddl:
        warnings.append("活动时间与办理截止为同一天，建议核对")
    else:
        passed.append("C7 活动时间未与截止冲突")

    # C8 置信度：软提示，不硬拦截
    conf = result.get("confidence")
    try:
        conf = float(conf or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf < 0.7:
        warnings.append("解析置信度 %.2f 偏低，建议人工核对关键字段" % conf)
    else:
        passed.append("C8 置信度达标")

    # C9 新闻稿不得携带办理类字段（复核文体闸门是否生效）
    if not result.get("actionable", True):
        leaked = [k for k in ("deadline", "target", "conditions", "materials",
                              "grade_requirement", "major_requirement", "location")
                  if result.get(k)]
        if leaked:
            issues.append("新闻资讯仍携带办理字段：%s" % "、".join(leaked))
        else:
            passed.append("C9 文体闸门无字段泄漏")

    return {"passed": passed, "issues": issues, "warnings": warnings, "ok": not issues}
