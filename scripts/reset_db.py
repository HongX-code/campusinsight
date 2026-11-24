"""重建演示数据库：清空所有业务表后重新导入样例数据。

用于改动解析逻辑后刷新演示数据，避免手工删除 SQLite 文件。
    python scripts/reset_db.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.database.connection import Base, engine, init_db  # noqa: E402
from app.database import models  # noqa: E402,F401  确保模型已注册到 Base


def main() -> None:
    Base.metadata.drop_all(bind=engine)
    print("已清空业务表")
    init_db()
    print("已重建表结构")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "import_samples.py")], check=True)


if __name__ == "__main__":
    main()
