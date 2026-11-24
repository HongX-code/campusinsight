"""云端数据库初始化：RDS 实例创建后，先建库、再建表，按需导入演示数据。

    python scripts/cloud_bootstrap.py --create-db
    python scripts/cloud_bootstrap.py --create-db --with-samples

只在 DATABASE_URL 指向 MySQL 时执行建库；SQLite 本地环境会直接跳过建库步骤。
脚本不会打印密码，也不修改 DATABASE_URL 本身。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy.engine import make_url  # noqa: E402


def _mysql_target():
    raw = os.getenv("DATABASE_URL", "")
    if not raw.startswith("mysql"):
        return None
    url = make_url(raw)
    db = url.database or "gaoxiao_zhixun"
    if not re.fullmatch(r"[A-Za-z0-9_]+", db):
        raise SystemExit(f"库名不合法：{db!r}（只允许字母、数字、下划线）")
    return url, db


def create_database() -> None:
    target = _mysql_target()
    if target is None:
        print("[SKIP] DATABASE_URL 不是 MySQL，跳过建库（本地 SQLite 无需建库）")
        return
    url, db = target
    import pymysql

    conn = pymysql.connect(
        host=url.host or "127.0.0.1",
        port=url.port or 3306,
        user=url.username or "root",
        password=url.password or "",
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{db}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
        print(f"[OK] 数据库就绪：{db}（utf8mb4_unicode_ci）")
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="云端 MySQL 建库 + 建表 + 可选导入样本")
    ap.add_argument("--create-db", action="store_true", help="库不存在时创建 utf8mb4 库")
    ap.add_argument("--with-samples", action="store_true", help="建表后导入演示数据并解析")
    args = ap.parse_args()

    if args.create_db:
        create_database()

    from app.database.connection import init_db

    init_db()
    print("[OK] 表结构已就绪")

    if args.with_samples:
        subprocess.run([sys.executable, "scripts/import_samples.py"], cwd=ROOT, check=True)
        print("[OK] 演示数据已导入")

    print("下一步：启动服务并访问 /api/health 检查 database.kind 是否为 RDS。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())