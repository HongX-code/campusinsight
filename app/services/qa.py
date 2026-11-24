"""智能问答：限定在"已收录通知"范围内作答，并强制给出原文出处。

检索策略（轻量、可解释、不依赖向量库）：
    查询清洗（去指代 / 人称 / 口语）-> 中文二元切分 + 英文数字词
    -> 同义词扩展（低权重）-> 标题加权 + 类目 / 结构化字段加权 -> TopK
    相关性门槛只按「查询原词」的覆盖率计算，同义词只加分、不降低门槛。
回答策略：
    先识别意图（材料 / 截止 / 条件 / 名单 / 联系方式 / 地点 / 报名方式），
    再优先使用已落库的结构化字段组装带编号的可核对答案；字段为空就写「原文未写明」。
    有模型时把结构化字段一并交给模型并要求引用编号，越界或无模型则回退结构化抽取，
    保证"答不了就不编"，并把引擎与降级原因如实返回给界面。
"""
from __future__ import annotations

import datetime as dt
import re
import time

from app.config import CATEGORIES
from app.services.llm_client import llm
from app.services.textutil import sentences

STOPWORDS = set(
    "的了和与及或在是为对于请问什么哪些如何怎么哪个我你他她它这那有可以需要能够是否吗呢啊吧"
    "学校学院通知公告一下介绍说明具体相关情况问题"
)

QA_SYSTEM = """你是"高校智讯"的通知问答助手。你只能依据【已知通知片段】与【结构化字段】回答，严禁使用片段之外的知识。
要求：
1. 优先使用结构化字段里的确切值（截止日期、材料清单、申请条件、适用对象、联系方式），不要自行推算或补全。
2. 结构化字段为空时写"原文未写明"，不要编造。
3. 回答简洁，先给结论，再给关键信息。
4. 每个结论后面用 [1]、[2] 标注来源，编号必须对应给定片段序号，不得编造不存在的编号。
5. 使用中文，不超过 200 字。"""


# 疑问 / 指代 / 人称 / 口语填充词：不携带检索语义，参与分词会稀释覆盖率。
# 长词必须排在短词前面（正则交替按书写顺序取首个匹配）。
FILLER_RE = re.compile(
    r"(适合我的|适合我|我的|这个|那个|这|那|它|"
    r"最近|近期|即将|马上|目前|现在|"
    r"什么时候|何时|几号|哪天|时候|"
    r"有哪些|哪些|有什么|什么|怎么样|怎么|如何|请问|麻烦|"
    r"可以|能否|需要|要求|介绍一下|一下|帮我|我想|知道|"
    r"吗|呢|啊|吧)"
)

# 粘连助词 / 指代词产生的二元词（如「的竞」「个竞」「我的」）不携带语义。
GLUE_CHARS = set("的了和与及或在是我这那个其之")

# 校园语境的同义词：只用于「加分」，不替代原词覆盖率门槛。
SYNONYMS: dict[str, tuple[str, ...]] = {
    "奖学金": ("奖助学金", "助学金", "评奖评优", "奖励", "资助"),
    "助学金": ("奖学金", "困难补助", "资助", "绿色通道"),
    "竞赛": ("比赛", "大赛", "赛事", "选拔赛"),
    "比赛": ("竞赛", "大赛", "赛事"),
    "讲座": ("学术报告", "报告会", "讲坛", "沙龙"),
    "志愿": ("志愿服务", "义工", "公益"),
    "支教": ("志愿服务", "支教团", "公益"),
    "实习": ("就业", "招聘", "宣讲会", "校招"),
    "招聘": ("就业", "实习", "宣讲会", "校招"),
    "报名": ("申报", "申请", "提交"),
    "截止": ("截止时间", "办理时限", "逾期"),
    "材料": ("提交材料", "材料要求", "附件"),
    "条件": ("资格", "申请条件", "要求"),
    "保研": ("推免", "推荐免试", "免试攻读"),
    "推免": ("保研", "推荐免试"),
    "评优": ("评奖", "奖学金", "荣誉称号"),
    "困难": ("家庭经济困难", "资助", "绿色通道"),
}

