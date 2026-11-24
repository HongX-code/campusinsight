"""通知分类：基于领域词典的加权打分器（作为规则引擎与 LLM 的分类校验基线）。"""
from __future__ import annotations

import re

from app.config import CATEGORIES

# 每类的关键词及其权重；标题命中权重 ×2
CATEGORY_KEYWORDS: dict[str, dict[str, float]] = {
    "学科竞赛": {
        "竞赛": 5, "大赛": 5, "比赛": 4, "赛项": 4, "参赛": 4, "选拔赛": 5, "初赛": 4,
        "决赛": 4, "一等奖": 3, "获奖": 3, "奖项": 3, "队伍": 3, "赛道": 3, "挑战杯": 5,
        "数学建模": 5, "蓝桥杯": 5, "ACM": 4, "华为ICT": 6, "创新赛": 5, "奥林匹克": 4,
    },
    "奖助学金": {
        "奖学金": 6, "助学金": 6, "励志奖学金": 6, "国家奖学金": 6, "助学贷款": 5,
        "资助": 4, "家庭经济困难": 5, "困难认定": 5, "勤工助学": 5, "补助": 4,
        "奖金": 3, "评定": 3, "名额": 2, "励志": 4,
    },
    "学术讲座": {
        "讲座": 6, "报告会": 6, "学术报告": 6, "论坛": 4, "研讨会": 5, "沙龙": 4,
        "主讲人": 5, "特邀": 3, "学术前沿": 4, "院士": 3, "教授": 2, "腾讯会议": 2,
        "ZOOM": 3, "线上直播": 3, "学术会议": 8, "征稿": 8, "投稿": 4, "论文录用": 5,
    },
    "科研项目": {
        "科研": 5, "课题": 4, "项目申报": 5, "立项": 5, "大创": 5, "创新创业训练": 6,
        "国创": 5, "基金": 3, "实验室": 3, "指导教师": 3, "中期检查": 4, "结题": 5,
        "研究成果": 4, "专利申请": 4,
    },
    "志愿服务": {
        "志愿": 6, "志愿服务": 7, "义工": 6, "公益": 5, "支教": 6, "无偿献血": 6,
        "志愿者": 6, "服务时长": 5, "西部计划": 5, "社区服务": 5,
    },
    "社会实践": {
        "社会实践": 8, "暑期社会实践": 8, "寒假社会实践": 8, "三下乡": 7, "调研": 4,
        "实习实践": 4, "社会调查": 5, "实践团队": 5, "走访": 3,
    },
    "校园活动": {
        "文艺": 8, "晚会": 8, "运动会": 10, "社团": 6, "文化节": 8, "比赛活动": 2,
        "歌手": 10, "演讲比赛": 6, "摄影": 8, "征文": 8, "宿舍": 4, "心理健康": 6,
        "长跑": 10, "十佳": 8, "新生": 2, "校园": 3, "学生会": 8, "竞选": 8,
        "换届": 7, "开放日": 8, "主席团": 7,
    },
    "实习就业": {
        "招聘": 6, "宣讲会": 6, "双选会": 6, "就业": 5, "实习": 5, "岗位": 4,
        "简历投递": 6, "校招": 6, "面试": 4, "用人单位": 5, "职业规划": 4, "offer": 3,
    },
}

# 强区分信号：出现即说明是文体活动，而非学科竞赛
CAMPUS_STRONG = ("歌手", "晚会", "文艺", "运动会", "长跑", "摄影", "征文", "文化节", "社团", "心理健康", "十佳")
# 出现志愿类信号时，说明"运动会"等只是服务场景，不应判为文体活动
VOL_STRONG = ("志愿服务", "志愿者", "义工", "公益", "支教", "无偿献血", "服务时长")

# 行政事务类通知：研究生招生、学位授予、学籍培养、工作站评估……
# 这类通知在 9 分类里最接近"其他"，但正文里常夹带"奖助""选拔""新生"等词，
# 会被误判成奖助学金或校园活动。命中这些词且没有明确的体裁强信号时归"其他"，
# 避免学生端把「硕博连读选拔」当成文体活动推荐。
ADMIN_TERMS = ("招生", "报考", "推免", "免试", "硕博连读", "学位授予", "答辩", "学籍",
               "培养方案", "培养计划", "工作站", "评估", "简章", "拟录取", "调剂",
               "开题", "中期检查", "结题验收", "教务", "选课", "学籍异动", "公示")
