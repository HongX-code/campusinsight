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
