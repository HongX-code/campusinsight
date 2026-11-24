"""建表脚本：python scripts/init_db.py"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.database.connection import healthcheck, init_db  # noqa: E402

if __name__ == "__main__":
    init_db()
    print("数据库：", settings.db_kind)
    print("连接状态：", healthcheck())
