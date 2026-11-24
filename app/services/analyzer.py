"""AI 解析编排：提示词构建 -> 模型调用 -> JSON 校验 -> 原文接地校验 -> 置信度评分。

四层保护（对应申报材料中的"可信 AI"设计）：
    1. 结构保护：强制 JSON Schema，输出后做类型/枚举校验；
    2. 文体保护：先判事务通知 / 公示公告 / 新闻资讯，新闻稿整体不抽报名类字段；
       规则与大模型两条链路共用 `_apply_genre_gate()`，换引擎也不会重现误报；
    3. 事实保护：抽出的日期与字段必须能在原文中定位（grounding），否则置空并标记待复核；
    4. 兜底保护：模型不可用或返回异常时，自动切换规则引擎，引擎来源始终对用户可见。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re

from app.config import CATEGORIES
from app.services import classifier, extractor, fields as fields_mod, genre, validator
from app.services.cache import cache
from app.services.llm_client import LLMError, llm
from app.services.textutil import normalize_text

CATEGORY_LIST = "、".join(CATEGORIES)

SYSTEM_PROMPT = f"""你是一个面向中国高校场景的通知公告结构化解析引擎。
你的任务是把一段高校通知原文转换为严格的结构化 JSON，供学生端做条件匹配与时间提醒。

必须遵守的规则：
1. 只允许从原文中抽取事实，禁止推理、禁止补全、禁止编造。原文没有的信息一律输出 null 或空数组。
2. deadline 只填写"报名/申报/提交的截止日期"，绝对不能填发布时间、活动举办时间、评审时间、公示时间。
3. 所有 date 字段格式统一为 YYYY-MM-DD；原文只有"X月X日"没有年份时，结合发布时间推断年份。
   start_time 表示报名开始时间，不要填活动/讲座举办时间；后者由程序抽取为 event_time。
4. category 只能是以下之一：{CATEGORY_LIST}。
5. evidence 中每个字段都必须是从原文中逐字复制的片段（不超过 60 字），用于人工核对；没有依据的字段不要出现在 evidence 中。
6. 如果某字段把握不大，就留空并降低 confidence，不要猜测。
7. needs_review 在以下情况必须为 true：deadline 为空、category 为其他、原文表述自相矛盾。
8. 若原文是新闻资讯/成果报道（无报名、申报、提交等办理事项），所有办理类字段一律输出 null 或空数组，
   不要把会议举办时间、赛事日期、公示期、论文里的学科名词当成截止日期或报名材料。
9. 年级/专业限制、活动与报名起止时间、地点、联系人电话属于「字面事实」，由程序的确定性抽取器
   从原文捕获并做接地校验，不由模型生成；你不需要输出这些字段，更不要推测或补全。