# 领域词 -> 主题判别词。用于类目加权与「正文碰巧提到」的误召回剔除。
DOMAIN_HINTS: dict[str, tuple[str, ...]] = {
    "竞赛": ("竞赛", "大赛", "选拔赛"),
    "奖学金": ("奖学金", "奖助学金", "励志奖学金"),
    "助学金": ("助学金", "困难补助", "资助"),
    "讲座": ("讲座", "学术报告", "论坛", "讲堂"),
    "志愿服务": ("志愿服务", "志愿者", "支教"),
    "实习": ("实习", "招聘", "宣讲会", "双选会"),
    "科研项目": ("大创", "创新创业训练", "结题"),
}

# 顺序敏感：属性类意图优先于「有哪些」的清单意图。
INTENT_RULES: tuple[tuple[str, str], ...] = (
    ("materials", r"材料|准备什么|需要准备|提交什么|提交哪些|带什么|附件|材料清单"),
    ("deadline", r"截止|何时|什么时候|几号|哪天|逾期|报名时间|时间安排"),
    ("condition", r"条件|资格|要求|谁能|面向|对象|限制|标准|排名"),
    ("contact", r"联系方式|联系人|电话|邮箱|咨询"),
    ("location", r"地点|在哪|哪里|地址|教室|会议室|场地"),
    ("method", r"怎么报名|如何报名|怎样报名|报名方式|申请方式|报名流程|怎么申请|如何申请|怎么参加"),
    ("list", r"有哪些|哪些|列出|清单|列表|有什么"),
)

INTENT_LABELS = {
    "materials": "查提交材料", "deadline": "查截止时间", "condition": "查申请条件",
    "contact": "查联系方式", "location": "查地点", "method": "查报名方式",
    "list": "列相关通知", "general": "综合问答",
}

# 意图 -> 优先展示的原文依据字段
EVIDENCE_FIELD = {
    "materials": "materials", "deadline": "deadline", "condition": "conditions",
    "contact": "contact", "location": "location", "method": "apply_method",
}

# 参与召回的结构化字段（值本身可检索，但权重低于正文）
STRUCTURED_TEXT_FIELDS = (
    "materials", "conditions", "target", "grade_requirement", "major_requirement",
    "apply_method", "location", "contact", "deadline_kind",
)

CITE_RE = re.compile(r"\[([\d\s,，]+)\]")
DEICTIC_RE = re.compile(r"^(这个|那个|这|那|它|该)(的|项|条|个)?")


def _cite_ids(text: str) -> list[int]:
    """兼容 [1] 与 [1,2] / [1，2] 两种模型常见写法。"""
    ids: list[int] = []
    for group in CITE_RE.findall(text or ""):
        ids.extend(int(x) for x in re.findall(r"\d{1,2}", group))
    return ids


def _terms(query: str) -> set[str]:
    q = FILLER_RE.sub(" ", query or "")
    q = re.sub(r"[^\w\u4e00-\u9fa5]", " ", q)
    terms: set[str] = set()
    for seg in re.findall(r"[\u4e00-\u9fa5]+", q):
        if len(seg) <= 2:
            terms.add(seg)
        else:
            for i in range(len(seg) - 1):
                terms.add(seg[i:i + 2])
    for word in re.findall(r"[A-Za-z0-9]+", q):
        terms.add(word.lower())
    return {
        t for t in terms
        if t and t not in STOPWORDS
        and not (len(t) == 2 and any(ch in GLUE_CHARS for ch in t))
    }


def _synonym_terms(query: str, base_terms: set[str]) -> dict[str, str]:
    """按查询里出现的领域词补充同义词，并记录它对应的原词。

    返回 {扩展词: 原词}。命中扩展词时按原词计入覆盖率，但只给 0.5 权重，
    这样「保研 → 推免」能检索到，又不会让同义词单独把不相关通知拉进来。
    """
    extras: dict[str, str] = {}
    q = query or ""
    for key, words in SYNONYMS.items():
        if key not in q:
            continue
        for word in words:
            if word not in base_terms:
                extras.setdefault(word, key)
    return extras


