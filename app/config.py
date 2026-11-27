"""全局配置：统一从环境变量 / .env 读取，便于本地 SQLite 与华为云 RDS 之间切换。"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_env(path: Path) -> None:
    """轻量 .env 解析，避免为读取一个配置文件引入额外依赖。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and not os.getenv(key):
            os.environ[key] = value


_load_env(BASE_DIR / ".env")


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings:
    APP_NAME = "高校智讯"
    APP_SUBTITLE = "基于华为云的高校通知智能解析与个性化服务平台"
    VERSION = "1.0.0"

    # 数据库：默认 SQLite；配置 DATABASE_URL 即切换到华为云 RDS for MySQL
    DATABASE_URL: str = os.getenv("DATABASE_URL") or f"sqlite:///{(DATA_DIR / 'gaoxiao_zhixun.db').as_posix()}"
    DB_ECHO: bool = os.getenv("DEBUG", "false").lower() == "true"

    # 大模型：OpenAI 兼容协议，适配华为云 MaaS
    LLM_PROVIDER: str = (os.getenv("LLM_PROVIDER") or "none").lower()
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL") or "https://api.modelarts-maas.com/v1"
    LLM_API_KEY: str = os.getenv("LLM_API_KEY") or ""
    LLM_MODEL: str = os.getenv("LLM_MODEL") or "DeepSeek-V3"
    LLM_TIMEOUT: float = float(os.getenv("LLM_TIMEOUT") or 45)

    # 缓存
    REDIS_URL: str = os.getenv("REDIS_URL") or ""

    # 采集
    CRAWL_ALLOWED_DOMAINS: list[str] = _split(os.getenv("CRAWL_ALLOWED_DOMAINS") or "edu.cn,ac.cn")
    CRAWL_TIMEOUT: float = 12.0
    CRAWL_MAX_BYTES: int = 3 * 1024 * 1024

    # 0.0.0.0 是"监听所有网卡"的通配地址，不是浏览器地址；本地默认只监听回环，
    # 云端 ECS / 容器需要对外暴露时再显式设置 APP_HOST=0.0.0.0。
    APP_HOST: str = os.getenv("APP_HOST") or "127.0.0.1"
    APP_PORT: int = int(os.getenv("APP_PORT") or 8000)

    # ---------------- 微信小程序（第二终端） ----------------
    # 未配置 AppID/Secret 时，小程序端走「游客演示模式」：登录接口返回
    # 一个稳定的演示用户，所有功能照常可用，便于无小程序账号时答辩演示。
    WECHAT_APPID: str = os.getenv("WECHAT_APPID") or ""
    WECHAT_SECRET: str = os.getenv("WECHAT_SECRET") or ""
    # 订阅消息模板 ID（微信公众平台 -> 订阅消息 里申请的一次性订阅）
    WECHAT_TEMPLATE_DEADLINE: str = os.getenv("WECHAT_TEMPLATE_DEADLINE") or ""
    # 小程序版本环境：developer 开发版 / trial 体验版 / formal 正式版。
    # 推错这个值微信会直接不下发，且报错信息很不直观，所以要能显式配置。
    WECHAT_MP_STATE: str = (os.getenv("WECHAT_MP_STATE") or "developer").lower()
    # 小程序登录态有效期（天），与微信 session 无关，仅用于本系统签发的本地票据
    WECHAT_TOKEN_TTL_DAYS: int = int(os.getenv("WECHAT_TOKEN_TTL_DAYS") or 30)
    # 推送是否启用：未配置模板 ID 时自动降级为「站内提醒」，不报错
    WECHAT_PUSH_ENABLED: bool = os.getenv("WECHAT_PUSH_ENABLED", "true").lower() != "false"

    # 日历订阅（ICS）里的站点标识，用于 UID 生成
    CALENDAR_UID_DOMAIN: str = os.getenv("CALENDAR_UID_DOMAIN") or "campusinsight.local"

    @property
    def access_url(self) -> str:
        """可直接粘贴进浏览器的首页地址；通配监听地址一律折算为 127.0.0.1。"""
        host = self.APP_HOST
        if host in ("0.0.0.0", "::", "[::]", ""):
            host = "127.0.0.1"
        elif ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{self.APP_PORT}/"

    @property
    def llm_enabled(self) -> bool:
        return self.LLM_PROVIDER not in ("", "none", "rule") and bool(self.LLM_API_KEY)

    @property
    def db_kind(self) -> str:
        if self.DATABASE_URL.startswith("sqlite"):
            return "SQLite（本地）"
        if "mysql" in self.DATABASE_URL:
            return "MySQL / 华为云 RDS"
        return "外部数据库"

    @property
    def is_mysql(self) -> bool:
        """是否外接 MySQL（华为云 RDS for MySQL 与自建 MySQL 走同一协议）。

        健康检查需要区分「已配置」与「真连通」，而中文 label 只适合给人看，
        给程序判断要用布尔量，避免把 "MySQL / 华为云 RDS" 这种展示文案写进条件里。
        """
        return "mysql" in self.DATABASE_URL.lower()

    @property
    def is_redis(self) -> bool:
        """是否配置了 Redis（华为云 DCS for Redis 与自建 Redis 走同一协议）。"""
        return bool(self.REDIS_URL.strip())

    @property
    def is_maas(self) -> bool:
        """是否走华为云 ModelArts Studio（MaaS）的 OpenAI 兼容接口。"""
        return self.llm_enabled and (
            self.LLM_PROVIDER == "maas" or "modelarts-maas" in self.LLM_BASE_URL
        )

    @property
    def wechat_enabled(self) -> bool:
        """是否具备真实调用微信接口的能力（能换 openid）。"""
        return bool(self.WECHAT_APPID and self.WECHAT_SECRET)

    @property
    def wechat_push_ready(self) -> bool:
        """订阅消息可用的充要条件：能登录 + 配了模板 + 未主动关闭。"""
        return self.wechat_enabled and bool(self.WECHAT_TEMPLATE_DEADLINE) and self.WECHAT_PUSH_ENABLED


settings = Settings()

# 通知分类体系（与前端筛选、匹配逻辑共用一份枚举）
CATEGORIES = [
    "学科竞赛",
    "奖助学金",
    "学术讲座",
    "科研项目",
    "志愿服务",
    "社会实践",
    "校园活动",
    "实习就业",
    "其他",
]
