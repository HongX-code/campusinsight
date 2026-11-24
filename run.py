"""高校智讯 · 本地启动器（启动前先体检，避免"命令跑起来了但页面打不开"）

    python run.py                    # 体检通过后启动，打印可直接点击的地址
    python run.py --check            # 只体检不启动（排错 / 交付前核对用）
    python run.py --open             # 启动后自动打开浏览器
    python run.py --host 0.0.0.0     # 云端 ECS / 容器对外暴露时显式覆盖
    python run.py --lan              # 局域网模式：自动监听所有网卡并打印手机可访问地址
    python run.py --port 8001        # 8000 被占用时临时换端口

为什么要默认 127.0.0.1
-----------------------
`0.0.0.0` 是"监听本机所有网卡"的通配地址，不是浏览器能打开的网址。
如果把它写进 .env，uvicorn 会打印 `Uvicorn running on http://0.0.0.0:8000`，
把这条地址复制进浏览器就会得到「无法访问此页面」。
本地开发只监听回环地址即可；云端部署需要被外部访问时，再显式设置 APP_HOST=0.0.0.0，
浏览器仍然访问服务器公网 IP 或域名，而不是 0.0.0.0。
"""
from __future__ import annotations

import argparse
import importlib.util
import socket
import sqlite3
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "pydantic": "pydantic",
    "sqlalchemy": "sqlalchemy",
    "bs4": "beautifulsoup4",
    "lxml": "lxml",
    "requests": "requests",
    "httpx": "httpx",
}
PAGES = (
    "app/templates/index.html",
    "app/static/js/app.js",
    "app/static/css/style.css",
    "app/static/manifest.webmanifest",
    "app/static/sw.js",
    "app/static/icons/icon-192.png",
)
WILDCARD_HOSTS = ("0.0.0.0", "::", "[::]", "")


class Report:
    def __init__(self) -> None:
        self.ok = True

    def add(self, ok: bool, item: str, detail: str = "") -> None:
        self.ok = self.ok and ok
        print(f"  [{'OK  ' if ok else 'FAIL'}] {item:<20}{detail}")

    def warn(self, item: str, detail: str = "") -> None:
        print(f"  [WARN] {item:<20}{detail}")


def has_module(name: str) -> bool:
    """find_spec 在部分环境会抛 ModuleNotFoundError，这里统一吞掉并按"未安装"处理。"""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def lan_addresses() -> list[str]:
    """探测本机局域网 IPv4，供手机 / 平板 / 鸿蒙设备访问（纯标准库，不联网）。"""
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
        # UDP connect 只做路由选择、不发包，离线环境也能拿到出口网卡地址
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("223.5.5.5", 80))
                ip = sock.getsockname()[0]
                if not ip.startswith("127."):
                    found.add(ip)
        except OSError:
            pass
    return sorted(found)


def browser_host(host: str) -> str:
    """把通配监听地址折算成浏览器真正能连的地址。"""
    if host in WILDCARD_HOSTS:
        return "127.0.0.1"
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def service_already_up(base_url: str) -> bool:
    """端口被占用时确认是不是「本服务」在跑：只看 200 会把别的应用误判成自己。"""
    import json

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(base_url + "api/health", timeout=1) as resp:
            if resp.status != 200:
                return False
            payload = json.loads(resp.read(2048).decode("utf-8", "replace"))
        return payload.get("app") == "高校智讯" and payload.get("status") == "ok"
    except Exception:
        return False


def port_free(host: str, port: int) -> bool:
    probe = "127.0.0.1" if host in WILDCARD_HOSTS else host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((probe, port))
        return True
    except OSError:
        return False


