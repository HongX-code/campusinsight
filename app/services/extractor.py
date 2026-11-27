"""结构化信息抽取：报名截止、适用对象、资格条件、报名材料、报名方式。

设计原则：
1. 所有抽取结果都附带原文片段（evidence），做到可追溯；
2. 日期必须通过上下文线索打分，避免把发布时间/活动时间误判为报名截止；
3. 适用对象归一化为最小可判定身份词，便于后续匹配逻辑稳定判断；
4. 报名材料按"条目"而非"关键词"抽取，并剥除数量词与括号注释；
5. 规则引擎既作为离线兜底，也作为 LLM 输出的校验基线。
"""
from __future__ import annotations

import datetime as dt
import re

from app.services import conditions as cond_mod
from app.services import fields as fields_mod, reltime
from app.services.textutil import blocks, find_dates, infer_year, sentences, snippet

# ---------- 日期上下文线索 ----------
DEADLINE_POS: list[tuple[str, float]] = [
    ("报名截止", 6), ("截止", 4.5), ("截至", 4.5), ("申请截止", 5), ("前提交", 4),
    ("前完成", 3), ("前将", 3.5), ("前发送", 3.5), ("前上传", 3.5), ("报名时间", 3),
    ("报名", 2), ("申报", 2), ("报送", 3), ("之内", 2), ("前", 1.2), ("申请", 1),
]
RANGE_END: list[tuple[str, float]] = [("至", 5), ("到", 4), ("—", 4), ("～", 5), ("~", 5)]
# 范围结束符只在「办理类时间范围」里才是截止线索。真实站点上大量出现
# 「公示期为X至Y」「新闻网讯 10月30日至11月2日」「活动期间X至Y」，
# 这些范围末端不是报名截止；原先无条件 +5 分会把它们硬推成 deadline。
RANGE_ACTION = (
    "报名", "报考", "申报", "申请", "提交", "报送", "征集", "征稿", "投稿",
    "填报", "递交", "预约", "受理", "投票", "补报", "评选", "推荐", "选拔", "参赛",
)
RANGE_BLOCK = (
    "公示期", "公示时间", "公示期内", "活动期间", "举办期间", "会期", "展期",
    "赛期", "有效期", "新闻网讯", "本网讯", "举行时间", "举办时间", "会议时间",
    "活动时间", "服务时间", "开放时间", "考试时间", "授课时间", "升级时间",
)
# 截止日期的语义类型：同一天可以是报名截止，也可以是异议截止，展示时不能混称
DEADLINE_KIND_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("异议截止", ("公示期", "公示时间", "异议", "反映情况")),
    ("报名截止", ("报名", "报考", "网上报名")),
    ("征集截止", ("征集", "征稿", "投稿")),
    ("申报截止", ("申报", "推荐", "评选", "选拔")),
    ("报送截止", ("报送", "上报")),
    ("提交截止", ("提交", "递交", "上传", "填报", "投递", "发送")),
)
DEADLINE_NEG: list[tuple[str, float]] = [
    ("发布时间", -8), ("印发", -5), ("发布日期", -8), ("发布于", -5), ("公布", -2),
    ("举行", -5), ("举办", -5), ("开幕", -5), ("开课", -4), ("讲座", -2.5),
    ("报告会", -2.5), ("答辩", -2), ("考试", -3), ("评审", -2), ("公示", -1),
    ("成立于", -6), ("建校", -6),
]
START_CUES: list[tuple[str, float]] = [
    ("举行", 5), ("举办", 5), ("开幕", 4), ("开始", 4), ("活动时间", 5),
    ("讲座时间", 6), ("报告时间", 6), ("时间", 1.5), ("于", 0.5),
]

# ---------- 适用对象 ----------
TARGET_PATTERNS = [
    re.compile(r"面向\s*([^。；\n]{2,50})"),
    re.compile(r"(?:参赛|申请|报名|招募|评选|资助|招聘|选拔|参会|参加)对象\s*[:：]?\s*([^。\n]{2,70})"),
    re.compile(r"(?:参会|参加|适用|参与)(?:对象|人员|范围|要求)\s*[:：]?\s*([^。\n]{2,70})"),
    re.compile(r"【(?:报名对象|参赛对象|活动对象)】\s*([^。\n]{2,70})"),
]