def detect_domain(query: str) -> str:
    """返回查询命中的领域键；没有明确领域时返回空串。"""
    q = query or ""
    for key, words in DOMAIN_HINTS.items():
        if any(w in q for w in words):
            return key
    return ""


def detect_intent(query: str) -> str:
    """把口语问题归类到可结构化作答的意图，无法归类时走综合问答。"""
    q = query or ""
    for key, pattern in INTENT_RULES:
        if re.search(pattern, q):
            return key
    return "general"


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(str(x) for x in value if x)
    if isinstance(value, dict):
        return " ".join(f"{k} {v}" for k, v in value.items() if v)
    return str(value)


def retrieve(query: str, notices: list[dict], top_k: int = 3) -> list[dict]:
    base_terms = _terms(query)
    if not base_terms:
        return []
    extra_terms = _synonym_terms(query, base_terms)
    domain = detect_domain(query)
    scored = []
    for n in notices:
        title = (n.get("title") or "").lower()
        body = (n.get("content") or "").lower()
        structured = " ".join(_as_text(n.get(k)) for k in STRUCTURED_TEXT_FIELDS).lower()
        score = 0.0
        matched: set[str] = set()
        for t in base_terms | set(extra_terms):
            weight = 1.0 if t in base_terms else 0.5
            if t in title:
                score += title.count(t) * 3.0 * weight
                matched.add(t if t in base_terms else extra_terms[t])
            if t in body:
                score += min(body.count(t), 6) * 1.0 * weight
                matched.add(t if t in base_terms else extra_terms[t])
            elif structured and t in structured:
                # 结构化字段只补分，不冒充正文命中次数
                score += 0.8 * weight
                matched.add(t if t in base_terms else extra_terms[t])
        if n.get("category") in CATEGORIES and n["category"] in query:
            score += 2.0
        if domain:
            words = DOMAIN_HINTS[domain]
            subject = title + (n.get("category") or "").lower()
            if any(w in subject for w in words):
                score += 2.5
        # 新闻稿 / 不可办理通知不参与「怎么办」类问答的主排序
        if n.get("genre") == "news" or n.get("actionable") is False:
            score *= 0.4
        # 相关性门槛：按查询原词的命中覆盖率计算；同义词命中折算到它对应的原词
        coverage = len(matched & base_terms) / len(base_terms)
        if score > 0 and matched and coverage >= 0.5:
            scored.append((score, n))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"score": round(s, 2), **n} for s, n in scored[:top_k]]


def _focus_hits(query: str, hits: list[dict]) -> list[dict]:
    """按主题聚焦：剔除「正文碰巧提到、但标题与类目都不相关」的误召回。

    例如问奖学金时，推免招生简章因正文出现「奖学金」被召回，但主题不符。
    若全部条目都被剔除，则保留原结果，不人为制造 no-hit。
    """
    domain = detect_domain(query)
    if not domain:
        return hits
    words = DOMAIN_HINTS[domain]
    focused = [h for h in hits
               if any(w in ((h.get("title") or "") + (h.get("category") or ""))
                      for w in words)]
    return focused or hits


RECENT_RE = re.compile(r"近期|最近|即将|马上|当前|本月")
OPEN_RE = re.compile(r"可以报名|可报名|正在报名|还能报名|现在报名")


def _upcoming_by_deadline(query: str, notices: list[dict], limit_k: int) -> list[dict]:
    """「近期有哪些截止事项」直接查结构化日期字段，不靠全文检索碰运气。

    全文检索会把「截止」字样多的历史通知排到前面，导致问近期却答出半年前的过期事项。
    问日期就应该按日期字段筛：排除已过期、按由近到远排、再按主题聚焦。
    """
    today = dt.date.today().isoformat()
    rows = [n for n in notices if str(n.get("deadline") or "") >= today]
    if not rows:
        return []
    rows = _focus_hits(query, rows)
    rows.sort(key=lambda n: str(n.get("deadline") or "9999-12-31"))
    return [{"score": 0.0, **n} for n in rows[:limit_k]]


