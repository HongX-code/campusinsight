"""个性化匹配：把用户画像与通知解析结果比对，输出三级结论 + 可解释依据。

三级结论：match（符合）/ maybe（可能符合）/ no（暂不符合）
每条结论都必须给出理由，理由中的"依据"直接引用原文或已抽取字段，杜绝黑盒推荐。
"""
from __future__ import annotations

import datetime as dt
import re

GRADE_ALIAS = {
    "大一": "本科低年级", "大二": "本科低年级",
    "大三": "本科高年级", "大四": "本科高年级",
    "研一": "研究生", "研二": "研究生", "研三": "研究生", "博士": "研究生",
}

INTEREST_MAP = {
    "竞赛": "学科竞赛", "比赛": "学科竞赛", "学科竞赛": "学科竞赛", "科创": "学科竞赛",
    "奖学金": "奖助学金", "助学金": "奖助学金", "资助": "奖助学金",
    "讲座": "学术讲座", "学术": "学术讲座", "报告": "学术讲座",
    "科研": "科研项目", "课题": "科研项目", "大创": "科研项目",
    "志愿": "志愿服务", "公益": "志愿服务",
    "实践": "社会实践", "调研": "社会实践",
    "就业": "实习就业", "实习": "实习就业", "招聘": "实习就业",
    "活动": "校园活动", "文体": "校园活动",
}

LEVEL_TEXT = {"match": "符合", "maybe": "可能符合", "no": "暂不符合"}


def _grade_family(grade: str) -> str:
    return GRADE_ALIAS.get((grade or "").strip(), "")


def _check_target(user: dict, target: list[str]) -> tuple[str, str]:
    """返回 (结论, 理由)。结论取值：ok / conflict / unknown"""
    text = " ".join(target)
    if not target:
        return "unknown", "通知未明确写出适用对象，需人工确认"

    grade = (user.get("grade") or "").strip()
    family = _grade_family(grade)

    open_all = any(k in text for k in ("全体", "不限", "在校学生", "全体学生", "所有学生", "全校"))
    if open_all:
        return "ok", f"通知面向全体学生（依据：{text[:40]}），与你身份不冲突"

    if "研究生" in text or "硕士" in text or "博士" in text:
        if family == "研究生":
            return "ok", "通知面向研究生，与你当前学历层次一致"
        return "conflict", "通知仅面向研究生，你当前为本科生"

    if "本科生" in text or "本科" in text:
        if family == "研究生":
            return "conflict", "通知面向本科生，你当前为研究生"
        # 年级细分
        hit = [g for g in ("大一", "大二", "大三", "大四") if g in text]
        if hit and grade and grade not in hit:
            return "conflict", f"通知限定{'、'.join(hit)}，你当前为{grade}"
        if hit and grade in hit:
            return "ok", f"通知限定{'、'.join(hit)}，你在适用范围内"
        return "ok", "通知面向本科生，你在适用范围内"

    hit = [g for g in ("大一", "大二", "大三", "大四") if g in text]
    if hit:
        if grade in hit:
            return "ok", f"通知限定{'、'.join(hit)}，你在适用范围内"
        if grade:
            return "conflict", f"通知限定{'、'.join(hit)}，你当前为{grade}"

    return "maybe", f"适用对象表述为「{text[:30]}」，需自行判断是否属于该范围"


def _check_grade_hard(user: dict, req: list[str]) -> tuple[str, str]:
    """硬条件：年级 / 学历限制（技术文档2 第 32 章）。

    设计文档要求专业、年级、学历走规则硬判断，早期版本只能拿 target 自由文本
    做包含判断。现在 grade_requirement 是归一化枚举，可以直接判定冲突。
    """
    if not req:
        return "", ""
    grade = (user.get("grade") or "").strip()
    family = _grade_family(grade)
    levels = [r for r in req if r in ("本科生", "硕士研究生", "博士研究生", "研究生",
                                      "专科生", "毕业生", "教师")]
    concrete = [r for r in req if r in ("大一", "大二", "大三", "大四", "研一", "研二", "研三")]
    cohort = [r for r in req if re.fullmatch(r"20\d{2}级|[一二三四]级", r or "")]

    if not grade:
        return "unknown", "尚未填写年级，无法核对通知的年级限制（%s）" % "、".join(req[:3])
    if levels:
        if family == "研究生":
            ok = any(x in ("研究生", "硕士研究生", "博士研究生") for x in levels)
        else:
            ok = "本科生" in levels or "毕业生" in levels
        # 「本科生、研究生均可」这类会同时给出两个层次，任意命中即通过
        if not ok:
            return "conflict", "通知限定 %s，你当前为 %s" % ("、".join(levels), grade)
    if concrete and grade not in concrete:
        return "conflict", "通知限定 %s，你当前为 %s" % ("、".join(concrete), grade)
    if concrete and grade in concrete:
        return "ok", "通知限定 %s，你在适用范围内" % "、".join(concrete)
    if cohort:
        return "maybe", "通知限定 %s，入学年份需自行核对" % "、".join(cohort)
    return "ok", "通知的年级/学历限制为 %s，你在适用范围内" % "、".join(levels or req[:3])


