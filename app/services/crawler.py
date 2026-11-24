"""单篇网页采集：用户粘贴一个公开链接，抽取出标题与正文。

与 `collector.py` 的关系：
    本模块只负责"一篇"的抓取入口，请求与解析全部复用 collector，
    保证「粘贴链接采集」与「站点批量采集」产出的字段口径完全一致；
    两套解析器各自演进的后果是同一个页面在两条链路里得到不同正文，
    之后所有基于 content_hash 的去重都会失效。

安全与合规约束：
1. 仅允许 http/https，且域名必须命中白名单（默认 .edu.cn / .ac.cn）；
2. 禁止访问内网地址与 localhost，避免 SSRF；
3. 限制响应体积与超时，遇到反爬或异常直接返回失败原因，由用户改用手动录入。
"""
from __future__ import annotations

from urllib.parse import urlsplit

from app.services.collector import CrawlError, check_url, fetch_page, parse_detail

__all__ = ["CrawlError", "fetch_notice"]


def fetch_notice(url: str) -> dict:
    """抓取并解析单个网页，返回 {title, content, publish_time, source_url, source_name}。"""
    url = check_url(url)
    html, final_url = fetch_page(url)
    detail = parse_detail(html, final_url, None)

    content = (detail.get("content") or "").strip()
    if detail.get("content_type") == "empty" or len(content) < 60:
        raise CrawlError("未能从页面提取到有效正文，建议手动粘贴内容")

    host = urlsplit(final_url).hostname or ""
    return {
        "title": (detail.get("title") or host)[:200],
        "content": content,
        "publish_time": detail.get("publish_time") or "",
        "source_url": detail.get("source_url") or final_url,
        "source_name": host,
        "attachments": detail.get("attachments") or [],
    }
