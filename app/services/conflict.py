"""跨来源冲突检测（Capability: Cross-Source Conflict Detection）。

## 为什么这是一个真问题

单条通知逐条解析时，每一条都可以「完全正确」：A 学院说 11 月 20 日截止，
B 学院转发同一活动说 11 月 27 日截止——分开看都是忠实于原文的抽取结果，
只有把它们放在一起，才暴露「同一件事有两个截止日期」这个真正的风险。

单来源抽取器**结构上不可能**发现这类问题，因为它从不做跨文档比较。
学生侧的真实后果是：按早的那个日期放弃，或按晚的那个日期提交而错过。

## 判定策略（只用可解释的规则，不用向量相似度）

1. 候选配对：标题字面重合度 + 同分类 + 发布时间接近 → 判定「疑似同一事件」。
   刻意不引入 embedding：冲突检测必须能对评委逐条解释「为什么认为这两条
   是同一件事」，向量相似度说不清理由，反而降低可信度。
2. 字段比对：截止日期 / 地点 / 联系人 / 报名方式 逐一比较。
3. 分级：截止日期不一致 = blocker（直接决定能否报名）；地点/联系人 = warn；
   其余 = info。只提示不裁决，最终由人工标记哪条为准。
"""
from __future__ import annotations

import datetime as dt
import re
from itertools import combinations

from app.services.textutil import normalize_text, safe_iso_date

# 标题里高频出现、但对「是不是同一件事」没有区分度的词
_STOP = {
    "关于", "开展", "举办", "组织", "做好", "做好关于", "通知", "公告", "公示",
    "我院", "我校", "学院", "大学", "学生", "工作", "有关", "事项", "决定",
    "现将", "如下", "要求", "安排", "进行", "参加", "参与", "报名",
}

_PUNCT = re.compile(r"[\s\u3000，,。、；;：:！!？?（）()《》“”\"'【】\[\]—\-·…]+")

# 年份与学期是「年度/季度性事务」的唯一区分点，必须保留为词元。
# 早期版本把年份直接删掉了，结果「2025年秋冬季学位授予」和「2026年秋冬季学位授予」
# 相似度算到 1.0，被误判成同一件事——而这类周期性通知恰恰是校园里最常成对出现的内容。
_YEAR = re.compile(r"(20\d{2})")
# 学期必须单独抽，不能和「2024-2025学年」混在一个集合里：
# 混在一起时两条通知都会命中「2024-2025学年」，取交集非空就把
# 「第一学期 / 第二学期」的真实差异掩盖掉了。
_SEMESTER = re.compile(r"(第[一二三]学期|[秋冬春夏]季学期|[秋冬春夏]季|[上下]半年)")
_ACADEMIC_YEAR = re.compile(r"(\d{4}-\d{4}学?年)")

# 单独的日期串，用于把「报名方式」这类长句里的日期抹掉再比：
# 「…报名截止时间为2026年12月1日」与「…2026年11月30日」渠道一字不差，
# 差异全在日期上，而日期已由 deadline 冲突覆盖。
_DATE_IN_TEXT = re.compile(r"\d{4}\s*[年\-/]\s*\d{1,2}\s*[月\-/]\s*\d{1,2}\s*日?")

# 事项所处「阶段」：同一个项目在不同阶段的标题高度相似（「…项目申报」与
# 「…项目结题验收」共享 70% 以上 2-gram），但它们是先后两件事而非同一件事
# 的两种说法，放在一起比截止日期只会制造噪声。互斥判定与时间限定词同理：
# 双方都写了不同阶段才算互斥，只有一方写时仍可配对。
_PHASE = re.compile(
    r"(申报|立项|结题|结项|验收|中期检查|中期考核|开题|预答辩|答辩|公示|结果公布|"
    r"评审|招募|培训|复赛|决赛|初赛)"
)

