"""文本处理公共工具：归一化、分句、日期识别与年份推断。"""
from __future__ import annotations

import datetime as dt
import hashlib
import re

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u00a0"), None)

# 全角数字/符号 -> 半角
# 仅统一全角数字，保留中文标点原貌（原文展示不能被改写）
_FULLWIDTH = {ord(c): ord(c) - 0xFEE0 for c in "０１２３４５６７８９"}

_SPACE_RE = re.compile(r"[ \t\u3000]+")
_MULTI_NL = re.compile(r"\n{3,}")

RE_FULL_DATE = re.compile(r"(20\d{2})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})\s*[日号]?")
RE_MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
RE_MONTH_ONLY = re.compile(r"(?<!\d)(\d{1,2})\s*月(?!\s*\d)")

SENT_SPLIT = re.compile(r"[。；;！!？?\n\r]+")


def normalize_text(text: str) -> str:
    """清洗：去零宽字符、统一全角、压缩空白、保留段落结构。"""
    if not text:
        return ""
    text = text.translate(_ZERO_WIDTH).translate(_FULLWIDTH)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _SPACE_RE.sub(" ", text)
    text = _MULTI_NL.sub("\n\n", text)
    return text.strip()


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT_SPLIT.split(text) if s and s.strip()]


def content_hash(title: str, content: str) -> str:
    """去重指纹：标题 + 正文归一化后取 SHA256，忽略空白差异。"""
    payload = re.sub(r"\s+", "", (title or "") + "|" + (content or ""))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_date(y: int, m: int, d: int) -> dt.date | None:
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None


ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# 允许 YYYY-MM-DD 或带时间的 YYYY-MM-DD HH:MM(:SS)，但拒绝任何多余尾巴，
# 否则「2026-11-13x」取前 10 位会被误当成合法日期。
ISO_DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?")


def safe_iso_date(value) -> dt.date | None:
    """全站统一的日期解析入口：格式不符或日历非法一律返回 None。

    看板、日程、匹配等接口会对数据库里的 deadline 做日期运算；若人工修正时
    写入了「2026-11-1」「2026-11-13x」这类字符串，裸调 date.fromisoformat
    会抛 ValueError 直接把整个接口变成 500。所有日期运算都必须经过这里。
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not ISO_DATETIME_RE.fullmatch(text):
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def is_iso_date(value) -> bool:
    """严格校验人工录入的日期：必须是补零且日历合法的 YYYY-MM-DD。"""
    if not value or not isinstance(value, str):
        return False
    return bool(ISO_DATE_RE.fullmatch(value.strip())) and safe_iso_date(value) is not None


def find_dates(text: str, ref_year: int) -> list[dict]:
    """识别文本中的日期，返回 [{start,end,date,raw,has_year}]。

    只用两层正则：先找带年份的完整日期，再在剩余片段里找"X月X日"，
    避免"11月"这类模糊表达被误判为日期。
    """
    found: list[dict] = []
    spans: list[tuple[int, int]] = []

    for m in RE_FULL_DATE.finditer(text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        date = _safe_date(y, mo, d)
        if date:
            found.append(
                {"start": m.start(), "end": m.end(), "date": date, "raw": m.group(0), "has_year": True}
            )
            spans.append((m.start(), m.end()))

    for m in RE_MONTH_DAY.finditer(text):
        if any(s <= m.start() < e for s, e in spans):
            continue
        mo, d = int(m.group(1)), int(m.group(2))
        date = _safe_date(ref_year, mo, d)
        if date:
            found.append(
                {"start": m.start(), "end": m.end(), "date": date, "raw": m.group(0), "has_year": False}
            )

    found.sort(key=lambda x: x["start"])
    return found


def infer_year(month: int, publish: str | None) -> int:
    """无年份日期推断：以发布时间为基准，处理"12月发布、1月截止"的跨年情形。"""
    base = None
    if publish:
        try:
            base = dt.date.fromisoformat(publish[:10])
        except ValueError:
            base = None
    if base is None:
        base = dt.date.today()
    year = base.year
    if month < base.month - 6:  # 发布时间在年末，日期落在年初 -> 次年
        year += 1
    return year


def fix_publish(publish: str | None) -> str:
    if not publish:
        return ""
    try:
        return dt.date.fromisoformat(publish[:10]).isoformat()
    except ValueError:
        return ""


HEADING_LINE_RE = re.compile(
    r"^(?:[（(]?[一二三四五六七八九十]+[）)、.．]|第[一二三四五六七八九十]+[章节条]|\d{1,2}[、.．]|【)"
)
# 纯数字编号（"1." "2、"）在通知里既可能是章节标题，也可能是条目列表。
# 真实通知的「二、选拔条件」下面就是「1.思想品德优良，身心健康；」这种条目：
# 若一律当标题，真正的章节正文会被切空，条件抽取整节丢失。
# 因此对纯数字编号收紧：只有短且不含句内标点时才算标题。
NUMERIC_HEADING_RE = re.compile(r"^\d{1,2}[、.．]")
SECTION_KEYS = ("材料", "清单", "条件", "资格", "要求", "对象", "方式", "时间",
                "说明", "安排", "流程", "事项", "程序", "标准", "范围", "待遇")


def _is_heading(line: str) -> bool:
    if not line or len(line) > 30:
        return False
    if HEADING_LINE_RE.match(line):
        if NUMERIC_HEADING_RE.match(line):
            return len(line) <= 15 and not re.search(r"[，,；;：:]", line)
        return True
    if len(line) <= 18 and not re.search(r"[。；，,]", line):
        return any(k in line for k in SECTION_KEYS)
    return False


def blocks(text: str) -> list[tuple[str, str]]:
    """把通知按"章节标题 + 正文"切成块。

    高校通知常把「四、材料要求」单独占一行，正文另起一行；若只按句号切分，
    标题与正文会被割裂，导致"报名表"这类条目失去上下文。返回 [(标题, 正文)]。
    """
    out: list[tuple[str, str]] = []
    head, buf = "", []
    for raw in (text or "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if _is_heading(line):
            if head or buf:
                out.append((head, "\n".join(buf)))
            head, buf = line, []
        else:
            buf.append(line)
    out.append((head, "\n".join(buf)))
    return out


def section_bodies(text: str, keys: tuple[str, ...]) -> list[str]:
    bodies = [body for head, body in blocks(text) if head and any(k in head for k in keys)]
    if text.strip() and not bodies:
        bodies = [text]
    return [b for b in bodies if b.strip()]


def snippet(text: str, start: int, end: int, pad: int = 18, limit: int = 90) -> str:
    """截取包含目标的原文片段，作为可追溯依据；左边界尽量对齐到句首。"""
    left = max(0, start - pad)
    # 在左侧窗口内回退到最近的句读处，避免片段从半句开始
    for sep in ("。", "；", "\n", "!", "?"):
        pos = text.rfind(sep, left, start)
        if pos != -1:
            left = pos + 1
            break
    right = min(len(text), end + pad)
    piece = text[left:right].replace("\n", " ")
    if len(piece) > limit:
        piece = piece[:limit] + "…"
    return piece.strip()