# 归一化身份词：顺序敏感（先博士/硕士，再本科，最后泛称）
TARGET_CANON: list[tuple[str, tuple[str, ...]]] = [
    ("博士研究生", ("博士生", "博士研究生", "博士")),
    ("硕士研究生", ("硕士生", "硕士研究生", "硕士", "研究生")),
    ("本科生", ("本科生", "本科")),
    ("专科生", ("专科生", "专科", "高职")),
    ("毕业生", ("毕业生", "应届生")),
    ("教师", ("教师", "教职工")),
    ("学生", ("在校生", "全体学生", "全校学生", "全体同学", "在校学生", "各位同学")),
]
# 这些词出现说明该片段是"要求"而不是"身份范围"
TARGET_REJECT = ("不限", "不设", "形式", "组队", "须", "限报", "请", "如下", "提交",
                 "材料", "名额", "择优", "每人", "个人赛", "要求", "基础", "坚持", "均可参加")

# 「研究生」单独出现时不能一律归成硕士研究生：
# 实测《关于我校2026年部分博士研究生（专项计划）补充报名的通知》正文里
# 只有「研究生院」「研究生专项计划」「研究生教育」这类机构名与项目名，
# 不含任何身份词，却被 TARGET_CANON 的「研究生」兜成「硕士研究生」——
# 于是这条博士专项计划通知被推给了硕士生，正是最该避免的误推。
# 办法：先剥掉这些复合词，剩下的「研究生」才算身份。
_RESEARCHER_COMPOUND = re.compile(
    r"研究生(?:院|工作部|院学位办公室|培养办公室|教育|招生|培养|管理|专项计划|项目|"
    r"创新|学术|学位|毕业|就业|国家奖学金|学业奖学金)"
)


def _score_context(ctx: str, pairs: list[tuple[str, float]]) -> float:
    return sum(w for kw, w in pairs if kw in ctx)


# ---------------- 报名截止 ----------------
def _line_span(text: str, start: int, end: int, left_max: int = 32, right_max: int = 24) -> tuple[int, int]:
    """取日期所在行的上下文范围。

    高校通知普遍写成"投稿截止：2026年12月20日\n录用通知：2027年1月25日"，
    若按字符数向后开窗，上一行的"截止"会渗到下一行，把"录用通知"判成截止日期。
    """
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    # 日期独占一行时（"五、截止时间\n2026年11月14日"），线索在上一行的标签里，
    # 此时上溯一行；若本行已有标签（"录用通知：2027年1月25日"）则不上溯，
    # 否则会把上一行的"投稿截止"错认成本行日期。
    if not text[line_start:start].strip() and line_start > 0:
        prev_start = text.rfind("\n", 0, line_start - 1) + 1
        prev_line = text[prev_start: line_start].strip()
        if prev_line and len(prev_line) <= 24:
            line_start = prev_start
    return max(line_start, start - left_max), min(line_end, end + right_max)


def deadline_kind(evidence: str) -> str:
    """按证据片段给截止日期标语义类型，避免把公示期结束日含糊叫成报名截止。"""
    for label, keys in DEADLINE_KIND_RULES:
        if any(k in evidence for k in keys):
            return label
    return "办理截止"


def _pick_deadline(dates: list[dict], text: str) -> dict | None:
    if not dates:
        return None
    scored = []
    for item in dates:
        lo, hi = _line_span(text, item["start"], item["end"])
        left = text[lo: item["start"]]
        around = text[lo: hi]
        score = _score_context(around, DEADLINE_POS)

        # 「X至Y」末端加分必须同时满足：上下文有办理动词，且不是公示/活动/报道类范围。
        # 标签常独占一行（「二、报名时间\n2026年11月3日至11月20日」），
        # 因此语境要往前多取一行，否则会把范围起始日误当成截止日。
        range_score = _score_context(left[-6:], RANGE_END)
        if range_score > 0:
            context = text[max(0, lo - 40): hi]
            if not any(k in context for k in RANGE_ACTION) or any(k in context for k in RANGE_BLOCK):
                range_score = 0
        score += range_score

        # 负向线索（举办/讲座/发布/考试…）只在「同一小句」内生效：跨句的活动描述
        # 不应误伤另一条独立的办理截止。例如「举办 X 至 Y 活动。请于 Z 前报送方案」
        # 中 Z 才是真实截止，不能因为同一段落里出现了「举办」就被判成非截止。
        # 正向下文（截止/报名/前…）仍用较宽的 around，以兼容「上一行标签 + 本行日期」。
        clause_start = max(
            (text.rfind(sep, 0, item["start"]) for sep in ("。", "；", "\n", "：")),
            default=-1,
        ) + 1
        neg_ctx = text[clause_start: item["start"]] + text[item["start"]: item["end"] + 10]
        score += _score_context(neg_ctx, DEADLINE_NEG)
        if not item["has_year"]:
            score -= 0.5
        scored.append((score, item["date"], item))

    scored.sort(key=lambda x: (x[0], x[1].toordinal()), reverse=True)
    best_score, _, best = scored[0]
    if best_score <= 0:
        return None
    evidence = snippet(text, best["start"], best["end"], pad=24, limit=120)
    return {
        "value": best["date"].isoformat(),
        "evidence": evidence,
        "kind": deadline_kind(evidence),
        "score": round(best_score, 2),
    }