# 「国家奖学金」与「国家励志奖学金」的 2-gram 重合度高达 0.73，
# 「优秀学生」与「优秀学生干部」也几乎是同一串字——但它们是**两个不同的奖项**，
# 各自有独立的截止日期，把它们配成一对纯属噪声。
# 这类「同词族不同实体」靠相似度永远分不开，只能把实体名本身抽出来比。
# 做法：以身份承载词结尾，向前回溯修饰语，得到「限定语 + 中心词」的整体名。
_IDENTITY_HEADS = (
    "奖学金", "助学金", "助学贷款", "大赛", "竞赛", "比赛", "计划", "项目",
    "工程", "论坛", "讲堂", "讲座", "训练营", "夏令营", "招聘", "选调",
)
_IDENTITY_RE = re.compile(
    r"([\u4e00-\u9fa5]{0,10}?(?:" + "|".join(_IDENTITY_HEADS) + r"))"
)
# 回溯到的修饰语里如果混进了这些动作/时间词，说明切到了句子骨架而非实体名
_IDENTITY_NOISE = re.compile(
    r"(关于|开展|举办|组织|做好|评选|申报|进行|转发|通知|公告|公示|"
    r"年度|学年|学期|第[一二三四]届|\d{4})"
)


def _tokens(title: str) -> set[str]:
    """把标题切成有区分度的词元：去标点、去通用词，保留 2-gram。

    中文没有空格分词，这里用「2-gram + 停用词过滤」近似：对「创新创业训练计划」
    产出 {创新, 新创, 创业, 业训, 训练, 练计, 计划}，只要两条标题共享足够多的
    2-gram，就说明它们在说同一件事。这个粗略方案的好处是完全可解释。
    """
    text = _PUNCT.sub("", normalize_text(title or ""))
    for word in _STOP:
        text = text.replace(word, "")
    if len(text) < 2:
        return set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


def _qualifiers(title: str) -> dict[str, set[str]]:
    """抽出四类限定词：公历年、学期、学年、事项阶段。

    同一句标题换个年份或学期就是完全不同的两件事（每学年的学位授予、
    每学期的助管招聘都是成对出现的周期性通知），所以这些词要在相似度之外
    单独比对，不能依赖 2-gram 重合度。
    """
    text = normalize_text(title or "")
    return {
        "year": set(_YEAR.findall(text)),
        "semester": set(_SEMESTER.findall(text)),
        "academic_year": set(_ACADEMIC_YEAR.findall(text)),
        "phase": set(_PHASE.findall(text)),
    }


def _identities(title: str) -> set[str]:
    """抽出标题里的「奖项 / 赛事 / 项目」实体名（限定语 + 中心词）。

    「国家奖学金」和「国家励志奖学金」是两个独立奖项，「校园歌手大赛」和
    「校园歌手大赛决赛」是同一赛事的不同轮次——前者必须拆开，后者可以合并。
    这里只负责抽出实体名，互斥判断交给 `_identity_conflict()`。
    """
    text = _PUNCT.sub("", normalize_text(title or ""))
    found: set[str] = set()
    for m in _IDENTITY_RE.finditer(text):
        name = m.group(1)
        # 砍掉混进来的动作/时间前缀（"关于评选2025学年国家奖学金" → "国家奖学金"）
        name = _IDENTITY_NOISE.sub("", name)
        if len(name) >= 3:
            found.add(name)
    return found


def _identity_conflict(a: str, b: str) -> bool:
    """实体名互斥：两边各自都写了实体名，且没有一个是另一个的子串。

    "国家奖学金" vs "国家励志奖学金" → 互斥（后者含前者？不含，"国家奖学金"
    不是 "国家励志奖学金" 的子串，因为中间插了"励志"）→ 正确拆开。
    "校园歌手大赛" vs "校园歌手大赛决赛" → 前者是后者的子串 → 不互斥 → 允许配对。
    只在一方写实体名时不判互斥（标题可能是"关于做好该项工作的通知"）。
    """
    ia, ib = _identities(a), _identities(b)
    if not ia or not ib:
        return False
    for x in ia:
        for y in ib:
            if x in y or y in x:
                return False
    return True


