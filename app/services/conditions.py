"""资格条件抽取：把自由表述归一到"原子约束"。

问题背景
--------
高校通知里的资格条件极少写成规范条目，通常混在长句、括号和并列结构里：

    学习成绩排名与综合考评排名均位于本专业前10%，且无不及格科目；
    个人或组合（不超过4人）均可；

如果直接把"逗号切分后的整句"当作一条条件，会同时产生两个后果：
1. 精确率低——整句里混着主语、修饰和并列，和人工标注的原子条目对不上；
2. 不可判定——匹配器无法判断"本专业前10%"到底满足不满足。

本模块的做法是：先用约束类型规则把句子**收缩到一个可判定的最小表述**，
再交给匹配器做数值/枚举比较。规则表按"约束类型"组织而非按样本组织，
新增一种通知体裁时只需补充对应类型的模式。

约束类型：规模（人数）、名额限制、成绩阈值、年级、经济状况、品行纪律、
身体条件、服从性、时间投入、师资、优先项。

返回值的 kind 字段用于区分"规则命中"与"兜底保留"：
- 规则命中是强信号，即使与适用对象重叠也应保留（如"3名学生组队"含"学生"）；
- 兜底保留较弱，在"对象"章节或正文中必须同时出现情态动词才采信。
"""
from __future__ import annotations

import re

# ---------------- 句子级过滤 ----------------

# 说的是"没有限制"，本身不构成约束（"专业不限""不设报名截止日期"）
NEGATION_RE = re.compile(
    r"(不限|不设|不受|不对|无需|不需|不收取|即可参加|均可参加|均可报名)"
    r"|无[^，。；、]{0,8}限制"
)
# 倡议性表述，不是硬性门槛
ADVOCATE_RE = re.compile(r"^(?:鼓励|欢迎|希望|建议|支持|倡导|请广大|请各位)")
# 章节标题残留（"评选条件："）
LABEL_ONLY_RE = re.compile(r"^[^：:]{0,10}[：:]$")
# 内联标签："参赛对象：""评选条件：""【报名对象】我校……"（方括号标签可能不带冒号）
INLINE_LABEL_RE = re.compile(
    r"^\s*[【\[]([^】\]]{2,10})[】\]]"          # 【报名对象】xxx
    r"|^\s*([^：:【】\[\]]{2,10})[：:]"            # 参赛对象：xxx
)
# 情态动词：出现即说明是在提要求，而不是叙述
MODAL_RE = re.compile(r"(须|应当|应具备|需|必须|限|要求|不低于|不少于|符合|具备)")
# 提交类动作：属于材料/流程要求，不是资格条件
SUBMIT_RE = re.compile(r"(签署|提交|上传|附|携带|填写).{0,12}(《|表|书|材料|附件)")