_ENTITY_STRONG = CAMPUS_STRONG + VOL_STRONG + ("竞赛", "大赛", "讲座", "报告会", "招聘", "宣讲会")

_CLEAN_RE = re.compile(r"[\s\u3000]+")


def _score(text: str, weights: dict[str, float]) -> float:
    return sum(w * text.count(kw) for kw, w in weights.items() if kw in text)


def classify(title: str, content: str) -> tuple[str, float, dict[str, float]]:
    """返回 (类别, 归一化置信度, 各类原始得分)。全部未命中时归入"其他"。"""
    head = _CLEAN_RE.sub("", title or "")
    body = _CLEAN_RE.sub("", content or "")[:2500]
    scores: dict[str, float] = {}
    for cat, weights in CATEGORY_KEYWORDS.items():
        scores[cat] = round(_score(head, weights) * 2 + _score(body, weights), 2)

    # 文体活动与学科竞赛共用"大赛/比赛"这类词，需要强信号纠偏。
    # 但纠偏只能用于"竞赛 vs 文体"的歧义，不能无视"讲座/志愿服务"等明确体裁——
    # 否则"心理健康讲座"会因为含"心理健康"被强行改判为校园活动。
    plain = (title or "") + (content or "")
    is_vol = any(k in plain for k in VOL_STRONG)
    if is_vol:
        scores["志愿服务"] += 15

    top_cat, top_score = max(scores.items(), key=lambda kv: kv[1])
    has_campus_signal = any(k in plain for k in CAMPUS_STRONG) and not is_vol
    if has_campus_signal and (top_cat in ("学科竞赛", "其他") or scores["校园活动"] >= top_score - 12):
        scores["校园活动"] += 40

    # 行政事务纠偏：标题是招生/学位/培养类，且没有明确的体裁强信号时归"其他"。
    # 只看标题——正文里出现"奖学金""新生""选拔"太常见，用正文判断必然误伤。
    head_admin = any(k in head for k in ADMIN_TERMS)
    head_entity = any(k in head for k in _ENTITY_STRONG)
    if head_admin and not head_entity:
        for cat in ("校园活动", "奖助学金", "科研项目"):
            if scores.get(cat, 0) > 0:
                scores[cat] *= 0.25          # 按比例压，正文很长时减固定分压不住
        scores["其他"] = max(scores.get("其他", 0.0), 1.0)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_cat, top_score = ranked[0]
    if top_score <= 0:
        return "其他", 0.0, scores

    second = ranked[1][1] if len(ranked) > 1 else 0.0
    # 领先幅度越大越可信；绝对分越高越可信
    margin = (top_score - second) / top_score if top_score else 0.0
    conf = min(0.95, 0.45 + 0.35 * margin + min(top_score, 30) / 100.0)
    return top_cat, round(conf, 2), scores


def normalize_category(value: str) -> str:
    """把模型可能输出的近义表述映射回标准分类。"""
    if not value:
        return "其他"
    value = value.strip()
    if value in CATEGORIES:
        return value
    alias = {
        "竞赛": "学科竞赛", "比赛": "学科竞赛", "学科竞赛类": "学科竞赛", "科技创新": "学科竞赛",
        "奖学金": "奖助学金", "助学金": "奖助学金", "奖助": "奖助学金", "资助": "奖助学金",
        "讲座": "学术讲座", "学术报告": "学术讲座", "报告": "学术讲座",
        "科研": "科研项目", "项目": "科研项目", "创新创业": "科研项目",
        "志愿": "志愿服务", "公益": "志愿服务",
        "实践": "社会实践",
        "活动": "校园活动", "文体": "校园活动",
        "就业": "实习就业", "招聘": "实习就业", "实习": "实习就业",
    }
    for key, cat in alias.items():
        if key in value:
            return cat
    return "其他"
