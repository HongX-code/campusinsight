"""AI 能力接口：解析预览、智能问答、引擎状态与连通性探测。"""
from __future__ import annotations

import socket

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.database.connection import get_db
from app.database.models import Notice
from app.serializers import AskIn, PreviewIn, analysis_to_dict
from app.services import analyzer, qa
from app.services.cache import cache
from app.services.llm_client import llm

router = APIRouter(prefix="/api", tags=["AI"])


@router.get("/ai/status")
def ai_status():
    """引擎状态：前端据此展示「当前用哪个引擎、为什么降级」。"""
    return {
        "llm": llm.describe(),
        "cache": cache.stats(),
        "active_engine": "llm" if llm.ready else "rule",
        "engine_label": f"大模型（{llm.model}）" if llm.ready else "内置规则引擎（离线兜底）",
        "capabilities": [
            {"key": "parse", "name": "通知结构化解析", "engine": "llm+rule", "offline": True},
            {"key": "qa", "name": "基于原文的智能问答", "engine": "llm+rule", "offline": True},
            {"key": "match", "name": "条件匹配与理由解释", "engine": "rule", "offline": True},
            {"key": "audit", "name": "规则校验与人工复核", "engine": "rule", "offline": True},
        ],
    }


@router.post("/ai/health")
def ai_health():
    """主动探测大模型连通性；未配置密钥时也返回 200，并说明规则引擎可用。"""
    result = llm.probe()
    result["active_engine"] = "llm" if result.get("ok") else "rule"
    return result


@router.post("/ai/preview")
def preview(payload: PreviewIn):
    """不写入数据库的即时解析，用于演示"粘贴一段通知立刻看结果"。"""
    result = analyzer.analyze(
        payload.title or "（未命名通知）", payload.content, payload.publish_time,
        use_llm=payload.use_llm, source_url=payload.source_url,
    )
    return result


def _lan_addresses() -> list[str]:
    """列出可用于手机 / 平板访问的局域网 IPv4（纯标准库，不发起外部请求）。"""
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET, socket.SOCK_STREAM):
            ip = info[4][0]
            if not ip.startswith("127."):
                found.add(ip)
    except OSError:
        pass
    if not found:
        # UDP connect 只做路由选择、不发包，离线环境也能拿到默认出口网卡地址
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("223.5.5.5", 80))
                ip = sock.getsockname()[0]
                if not ip.startswith("127."):
                    found.add(ip)
        except OSError:
            pass
    return sorted(found)


@router.post("/qa/ask")
def ask(payload: AskIn, db: Session = Depends(get_db)):
    """问答语料 = 通知原文 + 已经人工/规则复核过的结构化字段。

    只把 title/content 交给检索是早期的一个静默缺陷：系统明明抽出了截止日期、
    材料清单和申请条件，问答却还要从原文里碰运气捞句子，导致示例问题答成标题清单。
    """
    rows = db.scalars(select(Notice).options(selectinload(Notice.analysis))).all()
    corpus = []
    for n in rows:
        a = analysis_to_dict(n.analysis) or {}
        corpus.append({
            "id": n.id,
            "title": n.title,
            "content": n.content,
            "category": n.category,
            "source_url": n.source_url,
            "source_name": n.source_name,
            "publish_time": n.publish_time,
            "genre": a.get("genre", ""),
            "actionable": a.get("actionable", True),
            "deadline": a.get("deadline"),
            "deadline_kind": a.get("deadline_kind", ""),
            "registration_start": a.get("registration_start"),
            "start_time": a.get("start_time"),
            "event_time": a.get("event_time"),
            "target": a.get("target", []),
            "conditions": a.get("conditions", []),
            "materials": a.get("materials", []),
            "grade_requirement": a.get("grade_requirement", []),
            "major_requirement": a.get("major_requirement", []),
            "apply_method": a.get("apply_method", ""),
            "location": a.get("location", ""),
            "contact": a.get("contact", {}),
            "evidence": a.get("evidence", {}),
        })
    result = qa.answer(payload.query, corpus, top_k=payload.top_k)
    result["corpus_size"] = len(corpus)
    result["structured_corpus"] = sum(
        1 for x in corpus if x["materials"] or x["conditions"] or x["deadline"]
    )
    return result


@router.get("/system/info")
def system_info():
    return {
        "app": settings.APP_NAME,
        "subtitle": settings.APP_SUBTITLE,
        "version": settings.VERSION,
        "database": settings.db_kind,
        "cache": cache.kind,
        "llm_provider": settings.LLM_PROVIDER,
        "llm_model": settings.LLM_MODEL,
        "llm_available": llm.available,
        "llm_ready": llm.ready,
        "crawl_domains": settings.CRAWL_ALLOWED_DOMAINS,
        "network": {
            "listen_host": settings.APP_HOST,
            "port": settings.APP_PORT,
            "local_url": settings.access_url,
            "lan_addresses": _lan_addresses(),
            "lan_urls": [f"http://{ip}:{settings.APP_PORT}/" for ip in _lan_addresses()],
            "lan_exposed": settings.APP_HOST in ("0.0.0.0", "::", "[::]", ""),
            "pwa": {
                "manifest": "/manifest.webmanifest",
                "service_worker": "/sw.js",
                "requires_secure_context": True,
                "note": "离线壳与「添加到桌面」仅在 HTTPS 或 localhost 下可用；"
                        "局域网 http 访问时页面与全部接口照常工作。",
            },
            "clients": ["Windows", "Android", "HarmonyOS", "iOS", "macOS", "Linux"],
        },
    }