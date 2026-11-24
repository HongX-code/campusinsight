"""相对时间解析：技术文档2 第二十一章。

高校通知偶尔写「请于本周五前提交」「三个工作日内报送」，没有绝对日期。
这类表述不能让模型凭感觉补一个日期，必须：
    原文相对表述 + 通知发布时间 -> 规则计算；
    缺少发布时间或语境不明确 -> 置空 + 待人工复核。

两条安全约束：
  1. 只在「办理截止」语境中解析（报名/提交/报送/截止…），新闻稿里的「近日」
     「本月底组织评审」一律不触发；
  2. 解析结果必须不早于发布时间，且距发布时间不超过 120 天，否则视为异常，不采信。
"""
from __future__ import annotations

import datetime as dt
import re

# 办理语境：相对词前后 24 字内必须出现办理动词或截止标签
ACTION = re.compile(r"报名|报考|申报|申请|提交|报送|投递|上传|填报|递交|受理|截止|截至|材料|回执")
# 明确排除非办理语境（评审、举办、公示、审批、新闻）
BLOCK = re.compile(r"组织评审|举行|举办|召开|公示期|审批|验收|公布|新闻网讯|本网讯|启动|开展")
REL_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"本周([一二三四五六日天])"), "week_cn"),
    (re.compile(r"下周([一二三四五六日天])"), "next_week_cn"),
    (re.compile(r"(?:本|当)月底(?:前|之前)?"), "month_end"),
    (re.compile(r"([一二三四五六七八九十\d]+)\s*个?\s*工作日(?:之)?内"), "workdays"),
    (re.compile(r"([一二三四五六七八九十\d]+)\s*日(?:之)?内"), "days"),
    (re.compile(r"一周(?:之)?内"), "one_week"),
    (re.compile(r"(?:本|当)周(?:五|末)(?:前|之前)?"), "week_friday"),
)
CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6,
          "七": 7, "八": 8, "九": 9, "十": 10}
WEEKDAY = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def _num(s: str) -> int:
    return int(s) if s.isdigit() else CN_NUM.get(s, 0)


def _resolve_date(kind: str, m: re.Match, pub: dt.date) -> dt.date | None:
    if kind in ("week_cn", "next_week_cn"):
        wd = WEEKDAY.get(m.group(1))
        if wd is None:
            return None
        monday = pub - dt.timedelta(days=pub.weekday())
        return monday + dt.timedelta(days=wd + (7 if kind == "next_week_cn" else 0))
    if kind == "month_end":
        nxt = dt.date(pub.year + (pub.month == 12), pub.month % 12 + 1, 1)
        return nxt - dt.timedelta(days=1)
    if kind == "one_week":
        return pub + dt.timedelta(days=7)
    if kind == "week_friday":
        return pub + dt.timedelta(days=4 - pub.weekday())
    n = _num(m.group(1))
    if n <= 0 or n > 60:
        return None
    if kind == "days":
        return pub + dt.timedelta(days=n)
    if kind == "workdays":
        cur, left = pub, n
        while left:
            cur += dt.timedelta(days=1)
            if cur.weekday() < 5:
                left -= 1
        return cur
    return None


def resolve_relative(text: str, publish: str) -> tuple[str | None, str, str]:
    """返回 (ISO 日期或 None, 命中的原文片段, 说明)。

    说明为空表示「没有相对时间表述」；非空表示需要向复核人员解释为何置空。
    """
    try:
        pub = dt.date.fromisoformat((publish or "")[:10])
    except (TypeError, ValueError):
        pub = None

    for pattern, kind in REL_PATTERNS:
        for m in pattern.finditer(text):
            lo = text.rfind("\n", 0, m.start()) + 1
            hi = text.find("\n", m.end())
            line = text[lo: hi if hi != -1 else len(text)]
            window = text[max(0, m.start() - 24): min(len(text), m.end() + 24)]
            if BLOCK.search(window) or not ACTION.search(window):
                continue
            frag = line.strip()[:80]
            if pub is None:
                return None, frag, "原文使用相对时间「%s」且缺少发布时间，已置空待复核" % m.group(0)
            d = _resolve_date(kind, m, pub)
            if d is None:
                return None, frag, "相对时间「%s」无法换算为具体日期，已置空待复核" % m.group(0)
            if d < pub or (d - pub).days > 120:
                return None, frag, "相对时间换算结果 %s 超出合理区间，已置空待复核" % d.isoformat()
            return d.isoformat(), frag, ""
    return None, "", ""