def _qualifier_conflict(a: str, b: str) -> bool:
    """两段标题的限定词是否互斥。

    逐类比对：某类双方都写了、且完全不重合，才判互斥。一方没写该类别时
    不构成互斥，否则「关于助管招聘的通知」就无法与「2026年春季助管招聘」配对。
    """
    qa, qb = _qualifiers(a), _qualifiers(b)
    for key in ("year", "semester", "academic_year", "phase"):
        va, vb = qa[key], qb[key]
        if va and vb and not (va & vb):
            return True
    return _identity_conflict(a, b)


def similarity(a: str, b: str) -> float:
    """Jaccard 相似度；任一标题过短时返回 0，避免空集合导致除零或误判。"""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    union = ta | tb
    return len(inter) / len(union)


def _contains(a: str, b: str) -> bool:
    """短标题整体出现在长标题里 —— 典型的「某学院转发：<原标题>」写法。

    这类标题的 Jaccard 会被多出来的院系前缀稀释（转发前缀越长稀释越重），
    但语义上确定是同一件事，所以单独放行，不参与相似度阈值判断。
    """
    na = _PUNCT.sub("", normalize_text(a or ""))
    nb = _PUNCT.sub("", normalize_text(b or ""))
    if len(na) < 8 or len(nb) < 8:
        return False
    short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
    return short in long


def _within_days(d1: str, d2: str, days: int) -> bool:
    a, b = safe_iso_date(d1), safe_iso_date(d2)
    if a is None or b is None:
        return True  # 日期缺失时不作为排除条件，宁可多看一对
    return abs((a - b).days) <= days


def _strip_dates(text) -> str:
    """抹掉文本里的具体日期，只留办事渠道的措辞。

    用于「报名方式」比较：两校/两院的渠道写法一字不差、只有内嵌截止日期不同时，
    不该再报一条「报名渠道不一致」——日期差异已由 deadline 冲突单独覆盖。
    """
    return _DATE_IN_TEXT.sub("", str(text or "")).strip()