def inspect(host: str, port: int, settings, lan: bool = False) -> bool:
    """逐项体检；只有真正会导致"页面打不开"的问题才判 FAIL。"""
    r = Report()
    base_url = f"http://{browser_host(host)}:{port}/"
    print("=== 高校智讯 · 启动前体检 ===")
    print(f"  工作目录：{ROOT}")
    print(f"  Python  ：{sys.version.split()[0]}  ({sys.executable})")

    r.add(sys.version_info >= (3, 10), "Python 版本", "需 3.10+")

    missing = [pkg for mod, pkg in REQUIRED.items() if not has_module(mod)]
    r.add(not missing, "运行依赖",
          f"{len(REQUIRED) - len(missing)}/{len(REQUIRED)} 就绪" if not missing
          else "缺少 " + ", ".join(missing) + " → 执行 pip install -r requirements.txt")

    up = service_already_up(base_url)
    if up:
        r.add(True, "服务端口", f"{port} 已有本服务在运行，直接打开 {base_url}")
    else:
        free = port_free(host, port)
        if free:
            r.add(True, "服务端口", f"{port} 空闲")
        else:
            # 结束占用进程的命令按平台给出：Windows 用 netstat/findstr，Linux/macOS 用 ss/lsof。
            if sys.platform == "win32":
                kill_hint = f"结束占用进程（netstat -ano | findstr :{port}）"
            else:
                kill_hint = f"结束占用进程（ss -ltnp | grep :{port} 或 lsof -i :{port}）"
            r.add(False, "服务端口",
                  f"{port} 被占用 → 换端口 APP_PORT={port + 1}，或{kill_hint}")

    if settings.db_kind.startswith("SQLite"):
        db_path = ROOT / "data" / "gaoxiao_zhixun.db"
        if not db_path.exists():
            r.warn("本地数据库", "尚无数据文件，启动会自动建表；建议先跑 python scripts/import_samples.py")
        else:
            try:
                with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as conn:
                    tables = {row[0] for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")}
                    count = conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0] if "notices" in tables else 0
                r.add("notices" in tables, "本地数据库",
                      f"{db_path.name} · {count} 条通知" if count else
                      "表已建但无数据 → 执行 python scripts/import_samples.py")
            except sqlite3.Error as exc:
                r.warn("本地数据库", f"只读探测失败：{exc}（不阻断启动）")
    else:
        r.warn("数据库", f"{settings.db_kind} · 连通性由应用启动时校验")

    absent = [p for p in PAGES if not (ROOT / p).exists()]
    r.add(not absent, "页面与多端资源",
          f"{len(PAGES)}/{len(PAGES)} 齐全" if not absent else "缺失 " + ", ".join(absent))

    env_file = ROOT / ".env"
    r.warn("环境配置", "未找到 .env，使用内置默认值（SQLite + 规则引擎）" if not env_file.exists()
           else f"已加载 .env · APP_HOST={settings.APP_HOST} · APP_PORT={settings.APP_PORT}")

    if host in WILDCARD_HOSTS:
        r.warn("监听地址", "0.0.0.0 仅供云端/局域网暴露，浏览器请访问 " + base_url)

    if lan:
        urls = [f"http://{ip}:{port}/" for ip in lan_addresses()]
        if urls:
            print("\n  手机 / 平板 / 鸿蒙（同一 WiFi）：")
            for url in urls:
                print("    " + url)
            print("    局域网 http 不启用离线壳与「添加到桌面」；页面与全部接口照常可用。")
            print("    Windows 防火墙需放行 " + str(port) + " 端口。")
        else:
            r.warn("局域网地址", "未探测到 IPv4，请检查网络连接")

    print("\n  浏览器地址：" + base_url)
    print("  健康检查  ：" + base_url + "api/health")
    print("  接口文档  ：" + base_url + "docs")
    print("  停止服务  ：Ctrl + C")
    print(f"  体检结论  ：{'通过' if r.ok else '未通过（按上面 FAIL 项处理后重试）'}")
    return r.ok


def main() -> int:
    ap = argparse.ArgumentParser(description="高校智讯本地启动器")
    ap.add_argument("--check", action="store_true", help="只体检不启动")
    ap.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    ap.add_argument("--host", default=None, help="覆盖 APP_HOST（云端用 0.0.0.0）")
    ap.add_argument("--port", type=int, default=None, help="覆盖 APP_PORT")
    ap.add_argument("--reload", action="store_true", help="代码改动后自动重启（开发用）")
    ap.add_argument("--lan", action="store_true",
                    help="局域网模式：自动监听 0.0.0.0 并打印手机 / 鸿蒙可访问地址")
    args = ap.parse_args()

    from app.config import settings

    host = args.host or ("0.0.0.0" if args.lan else settings.APP_HOST)
    port = args.port or settings.APP_PORT
    settings.APP_HOST, settings.APP_PORT = host, port  # 让启动日志与 /api/health 显示一致
    base_url = f"http://{browser_host(host)}:{port}/"

    if not inspect(host, port, settings, lan=args.lan):
        return 1
    if args.check:
        print("\n[--check] 仅体检，未启动服务。")
        return 0

    print("\n=== 服务启动 ===")
    print("  请访问：" + base_url + "   （不要访问 http://0.0.0.0:" + str(port) + "/）")
    if args.lan:
        for url in (f"http://{ip}:{port}/" for ip in lan_addresses()):
            print("  手机端：" + url)
        print("  提示：局域网 http 下页面可用；离线壳与安装为应用需 HTTPS 或 localhost。")
    print()

    def open_browser() -> None:
        try:
            webbrowser.open(base_url)
        except Exception as exc:
            print(f"  [WARN] 自动打开浏览器失败：{exc}，请手动复制上面的地址")

    if args.open:
        timer = threading.Timer(2.0, open_browser)
        timer.daemon = True
        timer.start()

    import uvicorn

    uvicorn.run("app.main:app", host=host, port=port,
                reload=args.reload, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
