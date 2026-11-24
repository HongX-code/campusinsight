"""结构化字段契约：把设计文档 8.2 规定的「约束类 / 时间类 / 联络类」字段补齐。

设计文档里 AI 需要输出十四个字段，早期版本只落地了八个，专业限制与年级限制
混在 `target` 的自由文本里，导致匹配引擎只能靠字符串包含做模糊猜测。本模块把
这两类硬约束抽成可判定的枚举，另外补齐活动时间、报名起止、地点与联系方式。

与 extractor 的分工：
    extractor 负责「原文 -> 字段」的主链路（截止、对象、条件、材料、方式）；
    fields    负责「同一段原文 -> 结构化约束」，并把已识别的日期复用进来，
              避免对全文做第二次日期扫描而产生口径不一致。

三条硬约束，和项目整体的可信 AI 设计保持一致：
    1. 只从原文捕获，不做常识补全；没写就是空；
    2. 每个非空字段都能给出原文片段（evidence）；
    3. 归一化只在「同一事实的写法差异」范围内进行，不推断原文未声明的限制。
"""
from __future__ import annotations

import re

# 设计文档 8.2 里「约束类 / 时间类 / 联络类」字段的统一契约。
# 这些是字面事实，由本模块确定性抽取，不交给生成式模型（见 analyzer 的说明）。
FIELD_KEYS: tuple[str, ...] = (
    "grade_requirement", "major_requirement", "registration_start",
    "event_time", "location", "contact",
)


def empty_fields() -> dict:
    return {
        "grade_requirement": [],
        "major_requirement": [],
        "registration_start": None,
        "event_time": None,
        "location": "",
        "contact": {},
    }


# ---------------------------------------------------------------- 年级 / 学历约束
# 顺序敏感：先长词后短词，避免「博士研究生」被「研究生」吃掉。
GRADE_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("博士研究生", ("博士研究生", "博士生", "博士")),
    ("硕士研究生", ("硕士研究生", "硕士生", "硕士")),
    ("研究生", ("研究生",)),
    ("本科生", ("本科生", "本科")),
    ("专科生", ("专科生", "专科", "高职")),
    ("毕业生", ("毕业生", "应届生", "应届毕业生")),
    ("教师", ("教职工", "专任教师")),
    ("大一", ("大一", "本科一年级")),
    ("大二", ("大二", "本科二年级")),
    ("大三", ("大三", "本科三年级")),
    ("大四", ("大四", "本科四年级")),
    ("研一", ("研一", "硕士一年级")),
    ("研二", ("研二", "硕士二年级")),
    ("研三", ("研三", "硕士三年级")),
)
# 出现这些词说明整句在讲「不限」，此时不应产出限制
NO_LIMIT = ("不限", "均可", "不受限制", "不设", "无专业要求", "任何专业", "所有专业",
            "无专业", "无年级与专业", "不设专业限制", "无专业限制", "专业不限", "不限专业")
# 约束语境：只有这些标签附近的身份词才算「限制」，否则可能只是背景描述
CONSTRAINT_CUE = re.compile(
    r"(?:面向|限|仅限|只面向|适用于|对象|范围|要求|须为|应为|须是|具有|欢迎|招收|招募)"
    r"\s*[:：]?\s*([^。；;\n]{2,70})"
)
COHORT_RE = re.compile(r"(?:20\d{2}|[一二三四])\s*级")

# ---------------------------------------------------------------- 专业 / 学院约束
MAJOR_CAPTURE = re.compile(
    r"(?:面向|限|仅限|招收|欢迎|适用于|对象|专业要求|参赛范围)\s*[:：]?\s*"
    r"([^。；;\n]{0,48}?(?:专业|学院|学科|门类)[^。；;\n]{0,24})"
)
# 不收「系」后缀：新闻稿里的「知识体系」「学科体系」会被误当成专业单元
MAJOR_UNIT = re.compile(r"[\u4e00-\u9fa5A-Za-z0-9]{2,12}(?:专业|学院|学科)")
# 专业清单只允许出现在「约束语境」里。真实站点的新闻稿常写
# 「交通运输、港口物流等领域的学科」「扎根中国本土、聚焦学科」，
# 全文扫描会把这类叙述当成专业限制。
SCOPE_CUE = re.compile(r"(?:对象|面向|仅限|招收|招聘|参赛|报考|报名条件|资格|专业|学院|学科|限)")
NARRATIVE = ("领域", "建设", "发展", "体系", "系统", "知识", "介绍", "情况", "方面",
             "优势", "聚焦", "围绕", "推动", "开展", "涉及", "提升", "转型")