def _flat(value) -> str:
    """把字段值压成可比较、可展示的字符串。

    联系方式在分析结果里是数组（一个通知常留两个号码），直接 str() 会
    得到 "['027-87850001']" 这种带方括号和引号的展示，学生一眼看不懂。
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "、".join(str(x).strip() for x in value if str(x).strip())
    return str(value).strip()


def _cmp(field: str, va, vb, severity: str, reason: str) -> dict | None:
    """比较单个字段；值相同或一方为空时不算冲突（空不是错，是没抽到）。"""
    sa = _flat(va)
    sb = _flat(vb)
    if not sa or not sb or sa == sb:
        return None
    return {
        "field": field,
        "value_a": sa[:200],
        "value_b": sb[:200],
        "severity": severity,
        "reason": reason,
    }


FIELD_LABELS = {
    "deadline": "报名截止日期",
    "location": "办理地点",
    "contact": "联系方式",
    "contact.phone": "联系电话",
    "contact.email": "联系邮箱",
    "apply_method": "报名方式",
    "grade_requirement": "年级要求",
    "major_requirement": "专业要求",
}

SEVERITY_LABELS = {
    "blocker": "高",
    "warn": "中",
    "info": "低",
}


def compare_pair(na: dict, nb: dict) -> list[dict]:
    """比对两条已解析通知，返回冲突列表（可能为空）。

    入参是 {id, title, category, publish_time, analysis} 形态的字典，
    由路由层用 notice_to_dict 组装，保证与前端看到的是同一份数据。
    """
    aa = na.get("analysis") or {}
    ab = nb.get("analysis") or {}

    # 新闻资讯没有办理事项，即使标题像也不参与冲突比对
    if not aa.get("actionable", True) or not ab.get("actionable", True):
        return []

    out: list[dict] = []

    # 截止日期：最严重的一类冲突，直接决定「还来不来得及」
    item = _cmp("deadline", aa.get("deadline"), ab.get("deadline"), "blocker",
                "同一事项出现两个截止日期，按较早的会误判已过期，按较晚的会错过材料提交")
    if item:
        out.append(item)

    # 地点 / 联系方式：影响「去哪办、找谁问」
    item = _cmp("location", aa.get("location"), ab.get("location"), "warn",
                "办理地点不一致，学生可能跑错校区或楼栋")
    if item:
        out.append(item)

    ca, cb = aa.get("contact") or {}, ab.get("contact") or {}
    for key, label in (("phone", "联系人电话"), ("email", "联系邮箱")):
        item = _cmp(f"contact.{key}", ca.get(key), cb.get(key), "warn",
                    f"{label}不一致，咨询可能联系到不相关的人")
        if item:
            out.append(item)

    # 报名方式：影响「怎么报」
    # 先抹掉句子里的日期再比——两句话只是内嵌的截止日不同，那不是渠道差异，
    # 日期本身已由上面的 deadline 冲突覆盖，重复报一遍只会稀释严重度。
    item = _cmp("apply_method", _strip_dates(aa.get("apply_method")),
                _strip_dates(ab.get("apply_method")), "info",
                "报名渠道描述不同，需确认是否存在两个并行入口")
    if item:
        out.append(item)

    # 资格条件：影响「我能不能报」
    for field in ("grade_requirement", "major_requirement"):
        la = [x for x in (aa.get(field) or []) if x]
        lb = [x for x in (ab.get(field) or []) if x]
        if la and lb and set(la) != set(lb):
            out.append({
                "field": field,
                "value_a": "、".join(la)[:200],
                "value_b": "、".join(lb)[:200],
                "severity": "warn",
                "reason": f"{FIELD_LABELS[field]}不同，可能只有部分学院的学生符合条件",
            })

    return out


def detect(notices: list[dict], threshold: float = 0.45, window_days: int = 60) -> list[dict]:
    """全量检测：返回待入库的冲突记录列表。

    复杂度 O(n²)，对当前量级（数百到数千条）完全够用；n 上万时再考虑
    先按分类和发布时间分桶，把两两比较限制在同一桶内。
    """
    pairs: list[dict] = []
    usable = [n for n in notices if (n.get("analysis") or {}).get("actionable", True)]

    for na, nb in combinations(usable, 2):
        if na.get("category") != nb.get("category"):
            continue
        if not _within_days(na.get("publish_time") or "", nb.get("publish_time") or "", window_days):
            continue

        score = similarity(na.get("title") or "", nb.get("title") or "")
        # 转发场景：短标题被长标题完整包含，即使相似度被前缀稀释也算同一件事
        if score < threshold and not _contains(na.get("title") or "", nb.get("title") or ""):
            continue
        # 标题骨架相同但年份/学期/事项阶段不同 → 是同一件事在不同周期或不同
        # 阶段的独立通知，不是冲突
        if _qualifier_conflict(na.get("title") or "", nb.get("title") or ""):
            continue

        for conflict in compare_pair(na, nb):
            record = dict(conflict)
            record.update({
                "notice_a_id": min(na["id"], nb["id"]),
                "notice_b_id": max(na["id"], nb["id"]),
                "similarity": round(score, 3),
                "title_a": na.get("title", ""),
                "title_b": nb.get("title", ""),
            })
            pairs.append(record)

    # 去重：(a,b,field) 唯一；同一对通知的多个字段冲突各自成条
    seen: set[tuple] = set()
    unique: list[dict] = []
    for r in pairs:
        key = (r["notice_a_id"], r["notice_b_id"], r["field"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)

    unique.sort(key=lambda r: ({"blocker": 0, "warn": 1, "info": 2}[r["severity"]], -r["similarity"]))
    return unique


def summarize(records: list[dict]) -> dict:
    """给看板用的汇总：按严重程度与字段统计。"""
    by_severity = {"blocker": 0, "warn": 0, "info": 0}
    by_field: dict[str, int] = {}
    for r in records:
        by_severity[r.get("severity", "info")] = by_severity.get(r.get("severity", "info"), 0) + 1
        f = r.get("field", "")
        by_field[f] = by_field.get(f, 0) + 1
    return {
        "total": len(records),
        "by_severity": by_severity,
        "by_field": by_field,
        "unresolved": sum(1 for r in records if not r.get("resolved")),
    }


def today_iso() -> str:
    return dt.date.today().isoformat()