def _pick_start(dates: list[dict], text: str, deadline: str | None) -> str | None:
    for item in dates:
        if deadline and item["date"].isoformat() == deadline:
            continue
        around = text[max(0, item["start"] - 24): min(len(text), item["end"] + 12)]
        if _score_context(around, START_CUES) >= 5 and _score_context(around, DEADLINE_NEG) > -3:
            return item["date"].isoformat()
    return None


# ---------------- 适用对象 ----------------
# 泛称在出现具体学历层次时属于冗余，应剔除
GENERIC_TARGET = "学生"
SPECIFIC_TARGETS = ("本科生", "硕士研究生", "博士研究生", "专科生")


def _canon_targets(fragment: str) -> list[str]:
    frag = fragment
    # 先把带"研究生"后缀的具体层次折叠，避免"博士研究生"同时命中"研究生"
    frag = frag.replace("博士研究生", "博士").replace("硕士研究生", "硕士")
    # 再剥掉「研究生院」「研究生专项计划」这类机构名/项目名，
    # 否则裸「研究生」会把它们兜成硕士研究生（详见 _RESEARCHER_COMPOUND 注释）
    frag = _RESEARCHER_COMPOUND.sub("", frag)
    # "指导教师"是岗位要求，不代表受众含教师
    frag_for_teacher = frag.replace("指导教师", "").replace("指导老师", "")
    out: list[str] = []
    if "师生" in frag:
        out.append("师生")
    for canon, keys in TARGET_CANON:
        src = frag_for_teacher if canon == "教师" else frag
        if any(k in src for k in keys) and canon not in out:
            out.append(canon)
    # 已有具体层次时不再单列"学生/毕业生"这类泛称
    if any(t in out for t in SPECIFIC_TARGETS):
        out = [t for t in out if t not in (GENERIC_TARGET, "毕业生")]
    return out


def _extract_target(text: str, title: str = "") -> tuple[list[str], str]:
    """优先从"XX对象"这类显式片段取身份词，取不到再退回正文开头。

    标题也参与判定：校园通知的标题经常是唯一写明受众的地方
    （「关于…博士研究生（专项计划）补充报名的通知」），正文通篇只提机构名。
    标题的权重高于正文兜底——它经过编辑提炼，比正文前 400 字的偶然命中更可信。
    """
    for pat in TARGET_PATTERNS:
        for m in pat.finditer(text):
            raw = m.group(1).strip(" ：:，,。")
            # 片段后半常接"要求具备XX"，属于条件而非受众范围，截断后再判定
            raw = re.split(r"[，,；;]?(?:要求|须|需|应当|并)", raw)[0].strip(" ：:，,。")
            if any(bad in raw for bad in TARGET_REJECT):
                continue
            canon = _canon_targets(raw)
            if canon:
                return canon, snippet(text, m.start(), m.end(), pad=4, limit=100)

    # 标题优先：标题里明确的学历层次就是这份通知的受众
    title_canon = _canon_targets(title or "")
    if title_canon:
        return title_canon, (title or "")[:100]

    # 兜底：正文前 400 字内出现的身份词
    head = text[:400]
    canon = _canon_targets(head)
    if canon:
        anchors = ("本科", "研究生", "硕士", "博士", "教师", "学生")
        first = min((head.find(k) for k in anchors if k in head), default=0)
        return canon, head[max(0, first - 10): first + 40].replace("\n", " ")

    return _canon_targets(text)[:3], ""