def _norm_subject(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]", "", text or "").lower()


def _longest_subject(query: str, hits: list[dict]) -> str:
    """找出查询里「完整出现在某条通知标题中」的最长片段，用于精确点名定位。

    「华为ICT大赛的报名截止时间」-> 「华为ICT大赛」；
    「国家奖学金的申请条件」-> 「国家奖学金」，避免把同名近亲通知混进答案。
    """
    q = _norm_subject(query)
    titles = [_norm_subject(h.get("title", "")) for h in hits]
    for length in range(min(len(q), 14), 2, -1):
        for i in range(len(q) - length + 1):
            frag = q[i:i + length]
            if any(frag in t for t in titles):
                return frag
    return ""


def _focus_exact(query: str, hits: list[dict]) -> list[dict]:
    """查询点名了具体通知时，只回答被点名的那条，不做近似扩散。"""
    subject = _longest_subject(query, hits)
    if len(subject) < 3:
        return hits
    exact = [h for h in hits if subject in _norm_subject(h.get("title", ""))]
    return exact or hits


def _best_sentences(query: str, content: str, limit: int = 2) -> list[str]:
    terms = _terms(query)
    ranked = []
    for sent in sentences(content):
        sent = sent.strip()
        if not (8 <= len(sent) <= 160):
            continue
        hit = sum(1 for t in terms if t in sent)
        if hit:
            ranked.append((hit, len(sent), sent))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    return [r[2] for r in ranked[:limit]]


