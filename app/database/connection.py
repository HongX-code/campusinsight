"""数据库连接层：同一份代码在 SQLite 与华为云 RDS for MySQL 之间无缝切换。"""
from __future__ import annotations

import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.database.models import Base

logger = logging.getLogger("zhixun.db")

_connect_args: dict = {}
_engine_kwargs: dict = {"pool_pre_ping": True, "echo": settings.DB_ECHO}

if settings.DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}
else:
    # RDS for MySQL：回收空闲连接，避免云端长连接被服务端断开；
    # 显式设置连接/读写超时，网络抖动时快速失败而不是把请求线程挂死。
    _connect_args = {"connect_timeout": 5, "read_timeout": 30, "write_timeout": 30}
    _engine_kwargs.update({"pool_recycle": 1800, "pool_size": 5, "max_overflow": 10})

engine = create_engine(settings.DATABASE_URL, connect_args=_connect_args, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, class_=Session)


def get_db():
    """FastAPI 依赖注入：每个请求一个会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_columns() -> None:
    """轻量迁移：create_all 只建新表，不会给已存在的表补列。

    采集功能给 notices 增加了 attachments（附件清单）、文体闸门给
    notice_analysis 增加了 genre / actionable / deadline_kind，本地已有
    SQLite 库和云端 RDS 都需要补列，否则 ORM 一查询就报 no such column。
    这里只做「缺什么补什么」的加列，不做任何改名/删列，避免误伤历史数据。
    """
    wanted = {
        "notices": {"attachments": "TEXT"},
        "notice_analysis": {
            "genre": "VARCHAR(16) DEFAULT 'notice'",
            "actionable": "BOOLEAN DEFAULT 1",
            "deadline_kind": "VARCHAR(16) DEFAULT ''",
            # 结构化字段契约（设计文档 8.2）与规则校验报告
            "grade_requirement": "TEXT",
            "major_requirement": "TEXT",
            "registration_start": "VARCHAR(32)",
            "event_time": "VARCHAR(32)",
            "location": "VARCHAR(200) DEFAULT ''",
            "contact": "TEXT",
            "deadline_source": "VARCHAR(16) DEFAULT ''",
            "checks": "TEXT",
        },
    }
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in wanted.items():
            if not inspector.has_table(table):
                continue
            existing = {col["name"] for col in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name in existing:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                logger.info("已补充字段 %s.%s", table, name)


def _ensure_indexes() -> None:
    """为高频查询字段补索引（幂等）。

    ORM 的 index=True 只对新建表生效；已存在的库（本地历史库 / 云端 RDS）
    不会自动补索引。看板按 ingest_mode 分组、通知列表按 created_at 排序，
    这两个字段在老库里没有索引，数据量大时会全表扫描。这里按数据库类型
    分别检查并补建，已存在则跳过，绝不改动业务数据。
    """
    wanted = {
        "notices": [
            ("ix_notices_ingest_mode", "ingest_mode"),
            ("ix_notices_created_at", "created_at"),
        ],
        "notice_analysis": [
            ("ix_analysis_needs_review", "needs_review"),
            ("ix_analysis_reviewed", "reviewed"),
            ("ix_analysis_engine", "engine"),
        ],
    }
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, indexes in wanted.items():
            if not inspector.has_table(table):
                continue
            existing = {ix["name"] for ix in inspector.get_indexes(table)}
            for name, column in indexes:
                if name in existing:
                    continue
                try:
                    conn.execute(text(f"CREATE INDEX {name} ON {table} ({column})"))
                    logger.info("已补充索引 %s", name)
                except Exception as exc:  # 权限不足等：记录但不阻断启动
                    logger.warning("补索引 %s 未成功：%s", name, exc)


def init_db() -> None:
    """建表（幂等）。生产环境亦可直接执行 database/init.sql。"""
    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    _ensure_indexes()
    logger.info("数据库已就绪：%s", settings.db_kind)


def healthcheck() -> dict:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ok": True, "kind": settings.db_kind}
    except Exception as exc:  # pragma: no cover - 仅在连接异常时触发
        return {"ok": False, "kind": settings.db_kind, "error": str(exc)}