# ---------------- 资格条件 ----------------
COND_HINT = ("须", "应", "需", "要求", "条件", "资格", "不得", "优先", "限", "须为", "具备", "符合")
COND_TERMS = (
    "成绩", "排名", "挂科", "学分", "绩点", "GPA", "四级", "六级", "CET", "英语",
    "专业", "年级", "团队", "组队", "人数", "限报", "优先", "党员", "家庭经济困难",
    "贫困", "综合素质", "学有余力", "在读", "全日制", "不得兼报", "跨专业", "每人限",
    "无违纪", "处分", "学籍", "先修", "基础", "原创", "健康", "服从", "不可兼得", "全程",
    "指导教师", "每队", "人数", "团队人数", "名队员", "曾获", "经验",
)
# 这些词一旦出现，即使同时含身份词也保留为条件
COND_STRONG = ("家庭经济困难", "经济困难", "学有余力", "成绩优良", "无违纪", "优先",
               "不可兼得", "每人限", "不得", "原创", "健康", "服从", "全程")

CLAUSE_SPLIT = re.compile(r"[，,；;。\n]")
LABEL_RE = re.compile(r"^[一二三四五六七八九十\d]+[、.．)）]\s*")
OBJ_LABEL_RE = re.compile(r"^(?:[一二三四五六七八九十\d]+[、.．)）]\s*)?[^：:]{0,8}对象\s*[:：]\s*")


def _section_sentences(text: str, keys: tuple[str, ...]) -> list[str]:
    """取指定章节标题下的句子（如「三、申请条件」下的正文）。"""
    out: list[str] = []
    for head, body in blocks(text):
        if head and any(k in head for k in keys):
            out.extend(sentences(body))
    return out


def _extract_conditions(text: str, target: list[str] | None = None) -> tuple[list[str], str]:
    """抽出原子化资格约束。

    候选句有两个来源：
    1. 指定章节（条件/资格/要求/标准）——允许无提示词；
    2. "参赛对象：""评选条件："这类内联标签句，以及正文中出现情态动词的句子——
       这类来源更宽松，只采信规则命中的强约束，避免把身份描述当成条件。
    上述判断统一交给 conditions.canonicalize(strict=...)。
    """
    found: list[str] = []
    evidence = ""
    target = target or []

    sectioned = _section_sentences(text, ("条件", "资格", "要求", "标准"))
    sectioned_set = set(sectioned)
    # 对象章节要扫，但按 strict 处理：只有规则命中的原子约束才采信
    object_only = _section_sentences(text, ("对象", "范围"))
    pool = list(dict.fromkeys(
        sectioned
        + object_only
        + [s for s in sentences(text) if cond_mod.label_of(s)]
        + [s for s in sentences(text) if any(h in s for h in COND_HINT)]
    ))

    for sent in pool:
        if not cond_mod.is_candidate(sent):
            continue
        strict = sent not in sectioned_set
        for clause in CLAUSE_SPLIT.split(sent):
            clause = LABEL_RE.sub("", clause.strip()).strip()
            clause = OBJ_LABEL_RE.sub("", clause)
            if not (4 <= len(clause) <= 60):
                continue
            if any(x in clause for x in ("报名表", "材料", "上传", "附件")):
                continue
            target_overlap = any(t and (t in clause or clause in t) for t in target) and not any(
                s in clause for s in COND_STRONG
            )
            for kind, item in cond_mod.canonicalize(clause, strict=strict):
                # 纯身份描述与适用对象重叠时丢弃；规则命中的原子约束保留
                if kind == "fallback" and target_overlap:
                    continue
                if item not in found:
                    found.append(item)
                    if not evidence:
                        evidence = clause
            if len(found) >= 8:
                break
        if len(found) >= 8:
            break
    return found[:8], evidence