# ---------------- 原子约束规则表 ----------------
# (约束类型, 正则, 归一化函数)
# 顺序有意义：更具体的模式排在更宽泛的模式之前。
CANON_RULES: list[tuple[str, re.Pattern, object]] = [
    # 人数区间：3—5人 / 3-5人 / 3至5人
    ("scale", re.compile(r"(\d{1,2})\s*[—\-~～至]\s*(\d{1,2})\s*[名人]"),
     lambda m: f"{m.group(1)}—{m.group(2)}人"),
    # 人数上限：不超过4人 / 不多于4人 / 最多4人
    ("scale", re.compile(r"(?:不超过|不多于|最多|至多)\s*(\d{1,2})\s*[名人]"),
     lambda m: f"不超过{m.group(1)}人"),
    # 固定人数 + 组织形式：3名学生组队 / 3人团队
    ("scale", re.compile(r"(\d{1,2})\s*名\s*(?:学生|队员|同学)\s*(?:组队|组成|联合)?"),
     lambda m: f"{m.group(1)}名学生组队"),
    ("scale", re.compile(r"(\d{1,2})\s*人\s*(?:团队|组队|小组|一组|组成)"),
     lambda m: f"{m.group(1)}人团队"),
    # 报名次数限制：每人限报一队 / 每人限报2项
    ("quota", re.compile(r"每[人名]\s*限(?:报|选|申报)?\s*([一二三四五六七八九十\d]+)\s*([队项个])"),
     lambda m: f"每人限报{m.group(1)}{m.group(2)}"),
    # 时间投入：每周实习4天 / 每周到岗3天
    ("commitment", re.compile(r"(每周[^，。；、\s]{0,4}?\d+\s*天)"), lambda m: m.group(1)),
    # 成绩排名：专业前10% / 排名前30%
    ("score", re.compile(r"(?:本)?专业\s*前\s*(\d{1,3})\s*[%％]"),
     lambda m: f"专业前{m.group(1)}%"),
    ("score", re.compile(r"前\s*(\d{1,3})\s*[%％]"),
     lambda m: f"专业前{m.group(1)}%"),
    # 分数门槛：不低于80分 / 80分以上
    ("score", re.compile(r"(?:不低于|不少于|达到|在)\s*(\d{2,3})\s*分"),
     lambda m: f"{m.group(1)}分"),
    ("score", re.compile(r"(\d{2,3})\s*分\s*(?:以上|及以上)"),
     lambda m: f"{m.group(1)}分"),
    # 年级要求：二年级及以上 / 二年级以上
    ("grade", re.compile(r"([一二三四])\s*年级\s*(及)?\s*以上"),
     lambda m: f"{m.group(1)}年级及以上" if m.group(2) else f"{m.group(1)}年级以上"),
    # 年龄门槛：40周岁以下 / 35周岁以内（青年教师竞赛、选调生公告高频）
    ("age", re.compile(r"(\d{2})\s*周岁\s*(以下|以上|以内)"),
     lambda m: f"{m.group(1)}周岁{m.group(2)}"),
    # 工作年限：工作满一年 / 工作满2年
    ("experience", re.compile(r"工作\s*满\s*([一二三四五六七八九十\d]+)\s*年"),
     lambda m: f"工作满{m.group(1)}年"),
    # 原创性与发表状态：论文类通知的硬门槛
    ("original", re.compile(r"未(?:曾)?公开发表"), lambda m: "未公开发表"),
    ("original", re.compile(r"原创(?:性)?成果"), lambda m: "原创成果"),
    # 作者身份：第一作者为学生 / 第一作者为青年教师
    ("authorship", re.compile(r"第一作者\s*(?:须|应|需|必须)?\s*(?:为|是)\s*([^，。；、]{2,12})"),
     lambda m: f"第一作者为{m.group(1)}"),
    # 阶段性培养要求：已完成开题 / 已通过中期考核
    ("progress", re.compile(r"(?:已)?完成开题"), lambda m: "已完成开题"),
    ("progress", re.compile(r"通过中期(?:考核|检查)"), lambda m: "已通过中期考核"),
    # 技能要求：熟悉Python / 熟悉编程语言（限短词，避免把整句当条件）
    ("skill", re.compile(r"熟悉\s*([\u4e00-\u9fa5A-Za-z+#.]{2,12})(?=[，。；、）)]|$)"),
     lambda m: f"熟悉{m.group(1)}"),
    # 经济状况
    ("finance", re.compile(r"(?:家庭)?经济困难"), lambda m: "家庭经济困难"),
    ("finance", re.compile(r"突发(?:重大)?变故"), lambda m: "突发变故"),
    # 品行纪律
    ("conduct", re.compile(r"无(?:违纪|纪律处分|处分)(?:记录|行为)?"), lambda m: "无违纪记录"),
    ("conduct", re.compile(r"无(?:不及格|挂科|补考)(?:科目|记录|课程)?"), lambda m: "无不及格科目"),
    # 身体条件
    ("health", re.compile(r"身体健康|身心健康"), lambda m: "身体健康"),
    # 服从性（"服从安排"过于笼统，只认"服从管理/调配"这类强约束）
    ("obedience", re.compile(r"服从[^，。；、]{0,8}(?:管理|调配)"), lambda m: "服从管理"),
    # 时间投入
    ("commitment", re.compile(r"全程参与|全程参加"), lambda m: "全程参与"),
    # 师资
    ("teacher", re.compile(r"指导(?:教师|老师)"), lambda m: "指导教师"),
    # 优先项
    ("priority", re.compile(r"师范类(?:专业)?(?:学生)?优先"), lambda m: "师范类专业优先"),
    ("priority", re.compile(r"家庭经济困难(?:学生)?优先"), lambda m: "家庭经济困难优先"),
    ("priority", re.compile(r"有[^，。；、]{0,12}(?:经验|经历)者?优先"),
     lambda m: re.sub(r"者?优先$", "优先", m.group(0))),
]

