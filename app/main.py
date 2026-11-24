"""应用入口：装配路由、静态资源与启动初始化。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database.connection import healthcheck, init_db
from app.routers import ai, dashboard, notices, sources, users

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("zhixun")

BASE = Path(__file__).resolve().parent
STATIC_DIR = BASE / "static"
TEMPLATE_DIR = BASE / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("%s v%s 启动完成 | 数据库=%s | 解析引擎=%s",
                settings.APP_NAME, settings.VERSION, settings.db_kind,
                "大模型" if settings.llm_enabled else "规则引擎")
    logger.info("浏览器请打开 %s （0.0.0.0 是监听地址，不能作为网址访问）", settings.access_url)
    yield


app = FastAPI(
    title=f"{settings.APP_NAME} API",
    description=settings.APP_SUBTITLE,
    version=settings.VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(notices.router)
app.include_router(sources.router)
app.include_router(users.router)
app.include_router(ai.router)
app.include_router(dashboard.router)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(TEMPLATE_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """浏览器自动请求的站点图标；复用 PWA 图标，避免控制台出现 404 噪音。"""
    return FileResponse(STATIC_DIR / "icons" / "icon-192.png", media_type="image/png")


@app.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest():
    """PWA 清单：Android / Windows / HarmonyOS 浏览器据此识别为可安装应用。"""
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    """Service Worker 必须由根路径下发，作用域才能覆盖整个站点。"""
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/api/health")
def health():
    db = healthcheck()
    return {
        "status": "ok" if db["ok"] else "degraded",
        "database": db,
        "llm_available": settings.llm_enabled,
        "version": settings.VERSION,
        "access": {"listen": settings.APP_HOST, "url": settings.access_url},
        "app": settings.APP_NAME,
        "platforms": {
            "web": True,
            "pwa": True,
            "manifest": "/manifest.webmanifest",
            "service_worker": "/sw.js",
            "clients": ["Windows", "Android", "HarmonyOS", "iOS", "macOS", "Linux"],
        },
    }


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("未处理异常: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务内部错误，请查看服务端日志", "error": str(exc)[:200]},
    )