# 两种常见的「专业清单」写法：显式枚举 + 并列词收尾
MAJOR_INCLUDE = re.compile(r"专业(?:包括|为|限于|限定|范围)\s*[:：]?\s*([^。；;\n]{2,60})")
# 只认顿号/半角逗号分隔：真实通知写「计算机、电子信息等相关专业」；
# 「全国软件和信息技术专业」「无年级与专业限制」用「和/与/，」，不该进这个模式。
MAJOR_LIST_TAIL = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9]{2,8}(?:\s*[、,]\s*[\u4e00-\u9fa5A-Za-z0-9]{2,8}){2,7})"
    r"\s*等?(?:相关|相近)?专业"
)
# 并列专名里出现学历词/叙述词，说明这段是「学位授权学科」「招生专业目录」而非限制
LIST_STOP = ("博士", "硕士", "研究生", "学位", "授权", "招生", "考试", "报考点", "科目",
             "身高", "接收", "地址", "调档", "领域", "一级", "参赛", "教职工", "比例")
MAJOR_STOP = ("各专业", "相关专业", "专业不限", "不限专业", "专业方向", "本专业",
              "学科门类", "专业名称", "所学专业", "跨专业", "专业背景")
MAJOR_SPLIT = re.compile(r"[,，、/及和或与\s]+|等(?:相关|相近)?")
# 并列专名里出现的动词/泛词，出现即整段不采信，避免「每人限报一队」被当成专业
MAJOR_VERB = ("限报", "组队", "鼓励", "每人", "面向", "欢迎", "参加", "报名", "提交",
              "全日制", "在校", "全体", "相关", "不限", "不得", "须为", "均可")

# ---------------------------------------------------------------- 时间 / 地点 / 联络
ACTION_CUES = ("举行", "举办", "召开", "开展", "开赛", "开讲", "活动时间", "讲座时间",
               "报告时间", "比赛时间", "会议时间", "答辩时间", "考试时间")
REGISTER_CUES = ("报名", "报考", "申报", "申请", "征集", "投稿", "提交", "报送", "填报")
RANGE_SPLIT = re.compile(r"(?:\s*至\s*|\s*到\s*|\s*[—～~\-]\s*)")

