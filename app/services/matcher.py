"""个性化匹配与推荐打分：把用户画像与通知解析结果比对，输出三级结论 + 可解释依据。

三级结论：match（符合）/ maybe（可能符合）/ no（暂不符合）
每条结论都必须给出理由，理由中的"依据"直接引用原文或已抽取字段，杜绝黑盒推荐。

打分模型（多因子加权，权重和为 1，得分归一化到 5~98）：
    适用对象 0.28 / 专业限制 0.14 / 兴趣方向 0.20 / 硬性条件 0.08
    时效紧迫 0.12 / 内容新鲜 0.08 / 信息可执行度 0.10

硬冲突（学历不符、专业不符、报名已截止）是否决项：一旦命中直接判「暂不符合」，
不再参与加权——兴趣再像也不能把"你根本没资格"的通知推荐给你。
每个因子的原始值、权重、贡献都会随结果返回，界面可以逐条展示，杜绝黑盒。
"""
from __future__ import annotations

import datetime as dt
import re

from app.services.textutil import safe_iso_date

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

# 相邻主题：高度相关但不完全一致，给部分相似度而不是零分。
# 例：关注「科研」的学生，对「学科竞赛」通常也有兴趣（科创是同一批人在做）。
CATEGORY_AFFINITY = {
    "学科竞赛": {"科研项目"},
    "科研项目": {"学科竞赛"},
    "奖助学金": {"志愿服务"},
    "志愿服务": {"奖助学金", "社会实践"},
    "社会实践": {"志愿服务"},
    "实习就业": {"校园活动"},
    "学术讲座": {"科研项目"},
}

LEVEL_TEXT = {"match": "符合", "maybe": "可能符合", "no": "暂不符合"}

# 因子权重：和为 1.0。调整权重只需改这里，打分与展示同步生效。
FACTOR_WEIGHTS = {
    "target": 0.28,
    "major": 0.14,
    "interest": 0.20,
    "condition": 0.08,
    "urgency": 0.12,
    "freshness": 0.08,
    "quality": 0.10,
}

FACTOR_LABELS = {
    "target": "适用对象",
    "major": "专业限制",
    "interest": "兴趣方向",
    "condition": "硬性条件",
    "urgency": "时效紧迫",
    "freshness": "内容新鲜",
    "quality": "信息可执行度",
}

# 得分区间：fit∈[0,1] 线性映射到 5~98，避免出现 0 分或 100 分这种不可信极值。
SCORE_MIN, SCORE_MAX = 5, 98
MATCH_THRESHOLD = 72

# 否决项惩罚系数：命中硬冲突后，把剩余因子撑起来的分数整体压到低位。
# 否则会出现"84 分 / 暂不符合"这种自相矛盾的卡片——已截止的通知就算主题一模一样，
# 也不该在列表里显示成高分。0.35 让否决项的匹配度落在 20~45 分区间。
VETO_FACTOR = 0.35


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
    if open_all or re.fullmatch(r"(?:全体|所有|在校|全校)?学生", (text or "").strip()):
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


def _user_has_interests(user: dict) -> bool:
    """用户是否真的填了兴趣方向。没填时兴趣因子取中性值，不能当"不感兴趣"处理。"""
    raw = user.get("interests") or []
    if isinstance(raw, str):
        raw = [x for x in raw.replace("，", ",").split(",") if x]
    return any(str(x).strip() for x in raw)


def _interest_fit(user: dict, category: str) -> tuple[float, str]:
    """兴趣相似度：命中 1.0；相邻主题 0.6；完全不相关 0.15；未填兴趣 0.5（中性）。"""
    interests = user.get("interests") or []
    if isinstance(interests, str):
        interests = [i for i in interests.replace("，", ",").split(",") if i]
    mapped = {INTEREST_MAP.get(str(i).strip(), str(i).strip()) for i in interests if str(i).strip()}
    if not mapped:
        return 0.5, "尚未填写兴趣方向，按中性权重参与排序"
    if category in mapped:
        return 1.0, f"与你关注的「{'、'.join(sorted(mapped & {category}))}」方向一致"
    near = CATEGORY_AFFINITY.get(category, set()) & mapped
    if near:
        return 0.6, f"该通知属于「{category}」，与你关注的「{'、'.join(sorted(near))}」主题相邻"
    note = f"该通知属于「{category}」，不在你填写的兴趣方向内"
    return 0.15, note