# 兜底：没有任何规则命中时，判断这句话是否仍是可读的短约束
FALLBACK_MAX_LEN = 20
# 兜底片段必须含有的"约束性"词汇，否则视为叙述而非条件
FALLBACK_TERMS = (
    "成绩", "排名", "学分", "绩点", "四级", "六级", "英语", "专业", "年级",
    "团队", "组队", "人数", "党员", "在读", "全日制", "原创", "健康",
    "学有余力", "综合素质", "经验", "资格", "证书", "基础", "技能",
)
# 只有形式、没有任何可核验数值的组织方式，不构成约束
FORM_ONLY_RE = re.compile(r"^以.{0,8}形式|^以.{0,4}方式")
# 兜底片段要剥掉的主语与情态前缀
LEAD_RE = re.compile(
    r"^(?:项目负责人|参赛(?:队员|者|人员|学生)?|申请(?:人|者|学生)?|报名(?:人|者|学生)?|"
    r"申报(?:人|者)?|选手|同学|学生|各(?:位)?同学|团队负责人|负责人|本人)"
    r"\s*(?:须|应|需|必须|应当|一律)?\s*(?:为|是)?\s*"
)
TAIL_RE = re.compile(r"(?:者优先|的学生|的同学|均可|即可|均可报名|以上学生)$")


def canonicalize(clause: str, strict: bool = False) -> list[tuple[str, str]]:
    """把一条候选子句归一为若干原子约束，返回 [(kind, value)]。

    strict=True 表示该子句来自"对象/范围"这类身份章节或普通正文：
    兜底路径必须同时出现情态动词才采信，避免把"全日制本科三年级学生"
    这类身份描述误当成资格条件。
    """
    text = (clause or "").strip()
    if len(text) < 3:
        return []
    if SUBMIT_RE.search(text):  # 签署《承诺书》属于材料流程
        return []
    if re.search(r"(择优|推荐|名额|限额)", text):  # 选拔结果/名额说明，不是门槛
        return []

    # 优先/评奖类表述：只保留优先项，不当作硬门槛
    if "优先" in text:
        prio: list[tuple[str, str]] = []
        for kind, pattern, fmt in CANON_RULES:
            if kind != "priority":
                continue
            for m in pattern.finditer(text):
                value = fmt(m)
                if value and (kind, value) not in prio:
                    prio.append((kind, value))
        return prio

    hits: list[tuple[str, str]] = []
    for kind, pattern, fmt in CANON_RULES:
        for m in pattern.finditer(text):
            value = fmt(m)
            if value and (kind, value) not in hits:
                hits.append((kind, value))
    if hits:
        return hits

    # 无规则命中：仅在片段本身短且含约束性词汇时保留
    if len(text) > FALLBACK_MAX_LEN:
        return []
    if FORM_ONLY_RE.match(text) and not re.search(r"\d", text):
        return []
    if not any(t in text for t in FALLBACK_TERMS):
        return []
    if NEGATION_RE.search(text) or ADVOCATE_RE.match(text):
        return []
    if strict and not MODAL_RE.search(text):
        return []
    trimmed = TAIL_RE.sub("", LEAD_RE.sub("", text)).strip("，,。；; ")
    if 3 <= len(trimmed) <= FALLBACK_MAX_LEN and any(t in trimmed for t in FALLBACK_TERMS):
        return [("fallback", trimmed)]
    return []


def is_candidate(sentence: str) -> bool:
    """句子级预筛：排除否定式与倡议式表述，减少无效切分。"""
    s = (sentence or "").strip()
    if not s:
        return False
    if LABEL_ONLY_RE.match(s):
        return False
    if ADVOCATE_RE.match(s):
        return False
    if NEGATION_RE.search(s) and not MODAL_RE.search(s):
        return False
    return True


def label_of(sentence: str) -> str:
    """取句子的内联标签（"参赛对象""【材料要求】"），没有则返回空串。"""
    m = INLINE_LABEL_RE.match(sentence or "")
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").strip()