输出 JSON Schema：
{{
  "category": "string",
  "deadline": "YYYY-MM-DD 或 null",
  "start_time": "YYYY-MM-DD 或 null",
  "target": ["适用对象"],
  "conditions": ["资格条件"],
  "materials": ["报名材料"],
  "apply_method": "报名方式的简短描述",
  "evidence": {{"deadline": "原文片段", "target": "原文片段", "conditions": "原文片段", "materials": "原文片段"}},
  "confidence": 0.0,
  "needs_review": true
}}
只输出 JSON，不要输出任何解释文字。"""

USER_TEMPLATE = """【通知标题】{title}
【发布时间】{publish_time}
【通知原文】
{content}
"""

_ARABIC_CN = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _norm(text: str) -> str:
    return re.sub(r"[\s\u3000]", "", text or "")


def date_grounded(deadline: str, text: str) -> bool:
    """接地校验：抽出的日期必须能在原文里找到对应表述。"""
    try:
        d = dt.date.fromisoformat(deadline)
    except (TypeError, ValueError):
        return False
    body = _norm(text)
    candidates = [
        f"{d.year}年{d.month}月{d.day}日",
        f"{d.year}年{d.month:02d}月{d.day:02d}日",
        f"{d.year}-{d.month:02d}-{d.day:02d}",
        f"{d.year}-{d.month}-{d.day}",
        f"{d.year}/{d.month}/{d.day}",
        f"{d.year}.{d.month}.{d.day}",
        f"{d.month}月{d.day}日",
        f"{d.month}月{d.day}号",
    ]
    return any(c in body for c in candidates)


def evidence_grounded(fragment: str, text: str) -> bool:
    """证据片段接地：取前 8 个非空白字符做定位，容忍模型少量改写。"""
    frag = _norm(fragment)[:8]
    if not frag:
        return True
    return frag in _norm(text)


def _clean_list(value, limit: int = 6) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, (int, float)):
            item = str(item)
        if not isinstance(item, str):
            continue
        item = item.strip(" 。；;，,")
        if item and len(item) <= 80 and item not in out:
            out.append(item)
    return out[:limit]


def _attach_literal_fields(result: dict, fields: dict, content: str) -> dict:
    """挂载「字面事实」字段：年级/专业限制、报名起止、活动时间、地点、联络方式。

    这些字段由 fields.py 从原文确定性捕获，规则与大模型两条引擎共用同一份结果。
    每个字段都必须能通过原文接地校验；证据对不上就整字段置空，宁缺勿造。
    这样设计是为了避免让生成式模型输出电话、邮箱、专业名这类最容易被编造的字面值。
    """
    ev = dict(result.get("evidence") or {})
    src_ev = fields.get("evidence") or {}
    for key in fields_mod.FIELD_KEYS:
        value = fields.get(key)
        fragment = str(src_ev.get(key) or "")
        if value and fragment and evidence_grounded(fragment, content):
            result[key] = value
            ev[key] = fragment[:120]
        else:
            result[key] = fields_mod.empty_fields()[key]
            ev.pop(key, None)
    result["evidence"] = ev
    return result


def _finalize(result: dict, content: str, publish: str) -> dict:
    """解析链路的统一出口：跑规则校验，把硬异常写回复核状态。

    技术文档2 第 57 章「AI 负责理解，程序负责约束，原文负责证明」在这里落地：
    无论走规则还是大模型，最后都要过同一套校验，换引擎不会绕过约束。
    """
    result.setdefault("degraded", False)
    result.setdefault("fallback_reason", "")
    result.setdefault("fallback_kind", "")
    report = validator.audit(result, content, publish)
    if result.get("relative_note"):
        report["issues"].append(result["relative_note"])
        report["ok"] = False
    result["checks"] = report
    if report["issues"]:
        result["needs_review"] = True
        extra = "规则校验：" + "；".join(report["issues"])
        result["review_note"] = "；".join(
            x for x in (result.get("review_note") or "", extra) if x
        )
    return result


def _apply_genre_gate(result: dict, info: dict) -> dict:
    """文体闸门：新闻资讯不产出报名类字段，避免「看起来很合理」的假数据。

    规则引擎与大模型两条链路都要过这道闸，否则换上 MaaS 之后同样的误报会重现。
    """
    result["genre"] = info["genre"]
    result["genre_label"] = info["label"]
    result["genre_reason"] = info["reason"]
    result["actionable"] = info["actionable"]

    if info["actionable"]:
        # 事务通知 / 公示公告：给截止日期补一个语义类型，展示时不再笼统称「报名截止」
        ev = (result.get("evidence") or {}).get("deadline") or ""
        result["deadline_kind"] = extractor.deadline_kind(ev) if result.get("deadline") else ""
        return result

    result.update({
        "deadline": None,
        "start_time": None,
        "target": [],
        "conditions": [],
        "materials": [],
        "apply_method": "",
        "evidence": {},
        "deadline_kind": "",
        **fields_mod.empty_fields(),
        "confidence": round(min(float(result.get("confidence") or 0.6), 0.62), 2),
        # 没有可办理字段，也就没有需要人工核对的截止日期
        "needs_review": False,
    })
    # 重写说明：规则链路原本会加「未识别到明确的报名截止日期」「未启用大模型」
    # 这类与文体无关的噪音，对一篇新闻稿来说既是误导也是重复。
    result["review_note"] = "判定依据：%s" % info["reason"]
    return result


def _validate_llm(raw: dict, title: str, content: str, publish: str, source_url: str = "") -> dict:
    """把模型输出规整成可信结果：枚举收敛、日期接地、置信度重算。"""
    notes: list[str] = []

    category = classifier.normalize_category(str(raw.get("category") or ""))
    rule_cat, _, _ = classifier.classify(title, content)

    deadline = raw.get("deadline")
    if deadline in ("", "null", "None"):
        deadline = None
    if deadline:
        deadline = str(deadline)[:10]
        if not date_grounded(deadline, content):
            notes.append(f"截止日期 {deadline} 未能在原文定位，已置空待复核")
            deadline = None

    start_time = raw.get("start_time")
    if start_time in ("", "null", "None"):
        start_time = None
    if start_time:
        start_time = str(start_time)[:10]
        if not date_grounded(start_time, content):
            start_time = None

    try:
        confidence = float(raw.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    evidence_raw = raw.get("evidence") if isinstance(raw.get("evidence"), dict) else {}
    evidence: dict[str, str] = {}
    for key in ("deadline", "target", "conditions", "materials", "apply_method"):
        frag = str(evidence_raw.get(key) or "").strip()
        if frag and evidence_grounded(frag, content):
            evidence[key] = frag[:120]

    # 日期被置空但模型声称高置信 -> 降权
    if deadline is None and confidence > 0.7:
        confidence = 0.6
        notes.append("模型未给出可核验的截止日期，置信度已下调")

    # 分类与规则引擎分歧较大时提示复核
    if category != rule_cat and category != "其他" and rule_cat != "其他":
        notes.append(f"模型分类「{category}」与词典分类「{rule_cat}」不一致")

    needs_review = bool(raw.get("needs_review")) or deadline is None or category == "其他" or bool(notes)

    result = {
        "category": category,
        "deadline": deadline,
        "start_time": start_time,
        "target": _clean_list(raw.get("target")),
        "conditions": _clean_list(raw.get("conditions"), 5),
        "materials": _clean_list(raw.get("materials")),
        "apply_method": str(raw.get("apply_method") or "")[:200],
        "evidence": evidence,
        "confidence": round(confidence, 2),
        "needs_review": needs_review,
        "review_note": "；".join(notes),
        "engine": "llm",
        "model_name": llm.model,
    }
    # 字面事实不取模型输出，统一由确定性抽取器从同一份原文重算
    literal = extractor.extract(title, content, publish)
    _attach_literal_fields(result, literal, content)
    result["deadline_source"] = literal.get("deadline_source", "")
    result["relative_note"] = literal.get("relative_note", "")
    # 模型没给出可核验的截止日期时，允许相对时间规则补位
    if not result.get("deadline") and literal.get("deadline_source") == "relative":
        result["deadline"] = literal.get("deadline")
        result["deadline_kind"] = literal.get("deadline_kind") or "办理截止"
        result["evidence"]["deadline"] = (literal.get("evidence") or {}).get("deadline", "")
    return _apply_genre_gate(result, genre.detect(title, content, source_url))


def _rule_result(title: str, content: str, publish: str, reason: str = "",
                 source_url: str = "") -> dict:
    """规则引擎兜底结果：字段同样带 evidence，置信度按字段完整度折算。"""
    rule_cat, cat_conf, _ = classifier.classify(title, content)
    fields = extractor.extract(title, content, publish)

    filled = sum(
        1
        for key in ("deadline", "target", "conditions", "materials", "apply_method")
        if fields.get(key)
    )
    confidence = min(0.86, 0.42 + 0.09 * filled)
    if fields.get("deadline"):
        confidence = min(0.88, confidence + 0.06 * min(fields.get("deadline_score", 0), 4))

    notes = []
    if reason:
        notes.append(reason)
    if not fields.get("deadline"):
        notes.append("未识别到明确的报名截止日期")
    if rule_cat == "其他":
        notes.append("分类置信度不足，需人工确认")

    result = {
        "category": rule_cat,
        "deadline": fields.get("deadline"),
        "start_time": fields.get("start_time"),
        "target": fields.get("target", []),
        "conditions": fields.get("conditions", []),
        "materials": fields.get("materials", []),
        "apply_method": fields.get("apply_method", ""),
        "evidence": {k: v for k, v in fields.get("evidence", {}).items() if v},
        "confidence": round(confidence, 2),
        # 规则引擎仅在关键字段缺失或置信度偏低时请求人工复核，避免全量标红
        "needs_review": (not fields.get("deadline")) or rule_cat == "其他" or confidence < 0.6,
        "review_note": "；".join(notes),
        "engine": "rule",
        "model_name": "内置规则引擎 v1",
    }
    _attach_literal_fields(result, fields, content)
    result["deadline_source"] = fields.get("deadline_source", "")
    result["relative_note"] = fields.get("relative_note", "")
    if fields.get("relative_note"):
        notes.append(fields["relative_note"])
        result["review_note"] = "；".join(notes)
    return _apply_genre_gate(result, genre.detect(title, content, source_url))


def analyze(title: str, content: str, publish_time: str = "", use_llm: bool | None = None,
            source_url: str = "") -> dict:
    """解析主入口。use_llm=None 表示按配置自动选择；source_url 仅作文体判别的弱先验。"""
    content = normalize_text(content)
    title = normalize_text(title)
    publish = (publish_time or "")[:10]

    want_llm = llm.ready if use_llm is None else (use_llm and llm.ready)
    if not want_llm:
        if llm.available and not llm.ready:
            reason = f"大模型冷却中（剩余 {llm.cooldown_remaining}s），使用规则引擎"
        elif use_llm:
            reason = "未启用大模型，使用规则引擎"
        else:
            reason = ""
        return _finalize(_rule_result(title, content, publish, reason, source_url), content, publish)

    # 同一内容命中缓存，避免重复计费；只缓存真正由模型产出的结果，
    # 否则一次网络抖动产生的兜底结果会锁住同一条通知 24 小时。
    cache_key = "analysis:" + hashlib.sha256(
        (title + "|" + content[:800] + "|" + publish + "|" + (source_url or "")).encode("utf-8")
    ).hexdigest()[:24]
    cached = cache.get_json(cache_key)
    if cached and cached.get("engine") == "llm":
        return cached

    truncated = content[:4000]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(title=title, publish_time=publish or "未提供", content=truncated)},
    ]
    try:
        raw = llm.chat_json(messages)
        result = _validate_llm(raw, title, content, publish, source_url)
    except Exception as exc:  # 模型异常一律兜底，保证业务不中断
        reason = f"大模型调用失败已降级：{str(exc)[:60]}"
        result = _rule_result(title, content, publish, reason, source_url)
        # 结构化降级原因：界面能明确告诉用户「为什么这次用规则引擎」，
        # 而不是只在复核说明里塞一句容易被忽略的文本。
        info = llm.describe()
        result["degraded"] = True
        result["fallback_reason"] = str(exc)[:200]
        result["fallback_kind"] = info.get("last_error_kind") or "unknown"

    result = _finalize(result, content, publish)
    if result.get("engine") == "llm":
        cache.set_json(cache_key, result, ttl=86400)
    return result


def analyze_batch(items: list[dict]) -> list[dict]:
    return [
        analyze(item.get("title", ""), item.get("content", ""), item.get("publish_time", ""),
                source_url=item.get("source_url", ""))
        for item in items
    ]