def _urgency_fit(deadline, today: dt.date | None = None) -> tuple[float, str, bool]:
    """时效紧迫度：临期加分，已截止是否决项。

    返回 (raw, 理由, 是否已截止)。日期一律经 safe_iso_date 解析，
    避免人工修正写进来的脏日期在匹配接口里抛 ValueError。
    """
    today = today or dt.date.today()
    day = safe_iso_date(deadline)
    if day is None:
        if deadline:
            return 0.45, "截止日期格式异常，请打开原文确认", False
        return 0.45, "未识别到明确截止日期，请查看原文确认", False
    days = (day - today).days
    if days < 0:
        return 0.0, f"报名已于 {day.isoformat()} 截止（{-days} 天前）", True
    if days == 0:
        return 1.0, f"今天是报名最后一天（截止 {day.isoformat()}），请立即处理", False
    if days <= 3:
        return 1.0, f"报名仅剩 {days} 天（截止 {day.isoformat()}），建议尽快处理", False
    if days <= 7:
        return 0.88, f"距报名截止还有 {days} 天（{day.isoformat()}）", False
    if days <= 14:
        return 0.72, f"距报名截止还有 {days} 天（{day.isoformat()}）", False
    if days <= 30:
        return 0.55, f"报名截止时间 {day.isoformat()}（还有 {days} 天）", False
    if days <= 60:
        return 0.38, f"报名截止时间 {day.isoformat()}，时间较充裕", False
    return 0.22, f"报名截止时间 {day.isoformat()}，距现在较远", False


def _freshness_fit(notice: dict, analysis: dict, today: dt.date | None = None) -> tuple[float, str]:
    """内容新鲜度：新发布的通知优先展示，避免旧通知长期霸榜。"""
    today = today or dt.date.today()
    raw_time = (notice.get("publish_time") or notice.get("created_at")
                or analysis.get("updated_at") or "")
    day = safe_iso_date(str(raw_time)[:19]) if raw_time else None
    if day is None:
        return 0.5, "发布时间未知，按中性权重参与排序"
    age = (today - day).days
    if age < 0:
        return 0.6, "发布时间晚于今天（可能为预告），按中性偏高权重排序"
    if age <= 3:
        return 1.0, f"{age} 天前更新，属于最新通知"
    if age <= 7:
        return 0.9, f"{age} 天前更新"
    if age <= 30:
        return 0.75, f"约 {age} 天前发布"
    if age <= 90:
        return 0.55, f"约 {age} 天前发布，时效一般"
    if age <= 180:
        return 0.4, f"约 {age} 天前发布，时效偏旧"
    return 0.25, f"约 {age} 天前发布，时效较旧"


QUALITY_FIELDS = ("deadline", "materials", "conditions", "apply_method", "contact", "location")


