"""内容清洗：网页正文抽取、导航噪声剔除、段落重组。"""
from __future__ import annotations

import re

from app.services.textutil import normalize_text

# 常见导航 / 版权 / 交互噪声
NOISE_PATTERNS = [
    r"^(首页|上一页|下一页|返回|打印|关闭|分享到|字体[:：]|收藏|更多)$",
    r"^(版权所有|Copyright|技术支持|地址[:：]|邮编[:：]|电话[:：])",
    r"^(浏览次数|点击量|阅读量|责任编辑)[:：]?\s*\d*",
    r"^(上一篇|下一篇)[:：]",
    r"^\s*[<>\-—_=·]{3,}\s*$",
]
_NOISE_RE = [re.compile(p) for p in NOISE_PATTERNS]

# 章节标题：一、 / （二） / 3. / 第四条
HEADING_RE = re.compile(r"^[（(]?[一二三四五六七八九十]+[、.）)]|^\d+[、.）)]|^第[一二三四五六七八九十]+[章节条款]")


def clean_text(raw: str) -> str:
    """逐行清洗：丢弃导航行、超短行与重复空行。"""
    text = normalize_text(raw)
    kept: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        if len(line) <= 2 and not re.search(r"[\u4e00-\u9fa5]", line):
            continue
        if any(rx.search(line) for rx in _NOISE_RE):
            continue
        kept.append(line)
    # 合并"正文被拆成多行"的情况：短行且不是章节标题时与下一行拼接
    merged: list[str] = []
    for line in kept:
        prev = merged[-1] if merged else ""
        if (
            prev
            and len(prev) < 12
            and not prev.endswith(("。", "：", ":", "！", "？"))
            and not HEADING_RE.match(prev)
        ):
            merged[-1] = prev + line
        else:
            merged.append(line)
    out = "\n".join(merged).strip()
    return re.sub(r"\n{3,}", "\n\n", out)


def split_title_body(article_text: str, fallback_title: str = "") -> tuple[str, str]:
    """网页正文首行通常就是标题，其余为正文。"""
    lines = [ln for ln in article_text.split("\n") if ln.strip()]
    if not lines:
        return fallback_title, ""
    if fallback_title and len(lines) > 1:
        return fallback_title.strip(), "\n".join(lines[1:]).strip()
    first = lines[0].strip()
    if len(first) <= 60 and len(lines) > 1:
        return first, "\n".join(lines[1:]).strip()
    return fallback_title or first[:40], article_text