# ---------------- 报名材料 ----------------
MAT_SUFFIX = (
    "表", "书", "单", "件", "料", "明", "图", "片", "频", "码", "告", "册",
    "证", "信", "总结", "简历", "证书", "PPT", "说明", "代码", "简介", "意见",
)
MATERIAL_MAP: dict[str, str] = {
    # 长词必须排在短词之前："项目实施方案" 要先于 "实施方案" 命中
    "报名表": "报名表", "申请表": "申请表", "报名信息表": "报名表", "承诺书": "承诺书",
    "个人简历": "个人简历", "简历": "个人简历", "成绩单": "成绩单", "推荐信": "推荐信",
    "身份证": "身份证", "学生证": "学生证", "照片": "证件照片", "证书": "证书",
    "论文": "论文", "计划书": "项目计划书", "汇总表": "汇总表", "源代码": "源代码",
    "答辩PPT": "答辩PPT", "视频": "演示视频", "截图": "截图", "PPT": "答辩PPT",
    "申报书": "项目申报书", "预案": "预案", "证明": "证明材料",
    "总结": "个人总结", "事迹材料": "个人事迹材料", "佐证材料": "佐证材料", "决算表": "决算表",
    # 「方案」单用太泛（"解决方案""比赛方案"都会被误判成材料），
    # 只保留带限定词的真实材料名
    "实施方案": "实施方案", "项目方案": "项目方案", "活动方案": "活动方案",
    # 会议征稿与招聘场景的高频材料名
    "全文": "论文全文", "模板": "投稿模板", "个人陈述": "个人陈述",
    "推荐意见表": "推荐意见表", "推荐表": "推荐表",
    "教学设计方案": "教学设计方案", "作品原件": "作品原件", "报名回执": "报名回执",
    "作品说明": "作品说明", "申请审批表": "申请审批表",
}
# 「实践方案」「安全预案」是材料，「解决方案」「活动方案（作为正文叙述）」不是：
# 只接受"2-4 字限定词 + 方案"这种短材料名，长句一律走映射表。
SCHEME_RE = re.compile(r"^[\u4e00-\u9fa5]{2,4}方案$")
MATERIAL_HINT = ("提交", "上传", "附", "交", "携带", "提供", "材料", "一并", "备齐",
                 "准备", "报送", "投递", "发送", "寄送", "填报", "随附")
# 数量前缀：「两份《承诺书》」「三张照片」——数量不是材料名的一部分
MAT_QTY_RE = re.compile(r"^(?:[一二三四五六七八九十两\d]+\s*[份套张个本封])")
VERB_HINTS = ("扫描", "填写", "发送", "提交", "上传", "请", "需", "将", "并", "至", "于",
              "如", "按", "含", "附", "携带", "准备", "交至", "报送", "关注", "登录")
TRAILING_NOISE = re.compile(r"(各一份|各1份|一份|若干|等|原件|复印件|扫描件|电子版|纸质版|压缩包|一套)$")
PAREN_RE = re.compile(r"[（(][^）)]{0,40}[）)]")
MAT_LABEL_RE = re.compile(r"^(?:材料要求|提交材料|材料清单|申报材料|报名材料|需提交|需提供|提交)[:：]?")
# 这些只是章节名，不是材料条目
MAT_STOPWORDS = {"材料", "报名材料", "申报材料", "提交材料", "材料要求", "材料清单", "附件", "下列材料"}


def _clean_chunk(chunk: str) -> str:
    chunk = chunk.strip(" 《》“”\"'：:。；、")
    chunk = TRAILING_NOISE.sub("", chunk).strip()
    chunk = MAT_QTY_RE.sub("", chunk)          # 「两份《承诺书》」→「承诺书》
    return chunk.strip(" 《》“”\"'")


# 书名号与"填写XX表"是最稳定的材料信号，不受章节结构影响
DOC_BRACKET_RE = re.compile(r"《([^》]{2,20})》")
FILL_FORM_RE = re.compile(r"(?:填写|填报|下载|提交)([^，。；、\s]{2,10}(?:表|书|单))")


def _material_from_name(name: str) -> str | None:
    """把材料名映射到标准名；无法映射时返回清洗后的原名。"""
    for kw, canon in MATERIAL_MAP.items():
        if kw in name:
            return canon
    cleaned = _clean_chunk(TRAILING_NOISE.sub("", name))
    if 2 <= len(cleaned) <= 16:
        return cleaned
    return None


def _extract_materials(text: str) -> tuple[list[str], str]:
    found: list[str] = []
    evidence = ""
    # 「四、材料要求」标题下的条目即使没有"提交/材料"等提示词也要纳入
    candidates = _section_sentences(text, ("材料", "清单", "附件"))
    candidates += [s for s in sentences(text) if any(h in s for h in MATERIAL_HINT)]
    for sent in dict.fromkeys(candidates):
        body = PAREN_RE.sub("", sent)
        body = MAT_LABEL_RE.sub("", body)
        body = LABEL_RE.sub("", body)
        for chunk in re.split(r"[、，,；;/]|和|及", body):
            item = _clean_chunk(chunk)
            if not (2 <= len(item) <= 20) or item in MAT_STOPWORDS:
                continue
            if any(v in item for v in VERB_HINTS) or len(item) > 12:
                # 长句/动宾结构里只取已知材料名词，避免把整句当材料
                for kw, canon in MATERIAL_MAP.items():
                    if kw in item:
                        if canon not in found:
                            found.append(canon)
                        break
            elif SCHEME_RE.match(item) and item not in found:
                found.append(item)
            elif item.endswith(MAT_SUFFIX) and len(item) <= 10 and item not in found:
                found.append(item)
        if found and not evidence:
            evidence = sent[:100]

    # 补充通道：书名号内文档名与"填写XX表"，覆盖写在报名方式里的情况
    for sent in sentences(text):
        for m in DOC_BRACKET_RE.finditer(sent):
            name = _material_from_name(m.group(1))
            if name and name not in found and name not in MAT_STOPWORDS:
                found.append(name)
                evidence = evidence or sent[:100]
        for m in FILL_FORM_RE.finditer(sent):
            name = _material_from_name(m.group(1))
            if name and name not in found and name not in MAT_STOPWORDS:
                found.append(name)
                evidence = evidence or sent[:100]
    return found[:8], evidence