def _quality_fit(analysis: dict) -> tuple[float, str]:
    """信息可执行度：字段越齐全，学生越能直接照做，排序越靠前。

    这条因子把"解析质量"接进了推荐：只抽到标题、没有截止与材料字段的通知，
    即使主题相关也不该排在信息完整的通知前面。
    """
    filled = 0
    for key in QUALITY_FIELDS:
        value = analysis.get(key)
        if isinstance(value, (list, tuple, dict, set)):
            filled += 1 if value else 0
        else:
            filled += 1 if str(value or "").strip() else 0
    ratio = filled / len(QUALITY_FIELDS)
    try:
        confidence = float(analysis.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    raw = max(0.0, min(1.0, 0.7 * ratio + 0.3 * confidence))
    return raw, f"已抽取 {filled}/{len(QUALITY_FIELDS)} 个关键字段（截止/材料/条件/方式/联系/地点）"


def score_factors(user: dict, notice: dict, analysis: dict,
                  today: dt.date | None = None) -> dict:
    """纯打分：返回因子明细、归一化得分与否决标记，不含最终三档结论。

    与 match_one 分离，方便推荐排序复用同一套因子，也方便单元测试直接核对因子值。
    """
    today = today or dt.date.today()
    category = analysis.get("category") or notice.get("category") or "其他"

    t_state, t_text = _check_target(user, analysis.get("target") or [])
    hard = _check_grade_hard(user, analysis.get("grade_requirement") or [])
    if hard[0]:
        t_state, t_text = hard

    m_state, m_text = _check_major(
        user, analysis.get("conditions") or [], analysis.get("target") or [])
    hard_m = _check_major_hard(user, analysis.get("major_requirement") or [])
    if hard_m[0]:
        m_state, m_text = hard_m

    c_state, c_text = _check_conditions(user, analysis.get("conditions") or [])

    i_raw, i_text = _interest_fit(user, category)
    u_raw, u_text, expired = _urgency_fit(analysis.get("deadline"), today)
    f_raw, f_text = _freshness_fit(notice, analysis, today)
    q_raw, q_text = _quality_fit(analysis)

    target_raw = 1.0 if t_state == "ok" else (0.0 if t_state == "conflict" else 0.45)
    major_raw = {"ok": 1.0, "maybe": 0.55}.get(m_state, 0.0 if m_state == "conflict" else 0.5)
    condition_raw = {"ok": 1.0, "maybe": 0.5}.get(c_state, 0.6)

    raws = {
        "target": (target_raw, t_state, t_text),
        "major": (major_raw, m_state, m_text),
        "interest": (i_raw, "ok" if i_raw >= 1.0 else ("maybe" if i_raw >= 0.5 else "conflict"), i_text),
        "condition": (condition_raw, c_state, c_text),
        "urgency": (u_raw, "conflict" if expired else "ok", u_text),
        "freshness": (f_raw, "ok", f_text),
        "quality": (q_raw, "ok", q_text),
    }

    factors: list[dict] = []
    fit = 0.0
    for key, (raw, state, text) in raws.items():
        weight = FACTOR_WEIGHTS[key]
        contribution = raw * weight
        fit += contribution
        factors.append({
            "key": key,
            "label": FACTOR_LABELS[key],
            "raw": round(raw, 3),
            "weight": weight,
            "contribution": round(contribution, 4),
            "state": state,
            "note": text,
        })

    conflict = (t_state == "conflict" or m_state == "conflict" or expired)
    fit = max(0.0, min(1.0, fit))
    if conflict:
        # 否决项：硬冲突命中时按系数压缩，保证「暂不符合」的卡片分数也低，
        # 界面上的分数与结论不会互相打脸。
        fit *= VETO_FACTOR
    score = int(round(SCORE_MIN + (SCORE_MAX - SCORE_MIN) * fit))
    # 兴趣完全不相关（且用户确实填了兴趣）：资格够但方向不对。
    # 这类通知不给"符合"，否则一个只关心竞赛的同学会看到满屏"志愿服务 符合"。
    interest_off = bool(_user_has_interests(user)) and i_raw <= 0.16
    return {
        "factors": factors,
        "fit": round(fit, 4),
        "raw_fit": round(max(0.0, min(1.0, sum(f["contribution"] for f in factors))), 4),
        "score": max(SCORE_MIN, min(SCORE_MAX, score)),
        "conflict": conflict,
        "expired": expired,
        "interest_off": interest_off,
        "category": category,
    }


def _reason_type(state: str) -> str:
    return {"ok": "good", "conflict": "bad", "maybe": "warn", "unknown": "warn"}.get(state, "info")


def build_reasons(scored: dict, limit: int = 5) -> list[dict]:
    """把因子明细转成给人看的理由，按贡献从高到低取前 limit 条。

    否决项（学历/专业/已截止）永远排在最前，避免用户只看到"兴趣很匹配"就误判。
    """
    factors = list(scored["factors"])
    veto = [f for f in factors if f["state"] == "conflict"]
    rest = sorted([f for f in factors if f["state"] != "conflict"],
                  key=lambda f: -f["contribution"])
    ordered = veto + rest
    return [{"type": _reason_type(f["state"]), "text": f["note"]} for f in ordered[:limit]]


def match_one(user: dict, notice: dict, analysis: dict | None) -> dict:
    """对单个通知计算匹配结论。notice/analysis 为普通字典。"""
    if not analysis:
        return {
            "notice_id": notice["id"],
            "level": "maybe",
            "level_text": LEVEL_TEXT["maybe"],
            "score": 40,
            "fit": 0.4,
            "factors": [],
            "reasons": [{"type": "info", "text": "该通知尚未解析，无法判断适用条件"}],
            "evidence": {},
        }

    scored = score_factors(user, notice, analysis)
    score = scored["score"]
    conflict = scored["conflict"]

    if conflict:
        level = "no"
    elif score >= 72:
        # 资格与分数都够，但兴趣方向完全不符：给「可能符合」而不是「符合」，
        # 避免只关心竞赛的同学看到满屏"志愿服务 符合"。
        level = "maybe" if scored.get("interest_off") else "match"
    else:
        level = "maybe"

    return {
        "notice_id": notice["id"],
        "level": level,
        "level_text": LEVEL_TEXT[level],
        "score": score,
        "fit": scored["fit"],
        "category": scored["category"],
        "factors": scored["factors"],
        "reasons": build_reasons(scored),
        "evidence": analysis.get("evidence") or {},
    }
