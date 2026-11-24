"""采集源接口：站点列表 / 抓取预览 / 一键入库。

产品上对应"采集中心"：运营者不必逐条粘贴链接，而是选定栏目 →
系统翻页抓取 → 前端展示预览（标题、日期、正文长度、是否重复）→
人工勾选后入库，避免把栏目里的无关内容（会议通知、人事公示）也灌进来。

去重两道闸：
    1. 内容指纹 content_hash（标题+正文归一化后 SHA256），挡住正文相同、
       URL 不同的转载；
    2. source_url 精确匹配，挡住正文被站点改动过、指纹已变的重复抓取。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import Notice
from app.serializers import CrawlImportIn, CrawlPreviewIn
from app.services import classifier, collector
from app.services.cleaner import clean_text
from app.services.textutil import content_hash, fix_publish, normalize_text

router = APIRouter(prefix="/api/sources", tags=["采集"])


@router.get("")
def list_sources():
    """列出全部采集源（含停用项），前端据此渲染采集中心。"""
    conf = collector.load_config()
    all_sources = collector.load_sources(only_enabled=False)
    return {
        "sources": [
            {**s.to_dict(),
             "list_selector": s.list_selector,
             "detail_selectors": {k: v for k, v in s.detail.items()}}
            for s in all_sources
        ],
        "settings": conf.get("settings", {}),
        "note": conf.get("note", ""),
    }


@router.post("/preview")
def preview(payload: CrawlPreviewIn, db: Session = Depends(get_db)):
    """抓取但不落库。每条结果标注 duplicate，便于前端默认不勾选已存在的条目。"""
    try:
        sources = collector.load_sources()
    except collector.CrawlError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not sources:
        raise HTTPException(status_code=400, detail="没有启用的采集源，请检查 config/sources.json")

    # 增量抓取：先从库里读出已收录的标题与链接，让采集阶段就跳过它们，
    # 把每源条数配额留给真正的新内容（否则库满后翻第一页全是"已收录"）。
    known_titles: set[str] = set()
    known_urls: set[str] = set()
    if payload.incremental:
        known_titles = {t for (t,) in db.execute(select(Notice.title)).all() if t}
        known_urls = {u for (u,) in db.execute(select(Notice.source_url)).all() if u}

    result = collector.crawl_all(
        source_ids=payload.source_ids or None,
        limit=payload.limit,
        max_pages=payload.max_pages,
        fetch_detail=payload.fetch_detail,
        known_titles=known_titles,
        known_urls=known_urls,
        stop_after_known=payload.stop_after_known if payload.incremental else 0,
    )

    hashes = {h for (h,) in db.execute(select(Notice.content_hash)).all() if h}
    urls = known_urls or {u for (u,) in db.execute(select(Notice.source_url)).all() if u}

    for block in result["sources"]:
        for item in block["items"]:
            fingerprint = content_hash(item.get("title", ""), item.get("content", ""))
            item["duplicate"] = bool(
                fingerprint in hashes or (item.get("url") and item["url"] in urls)
            )
            item["preview"] = (item.get("content") or "").replace("\n", " ")[:120]
    return result


@router.post("/import")
def import_items(payload: CrawlImportIn, auto_analyze: bool | None = None, db: Session = Depends(get_db)):
    """入库。items 为空时按 source_ids 现场抓取后直接入库（适合定时任务）。"""
    from app.routers.notices import run_analysis      # 延迟导入，避免路由模块互相依赖

    todo: list[dict] = [item.model_dump() for item in payload.items]

    if not todo:
        if not payload.source_ids:
            raise HTTPException(status_code=400, detail="请至少选择一条待入库通知或一个采集源")
        result = collector.crawl_all(
            source_ids=payload.source_ids, limit=payload.limit, max_pages=1, fetch_detail=True
        )
        for block in result["sources"]:
            todo.extend({**item, "unit": item.get("unit") or block["source"]["name"]}
                        for item in block["items"])

    want_analyze = payload.auto_analyze if auto_analyze is None else auto_analyze
    created, skipped, failed, analyzed = 0, 0, [], 0

    for raw in todo:
        title = normalize_text(raw.get("title", ""))[:200]
        content = clean_text(raw.get("content", ""))
        url = (raw.get("url") or "").strip()

        # 预览时未抓详情（只抓了列表）→ 入库前补齐正文
        if len(content) < 40 and url:
            try:
                detail_html, detail_url = collector.fetch_page(url)
                detail = collector.parse_detail(detail_html, detail_url)
                content = detail["content"] or content
                raw.setdefault("attachments", detail["attachments"])
            except collector.CrawlError as exc:
                failed.append({"title": title, "error": f"正文补抓失败：{exc}"})
                continue

        if len(content) < 10:
            failed.append({"title": title, "error": "正文过短，已跳过"})
            continue

        fingerprint = content_hash(title, content)
        exists = db.scalar(
            select(Notice).where(Notice.content_hash == fingerprint)
        ) or (url and db.scalar(select(Notice).where(Notice.source_url == url)))
        if exists:
            skipped += 1
            continue

        notice = Notice(
            title=title,
            content=content,
            category=classifier.classify(title, content)[0],
            source_url=url,
            source_name=(raw.get("unit") or "").strip() or "站点采集",
            publish_time=fix_publish(raw.get("publish_date") or ""),
            content_hash=fingerprint,
            ingest_mode="crawl",
            attachments=json.dumps(raw.get("attachments") or [], ensure_ascii=False),
        )
        db.add(notice)
        db.commit()
        db.refresh(notice)
        created += 1

        if want_analyze:
            try:
                run_analysis(db, notice)
                analyzed += 1
            except Exception as exc:                  # 单条解析失败不影响整体入库
                failed.append({"title": title, "error": f"解析失败：{str(exc)[:80]}"})

    return {"created": created, "skipped": skipped, "analyzed": analyzed,
            "failed": failed, "total": len(todo)}