# ---------------- 报名方式 ----------------
def _extract_method(text: str) -> str:
    best = ""
    for sent in sentences(text):
        if not re.search(r"报名|申请|投递|提交", sent):
            continue
        if re.search(r"https?://|邮箱|@|系统|平台|扫码|二维码|QQ|微信|现场|小程序", sent):
            if re.search(r"https?://", sent):
                return sent[:160]
            if not best or len(sent) < len(best):
                best = sent[:160]
    return best


def extract(title: str, content: str, publish_time: str = "") -> dict:
    """规则抽取入口，返回扁平字典（含 evidence 与截止日期线索分）。"""
    text = content or ""
    publish = publish_time or ""
    ref_year = dt.date.today().year
    if publish:
        try:
            ref_year = dt.date.fromisoformat(publish[:10]).year
        except ValueError:
            pass

    dates = find_dates(text, ref_year)
    dates = [
        {**d, "date": dt.date(infer_year(d["date"].month, publish), d["date"].month, d["date"].day)
         if not d["has_year"] else d["date"]}
        for d in dates
    ]

    dl = _pick_deadline(dates, text)
    relative_note = ""
    deadline_source = "absolute" if dl else ""
    if dl is None:
        # 技术文档2 第 21 章：相对时间结合发布时间换算；无法确定则置空待复核
        rel, rel_ev, rel_note = reltime.resolve_relative(text, publish)
        if rel:
            dl = {"value": rel, "score": 3.0, "kind": deadline_kind(rel_ev), "evidence": rel_ev}
            deadline_source = "relative"
        elif rel_note:
            relative_note = rel_note
    target, target_ev = _extract_target(text, title)
    conditions, cond_ev = _extract_conditions(text, target)
    materials, mat_ev = _extract_materials(text)
    method = _extract_method(text)
    # 设计文档 8.2 规定的约束类 / 时间类 / 联络类字段，复用同一批日期候选，
    # 避免二次扫描产生与截止日期不一致的年份推断。
    structured, struct_ev = fields_mod.extract_structured(
        text, dates, target, conditions, dl["value"] if dl else None
    )
    # 字段契约对齐总控文档 8.2：start_time = 报名开始，event_time = 活动举办时间。
    # 早期 _pick_start 实际抽的是「举行/举办」线索（活动开始），语义放在 start_time
    # 是错的；这里把它降为 event_time 的兜底，避免两个字段重复又漏掉报名开始。
    if not structured.get("event_time"):
        fallback = _pick_start(dates, text, dl["value"] if dl else None)
        if fallback:
            structured["event_time"] = fallback
            # 证据必须取原文所在行，不能写拼装描述串（接地校验会拦截）
            for d in dates:
                if d["date"].isoformat() == fallback:
                    lo = text.rfind("\n", 0, d["start"]) + 1
                    hi = text.find("\n", d["start"])
                    struct_ev["event_time"] = text[lo: hi if hi != -1 else len(text)].strip()[:80]
                    break
    ev = {
        "deadline": dl["evidence"] if dl else "",
        "target": target_ev,
        "conditions": cond_ev,
        "materials": mat_ev,
        "apply_method": method[:80] if method else "",
    }
    ev.update(struct_ev)

    return {
        "deadline": dl["value"] if dl else None,
        "deadline_score": dl["score"] if dl else 0.0,
        "deadline_kind": dl["kind"] if dl else "",
        "deadline_source": deadline_source,
        "relative_note": relative_note,
        "start_time": structured.get("registration_start"),
        "target": target,
        "conditions": conditions,
        "materials": materials,
        "apply_method": method,
        "evidence": ev,
        **structured,
    }