LOCATION_LABEL = re.compile(
    r"(?:报名|活动|会议|讲座|报告|比赛|考试|答辩|现场|办公|提交|报送|材料接收)?\s*"
    r"(?:地点|地址|场所|会场)\s*[:：]\s*([^，,。；;\n]{2,40})"
)
# 公众号推文常见"地点在南湖校区…"的无冒号写法，单列一条避免与其他"在"字句混淆
LOCATION_NOCOLON = re.compile(
    r"(?:报名|活动|会议|讲座|报告|比赛|考试|答辩|现场)?\s*"
    r"(?:地点|地址|场所|会场)\s*在\s*([^，,。；;\n]{2,40})"
)
LOCATION_VENUE = re.compile(r"在\s*([^，,。；;\n]{2,30}?)\s*(?:举行|举办|召开|开展|进行)")
LOCATION_STOP = ("网上", "线上", "系统", "平台", "邮箱", "网站", "另行通知", "待定", "详见")
PHONE_RE = re.compile(r"(?<!\d)(?:0\d{2,3}[- ]?)?\d{7,8}(?!\d)|(?<!\d)1[3-9]\d{9}(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PERSON_RE = re.compile(r"(?:联系人|咨询人|联系老师)\s*[:：]?\s*([\u4e00-\u9fa5]{2,4})")


def _unique(seq: list[str]) -> list[str]:
    out: list[str] = []
    for x in seq:
        x = (x or "").strip(" \t：:，,。;；")
        if x and x not in out:
            out.append(x)
    return out


def _constraint_text(text: str, target: list[str], conditions: list[str]) -> str:
    """拼出「约束语境」：显式标签后的片段 + 资格条件 + 原文中的限制行。

    刻意不使用 `target`：那是归一化后的结果，会把泛指「研究生」折叠成
    「硕士研究生」。硬条件一旦继承这个值，博士生就会被误判为不符合，
    属于对原文未声明限制的推断，违反字段契约。
    """
    parts = list(conditions or [])
    parts += [m.group(0) for m in CONSTRAINT_CUE.finditer(text)]
    parts += [ln for ln in text.split("\n")
              if re.search(r"(?:对象|面向|范围|仅限|年级|学历)", ln)]
    return " ".join(parts)


def extract_grade(ctx: str) -> list[str]:
    """年级 / 学历限制，归一化为可判定的身份词。"""
    if any(k in ctx for k in NO_LIMIT):
        # 「专业不限」不影响年级限制，所以只拦截身份层面的不限表述
        if any(k in ctx for k in ("年级不限", "学历不限", "身份不限", "均可报名", "均可参加")):
            return []
    found: list[str] = []
    for canon, keys in GRADE_TOKENS:
        if any(k in ctx for k in keys):
            # 「博士研究生」「硕士研究生」已表达层次时，不再单列泛化的「研究生」
            if canon == "研究生" and any(g in found for g in ("博士研究生", "硕士研究生")):
                continue
            found.append(canon)
    # 具体年级出现时，泛化的「本科生」仍有价值（它声明学历层次），保留即可
    cohort = COHORT_RE.search(ctx)
    if cohort:
        found.insert(0, re.sub(r"\s+", "", cohort.group(0)))
    return _unique(found)[:6]


def _valid_major_unit(seg: str) -> bool:
    """只采信带明确后缀的专业/学院单元，且排除「跨学院」「各专业」这类泛称。"""
    if not re.search(r"(?:专业|学院|学科|系)$", seg):
        return False
    if len(seg) < 3 or len(seg) > 8:
        return False
    # 「要求到学院办理」里的「到学院」是动作，不是学院限制
    if re.match(r"^[到至去在由把被要求]", seg):
        return False
    return not any(k in seg for k in MAJOR_STOP + MAJOR_VERB + LIST_STOP
                   + ("跨", "各", "本", "所", "的", "与", "及", "不", "低", "于", "比", "例"))


def scoped_text(text: str) -> str:
    """约束语境切片：保留含限制线索的行及其紧邻下一行，供专业字段检索。"""
    lines = text.split("\n")
    keep: set[int] = set()
    for i, ln in enumerate(lines):
        if SCOPE_CUE.search(ln):
            keep.add(i)
            if i + 1 < len(lines):
                keep.add(i + 1)
    return "\n".join(lines[i] for i in sorted(keep))


def extract_major(text: str, ctx: str) -> list[str]:
    """专业 / 学院限制。只在约束语境 + 明确后缀里找，避免把正文叙述当限制。

    两类可采信形态：
      ① 带后缀单元：「信息管理专业」「计算机学院」；
      ② 并列专名 + 收尾：「信息管理、计算机等相关专业」中的「信息管理」「计算机」。
    其余一律不采信——宁可少给一个字段，也不给一个原文没写的专业限制。
    """
    # 只在约束语境行（及其紧邻下一行）里找专业限制，新闻稿的叙述性并列词被挡在外面
    scoped = scoped_text(text)

    candidates: list[str] = [m.group(1) for m in MAJOR_CAPTURE.finditer(scoped)]
    candidates += [m.group(0) for m in MAJOR_UNIT.finditer(ctx)]

    out: list[str] = []
    # 专业清单：「专业包括计算机、电子信息…」/「计算机、电子信息等相关专业」
    enum_hits = [m for m in MAJOR_INCLUDE.finditer(scoped)] + [m for m in MAJOR_LIST_TAIL.finditer(scoped)]
    for m in enum_hits:
        # 捕获段常带「专业包括…」前缀与「等相关专业」后缀，先削干净再切词；
        # 否则「相关」会命中停用词，把整段合法清单误杀。
        lst = m.group(1).strip()
        lst = re.sub(r"^专业(?:包括|为|限于|限定|范围)?\s*[:：]?", "", lst)
        lst = re.sub(r"等?(?:相关|相近)?(?:专业|学科|方向)?$", "", lst).strip()
        if any(k in lst for k in NO_LIMIT + MAJOR_VERB + NARRATIVE + LIST_STOP):
            continue
        for seg in re.split(r"[,，、/及和等\s]+", lst):
            seg = re.sub(r"^(?:我校|该校|大学|学院)+", "", seg.strip())
            if (2 <= len(seg) <= 8 and re.fullmatch(r"[\u4e00-\u9fa5A-Za-z0-9]+", seg)
                    and not any(k in seg for k in MAJOR_STOP + MAJOR_VERB + LIST_STOP)):
                out.append(seg)

    for cand in candidates:
        if any(k in cand for k in NO_LIMIT + MAJOR_VERB):
            continue
        for m in MAJOR_UNIT.finditer(cand):
            if _valid_major_unit(m.group(0)):
                out.append(m.group(0))
    # 去掉被更长专名包含的短词（「师范类」⊂「师范类专业」），避免同一事实重复计两次
    uniq = _unique(out)
    return [s for s in uniq if not any(t != s and s in t for t in uniq)][:6]


def _line_of(text: str, pos: int) -> str:
    lo = text.rfind("\n", 0, pos) + 1
    hi = text.find("\n", pos)
    return text[lo: hi if hi != -1 else len(text)]


def extract_times(text: str, dates: list[dict], deadline: str | None) -> tuple[str | None, str | None, dict]:
    """返回 (报名开始时间, 活动时间, 证据)。

    报名开始只认「报名时间 X 至 Y」这类显式区间的左端；
    活动时间只认带举行/举办/讲座等线索的日期，且不与截止日重复。
    """
    ev: dict = {}
    reg_start: str | None = None
    event: str | None = None

    for m in re.finditer(r"(?:报名|报考|申报|申请|征集|投稿)(?:开始)?时间\s*[:：]?\s*([^\n]{4,60})", text):
        seg = m.group(1)
        if not any(c in seg for c in REGISTER_CUES + ("至", "到", "—", "～", "~", "-")):
            continue
        parts = RANGE_SPLIT.split(seg, maxsplit=1)
        if len(parts) < 2:
            continue
        def _in_seg(d: dict) -> bool:
            day = d["date"]
            forms = (f"{day.year}年{day.month}月{day.day}日", f"{day.month}月{day.day}日",
                     f"{day.month:02d}月{day.day:02d}日", day.isoformat(),
                     f"{day.year}-{day.month}-{day.day}")
            return any(f in seg for f in forms)

        hit = next((d for d in dates if _in_seg(d)), None)
        if hit is None:
            # 日期可能写成「2026年11月3日」而候选里已有对应对象，按位置就近取
            hit = next((d for d in dates if m.start() <= d["start"] <= m.end() + 60), None)
        if hit and (not deadline or hit["date"].isoformat() != deadline):
            reg_start = hit["date"].isoformat()
            ev["registration_start"] = seg.strip()[:80]
            break

    for item in dates:
        iso = item["date"].isoformat()
        if deadline and iso == deadline:
            continue
        line = _line_of(text, item["start"])
        if any(k in line for k in ACTION_CUES):
            event = iso
            ev["event_time"] = line.strip()[:80]
            break
    return reg_start, event, ev


def extract_location(text: str) -> tuple[str, str]:
    for pattern in (LOCATION_LABEL, LOCATION_NOCOLON, LOCATION_VENUE):
        for m in pattern.finditer(text):
            value = m.group(1).strip(" \t：:，,。;；")
            if not value or len(value) > 40 or any(k in value for k in LOCATION_STOP):
                continue
            if not re.search(r"[\u4e00-\u9fa5]", value):
                continue
            return value, m.group(0).strip()[:80]
    return "", ""


def extract_contact(text: str) -> tuple[dict, str]:
    person = ""
    m = PERSON_RE.search(text)
    if m:
        person = m.group(1)
    phones = _unique(PHONE_RE.findall(text))[:3]
    emails = _unique(EMAIL_RE.findall(text))[:3]
    # 过滤明显是日期或编号的 7-8 位数字（如 20261120、学号片段）
    phones = [p for p in phones if not re.fullmatch(r"20\d{6}", p)]
    if not (person or phones or emails):
        return {}, ""
    # 证据必须是原文中的连续一行，不能是拼装的描述串
    ev = next((ln.strip()[:100] for ln in text.split("\n")
               if (person and "联系人" in ln)
               or (phones and any(p in ln for p in phones))
               or (emails and any(e in ln for e in emails))), "")
    return {"person": person, "phone": phones, "email": emails}, ev


def extract_structured(text: str, dates: list[dict], target: list[str],
                       conditions: list[str], deadline: str | None) -> tuple[dict, dict]:
    """结构化字段统一入口，返回 (字段字典, 证据字典)。"""
    ctx = _constraint_text(text, target, conditions)
    grade = extract_grade(ctx)
    major = extract_major(text, ctx)
    reg_start, event, time_ev = extract_times(text, dates, deadline)
    location, loc_ev = extract_location(text)
    contact, contact_ev = extract_contact(text)

    fields = {
        "grade_requirement": grade,
        "major_requirement": major,
        "registration_start": reg_start,
        "event_time": event,
        "location": location,
        "contact": contact,
    }
    evidence: dict[str, str] = {}
    # 证据必须是原文中的连续片段，不能是拼接出来的归一化结果，
    # 否则 analyzer 的 grounding 校验会把合法字段误判成模型编造。
    if grade:
        grade_ev = next((ln.strip()[:80] for ln in text.split("\n")
                         if any(k in ln for _, keys in GRADE_TOKENS for k in keys)), "")
        if grade_ev:
            evidence["grade_requirement"] = grade_ev
    if major:
        scoped = scoped_text(text)
        major_ev = next((m.group(0).strip()[:80] for m in MAJOR_INCLUDE.finditer(scoped)), "")
        if not major_ev:
            major_ev = next((m.group(0).strip()[:80] for m in MAJOR_LIST_TAIL.finditer(scoped)), "")
        if not major_ev:
            major_ev = next((m.group(0).strip()[:80] for m in MAJOR_CAPTURE.finditer(scoped)), "")
        if not major_ev:
            major_ev = next((m.group(0) for m in MAJOR_UNIT.finditer(ctx)), "")
        if major_ev:
            evidence["major_requirement"] = major_ev
    evidence.update(time_ev)
    if location:
        evidence["location"] = loc_ev
    if contact:
        evidence["contact"] = contact_ev
    return fields, evidence