def _snippet(content: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", content or "").strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def citation_check(text: str, total: int) -> tuple[bool, list[int], str]:
    """校验模型回答里的引用编号：越界即视为幻觉信号。

    返回 (是否可核对, 越界编号, 说明)。没有编号不算越界，但标记为「未标注」，
    提示用户以来源列表为准；编号越界则调用方会回退到抽取式回答。
    """
    ids = _cite_ids(text)
    if not ids:
        return False, [], "回答未标注来源编号，请以引用列表核对原文"
    invalid = sorted({i for i in ids if i < 1 or i > total})
    if invalid:
        return False, invalid, f"回答引用了不存在的来源编号 {invalid}，已回退为抽取式回答"
    return True, [], ""


def _citations(query: str, hits: list[dict], intent: str = "") -> list[dict]:
    out = []
    for i, h in enumerate(hits):
        ev = h.get("evidence") or {}
        key = EVIDENCE_FIELD.get(intent)
        raw = _as_text(ev.get(key)) if isinstance(ev, dict) and key else ""
        raw = re.sub(r"\s+", " ", raw).strip()
        best = _best_sentences(query, h.get("content", ""), limit=1)
        snippet = raw[:160] if raw else (best[0] if best else _snippet(h.get("content", "")))
        out.append({
            "index": i + 1,
            "notice_id": h["id"],
            "title": h.get("title", ""),
            "source_url": h.get("source_url", ""),
            "snippet": snippet,
            "score": h["score"],
        })
    return out


def _list_values(value) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    if value is None:
        return []
    s = str(value).strip()
    return [s] if s else []


def _contact_text(value) -> str:
    if isinstance(value, dict):
        bits = _list_values(value.get("person"))
        bits += _list_values(value.get("phone"))
        bits += _list_values(value.get("email"))
        return "、".join(bits)
    return str(value or "").strip()


def _section_excerpt(content: str, keywords: tuple[str, ...], limit: int = 100) -> str:
    """结构化字段为空时的原文兜底：按「三、报名方式」「联系人：」这类小标题抽取说明。

    只取小标题之后、下一个编号小标题之前的正文，避免把整段通知塞进一个字段；
    抽不到（返回空串）时由调用方如实写「原文未写明」，不编造。
    """
    text = re.sub(r"[ \t]+", " ", content or "").strip()
    if not text:
        return ""
    for kw in keywords:
        m = re.search(rf"(?:[一二三四五六七八九十\d]+[、.．]\s*)?{re.escape(kw)}\s*[:：]?\s*(.+)", text)
        if not m:
            continue
        val = re.split(r"[一二三四五六七八九十]+[、.．]", m.group(1))[0]
        val = re.sub(r"\s+", " ", val).strip(" 。；;，,")
        if len(val) >= 4:
            return val[:limit]
    return ""


def _structured_answer(query: str, hits: list[dict], intent: str) -> tuple[str, list[dict]]:
    """用已落库的结构化字段组装答案；每个结论带可核对的 [n] 编号。"""
    lead = {
        "materials": "我查了已收录的通知，关于需要准备的材料，整理如下：",
        "deadline": "关于截止时间，我在已收录通知里查到的信息如下：",
        "condition": "关于申请条件，根据已收录通知整理如下：",
        "contact": "关于联系方式，我在已收录通知里查到的信息如下：",
        "location": "关于地点，我在已收录通知里查到的信息如下：",
        "method": "关于报名方式，我在已收录通知里查到的信息如下：",
        "list": f"在已收录的通知里，与「{(detect_domain(query) or '该主题')}」相关的可办理事项共 {len(hits)} 条：",
        "general": "根据已收录通知，与你这个问题最相关的信息如下：",
    }[intent]
    if intent == "deadline" and RECENT_RE.search(query or ""):
        lead = "按截止日期由近到远排列，已收录通知中尚未截止的事项如下："
    if intent == "list" and OPEN_RE.search(query or ""):
        lead = (f"在已收录通知中，与「{detect_domain(query) or '该主题'}」相关且尚未截止的"
                f"共 {len(hits)} 条：")

    lines: list[str] = [lead]
    used_rows: list[dict] = []
    for i, h in enumerate(hits):
        idx = i + 1
        title = h.get("title") or "（无标题）"
        used: dict[str, object] = {}
        bits: list[str] = []

        if intent == "materials":
            mats = _list_values(h.get("materials"))
            used["materials"] = mats
            bits.append("需提交：" + "、".join(mats) if mats else "原文未明确列出材料")
            d = str(h.get("deadline") or "").strip()
            if d:
                used["deadline"] = d
                bits.append(f"截止 {d}")

        elif intent == "deadline":
            d = str(h.get("deadline") or "").strip()
            used["deadline"] = d
            bits.append(f"截止 {d}" if d else "原文未写明截止日期")
            kind = str(h.get("deadline_kind") or "").strip()
            if d and kind:
                bits[-1] += f"（{kind}）"
            start = str(h.get("registration_start") or h.get("start_time") or "").strip()
            if start:
                used["registration_start"] = start
                bits.append(f"报名开始 {start}")

        elif intent == "condition":
            conds = _list_values(h.get("conditions"))
            used["conditions"] = conds
            bits.append("申请条件：" + "、".join(conds) if conds else "原文未明确列出条件")
            targets = _list_values(h.get("target")) + _list_values(h.get("grade_requirement"))
            targets = list(dict.fromkeys(targets))
            if targets:
                used["target"] = targets
                bits.append("面向：" + "、".join(targets[:5]))

        elif intent == "contact":
            contact = _contact_text(h.get("contact"))
            if not contact:
                contact = _section_excerpt(h.get("content", ""), ("联系方式", "联系人", "咨询电话", "联系电话"))
            used["contact"] = contact
            bits.append("联系方式：" + contact if contact else "原文未写明联系方式")

        elif intent == "location":
            loc = str(h.get("location") or "").strip()
            if not loc:
                loc = _section_excerpt(h.get("content", ""), ("地点", "场地", "教室", "会议室"))
            used["location"] = loc
            bits.append("地点：" + loc if loc else "原文未写明地点")

        elif intent == "method":
            method = str(h.get("apply_method") or "").strip()
            if not method:
                method = _section_excerpt(h.get("content", ""), ("报名方式", "报名方法", "申请方式", "参与方式"))
            used["apply_method"] = method
            bits.append("报名方式：" + method if method else "原文未写明报名方式")
            d = str(h.get("deadline") or "").strip()
            if d:
                used["deadline"] = d
                bits.append(f"截止 {d}")

        elif intent == "list":
            d = str(h.get("deadline") or "").strip()
            targets = _list_values(h.get("target"))
            mats = _list_values(h.get("materials"))
            used = {"deadline": d, "target": targets[:3], "materials": mats[:4]}
            if d:
                flag = "（已截止）" if d < dt.date.today().isoformat() else ""
                bits.append(f"截止 {d}{flag}")
            if targets:
                bits.append("面向 " + "、".join(targets[:3]))
            if mats:
                bits.append("需提交 " + "、".join(mats[:4]))
            if not bits:
                bits.append("可办理通知，具体信息请查看原文")

        else:  # general
            best = _best_sentences(query, h.get("content", ""), limit=2)
            used["excerpt"] = best
            bits.append("；".join(best) if best else "请查看原文")
            d = str(h.get("deadline") or "").strip()
            if d:
                used["deadline"] = d
                bits.append(f"截止 {d}")

        lines.append(f"[{idx}]《{title}》：" + "；".join(bits) + "。")
        used_rows.append({"index": idx, "notice_id": h.get("id"), "fields": used})

    lines.append("以上信息都是从通知原文中抽取的，编号对应下方来源，点开即可核对原文。")
    return "\n".join(lines), used_rows


def _extractive(query: str, hits: list[dict], citations: list[dict], started: float,
                fallback_reason: str = "") -> dict:
    """兼容保留：模型不可用且无结构化字段时的纯原文摘取兜底。"""
    if re.search(r"哪(些|几个)|有什么|几种|清单|列表", query):
        lines = [f"已收录的通知中，与你问题相关的共 {len(hits)} 条："]
        lines += [f"[{i + 1}]《{h.get('title', '')}》" for i, h in enumerate(hits)]
        lines.append("点击下方来源可查看原文、截止时间与报名材料。")
        return {
            "answer": "\n".join(lines), "citations": citations, "mode": "extractive",
            "engine": "rule", "hit": True, "citation_ok": True,
            "fallback_reason": fallback_reason,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }

    parts: list[str] = []
    for i, h in enumerate(hits[:2]):
        sents = _best_sentences(query, h.get("content", ""))
        if sents:
            parts.append(f"根据《{h.get('title','')}》[{i + 1}]：{'；'.join(sents)}。")
    if not parts:
        parts.append(f"已找到 {len(hits)} 条相关通知，其中《{hits[0].get('title','')}》最相关，请查看原文。")
    return {
        "answer": "\n".join(parts), "citations": citations, "mode": "extractive",
        "engine": "rule", "hit": True, "citation_ok": True,
        "fallback_reason": fallback_reason,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


def _base(query_terms: list[str], intent: str, started: float, **kwargs) -> dict:
    """统一补齐所有返回分支都应有的诊断字段。"""
    return {
        "query_terms": query_terms,
        "intent": intent,
        "intent_label": INTENT_LABELS.get(intent, "综合问答"),
        "answer_fields": [],
        "latency_ms": int((time.perf_counter() - started) * 1000),
        **kwargs,
    }


def answer(query: str, notices: list[dict], top_k: int = 3) -> dict:
    """返回 {answer, citations, mode, engine, hit, citation_ok, intent, answer_fields, ...}。"""
    started = time.perf_counter()
    query_terms = sorted(_terms(query))[:12]
    intent = detect_intent(query)

    # 纯指代且没有具体名词：不猜、不编，明确请用户点名通知。
    if DEICTIC_RE.match((query or "").strip()) and len(query_terms) <= 1:
        return _base(
            query_terms, intent, started,
            answer="请说明具体是哪一条通知，例如「数学建模竞赛需要提交哪些材料？」；"
                   "也可以先打开通知详情，再针对它提问。",
            citations=[], mode="clarify", engine="rule", hit=False,
            citation_ok=True, fallback_reason="",
        )

    limit_k = max(1, min(10, top_k))
    recent_deadline = intent == "deadline" and bool(RECENT_RE.search(query or ""))
    open_list = intent == "list" and bool(OPEN_RE.search(query or ""))
    # 「有哪些…可以报名」是先召回、再按日期剔除过期项；召回面要够宽，
    # 否则 top_k 命中的恰好都是过期通知时，过滤后只剩一两条，清单显得单薄。
    effective_k = max(limit_k, 12) if (recent_deadline or open_list) else limit_k
    hits = retrieve(query, notices, top_k=effective_k)
    if recent_deadline:
        upcoming = _upcoming_by_deadline(query, notices, limit_k)
        if upcoming:
            hits = upcoming
    if not hits:
        return _base(
            query_terms, intent, started,
            answer="已收录的通知中暂未找到相关信息。可以换一个关键词，或在「通知中心」中先录入相关通知。",
            citations=[], mode="no-hit", engine="rule", hit=False,
            citation_ok=True, fallback_reason="",
        )

    hits = _focus_hits(query, hits)
    hits = _focus_exact(query, hits)
    # 「可以报名」只列尚未截止、且确实可办理的通知：
    # 过期通知不当机会推荐，新闻稿（genre=news / actionable=False）也不混进清单。
    if intent == "list" and OPEN_RE.search(query or ""):
        today = dt.date.today().isoformat()
        open_hits = [h for h in hits
                     if h.get("genre") != "news" and h.get("actionable") is not False
                     and (not h.get("deadline") or str(h["deadline"]) >= today)]
        if open_hits:
            hits = open_hits[:limit_k]
    citations = _citations(query, hits, intent)
    context = "\n\n".join(
        f"[{i + 1}] 标题：{h.get('title','')}\n内容：{(h.get('content') or '')[:900]}\n"
        f"结构化字段：截止={h.get('deadline') or '未写明'}；"
        f"材料={'、'.join(_list_values(h.get('materials'))) or '未写明'}；"
        f"条件={'、'.join(_list_values(h.get('conditions'))) or '未写明'}；"
        f"对象={'、'.join(_list_values(h.get('target'))) or '未写明'}；"
        f"报名方式={h.get('apply_method') or '未写明'}"
        for i, h in enumerate(hits)
    )

    fallback_reason = ""
    if llm.ready:
        try:
            reply = llm.chat(
                [
                    {"role": "system", "content": QA_SYSTEM},
                    {"role": "user", "content": f"【已知通知片段】\n{context}\n\n【用户问题】{query}"},
                ],
                temperature=0.2,
                max_tokens=500,
            ).strip()
            ok, invalid, note = citation_check(reply, len(citations))
            if not invalid:
                return _base(
                    query_terms, intent, started,
                    answer=reply, citations=citations, mode="llm", engine="llm",
                    hit=True, citation_ok=ok,
                    fallback_reason="" if ok else note,
                    answer_fields=[],
                )
            fallback_reason = note
        except Exception as exc:
            fallback_reason = f"大模型暂不可用，已使用结构化抽取回答：{str(exc)[:80]}"

    text, used_rows = _structured_answer(query, hits, intent)
    ok, invalid, note = citation_check(text, len(citations))
    if invalid:
        # 结构化答案理论上不会越界；真出现时退回纯原文摘取，绝不带病输出。
        legacy = _extractive(query, hits, citations, started, note)
        legacy.update(_base(query_terms, intent, started))
        legacy["latency_ms"] = int((time.perf_counter() - started) * 1000)
        return legacy

    return _base(
        query_terms, intent, started,
        answer=text, citations=citations, mode="structured", engine="rule",
        hit=True, citation_ok=ok, fallback_reason=fallback_reason,
        answer_fields=used_rows,
    )