def _check_major_hard(user: dict, req: list[str]) -> tuple[str, str]:
    """硬条件：专业 / 学院限制。明确列出且完全不命中时直接判「暂不符合」。"""
    if not req:
        return "", ""
    major = (user.get("major") or "").strip()
    if not major:
        return "unknown", "尚未填写专业，无法核对通知的专业限制（%s）" % "、".join(req[:3])
    # 只做词级双向包含，不用「前两个字」这类短前缀：
    # 「信息管理与信息系统」的前两字「信息」是「电子信息」的子串，短前缀会造成假命中。
    stem = re.sub(r"(?:专业|学院|系)$", "", major)
    for r in req:
        key = re.sub(r"(?:专业|学院|学科)$", "", r)
        if len(key) >= 2 and (key in stem or stem in key):
            return "ok", "通知限定专业「%s」，与你的专业「%s」相符" % (r, major)
    return "conflict", "通知限定专业 %s，你的专业「%s」不在范围内" % (
        "、".join("「%s」" % r for r in req[:4]), major)


def _check_major(user: dict, conditions: list[str], target: list[str]) -> tuple[str, str]:
    major = (user.get("major") or "").strip()
    joined = " ".join(conditions + target)
    if not major:
        return "unknown", "尚未填写专业，无法判断专业限制"

    restricted = [kw for kw in ("专业", "学科门类", "学院") if kw in joined]
    if not restricted:
        return "ok", "通知未设置专业限制"

    # 专业关键词直接命中
    core = [seg for seg in (major, major[:2]) if seg]
    if any(seg and seg in joined for seg in core):
        return "ok", f"通知的专业要求包含「{major}」相关方向"

    return "maybe", "通知存在专业方向限制，你的专业是否属于该范围需人工确认"


def _check_conditions(user: dict, conditions: list[str]) -> tuple[str, str]:
    if not conditions:
        return "unknown", "通知未列明硬性资格条件"
    hard = [c for c in conditions if any(k in c for k in ("成绩", "排名", "挂科", "四级", "六级", "绩点", "GPA", "无违纪"))]
    if not hard:
        return "ok", "条件以流程性要求为主，未发现硬性门槛"
    return "maybe", f"存在需自行核对的硬性条件：{hard[0][:40]}"


def match_one(user: dict, notice: dict, analysis: dict | None) -> dict:
    """对单个通知计算匹配结论。notice/analysis 为普通字典。"""
    interests = user.get("interests") or []
    if isinstance(interests, str):
        interests = [i for i in interests.replace("，", ",").split(",") if i]
    mapped = {INTEREST_MAP.get(i.strip(), i.strip()) for i in interests if i.strip()}

    if not analysis:
        return {
            "notice_id": notice["id"],
            "level": "maybe",
            "level_text": LEVEL_TEXT["maybe"],
            "score": 40,
            "reasons": [{"type": "info", "text": "该通知尚未解析，无法判断适用条件"}],
        }

    reasons: list[dict] = []
    score = 30
    conflict = False

    # 1) 截止时间
    deadline = analysis.get("deadline")
    if deadline:
        try:
            days = (dt.date.fromisoformat(deadline) - dt.date.today()).days
            if days < 0:
                conflict = True
                reasons.append({"type": "bad", "text": f"报名已于 {deadline} 截止（{-days} 天前）"})
            elif days <= 3:
                score += 6
                reasons.append({"type": "warn", "text": f"报名仅剩 {days} 天（截止 {deadline}），建议尽快处理"})
            elif days <= 14:
                score += 4
                reasons.append({"type": "good", "text": f"距报名截止还有 {days} 天（{deadline}）"})
            else:
                score += 1
                reasons.append({"type": "info", "text": f"报名截止时间 {deadline}"})
        except ValueError:
            pass
    else:
        reasons.append({"type": "warn", "text": "未识别到明确截止日期，请查看原文确认"})

    # 2) 适用对象 / 年级硬条件：结构化字段优先，自由文本兜底
    t_state, t_text = _check_target(user, analysis.get("target") or [])
    hard = _check_grade_hard(user, analysis.get("grade_requirement") or [])
    if hard[0]:
        t_state, t_text = hard
    if t_state == "conflict":
        conflict = True
        score -= 30
    elif t_state == "ok":
        score += 12
    else:
        score -= 5
    reasons.append({"type": "good" if t_state == "ok" else ("bad" if t_state == "conflict" else "warn"), "text": t_text})

    # 3) 专业硬条件：结构化字段优先；明确冲突时与学历冲突一样直接判「暂不符合」
    m_state, m_text = _check_major(user, analysis.get("conditions") or [], analysis.get("target") or [])
    hard_m = _check_major_hard(user, analysis.get("major_requirement") or [])
    if hard_m[0]:
        m_state, m_text = hard_m
    if m_state == "conflict":
        conflict = True
        score -= 30
    elif m_state == "maybe":
        score -= 4
    elif m_state == "ok":
        score += 6
    reasons.append({
        "type": "good" if m_state == "ok" else ("bad" if m_state == "conflict" else "warn"),
        "text": m_text,
    })

    # 4) 条件
    c_state, c_text = _check_conditions(user, analysis.get("conditions") or [])
    if c_state == "maybe":
        score -= 3
    elif c_state == "ok":
        score += 4
    reasons.append({"type": "good" if c_state == "ok" else "info", "text": c_text})

    # 5) 兴趣
    category = analysis.get("category") or notice.get("category") or "其他"
    if mapped and category in mapped:
        score += 26
        reasons.append({"type": "good", "text": f"与你关注的「{'、'.join(sorted(mapped & {category}))}」方向一致"})
    elif mapped:
        score -= 8
        reasons.append({"type": "info", "text": f"该通知属于「{category}」，不在你填写的兴趣方向内"})

    score = max(5, min(98, score))
    if conflict:
        level = "no"
    elif score >= 72:
        level = "match"
    else:
        level = "maybe"

    return {
        "notice_id": notice["id"],
        "level": level,
        "level_text": LEVEL_TEXT[level],
        "score": score,
        "reasons": reasons,
        "evidence": analysis.get("evidence") or {},
    }
