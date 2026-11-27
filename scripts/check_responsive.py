"""多机型视口自检：在 320/375/390/430/768 五档宽度下确认页面不横向溢出。

    python scripts/check_responsive.py

为什么需要它：静态 CSS 断言只能钉住「写法」，钉不住「渲染结果」。
2026-11-25 的真机视口诊断发现，grid 轨道的 auto 最小尺寸会被
white-space:nowrap 的标题撑到 480px+，卡片看起来「显示不全」——
这类问题只有真的把浏览器缩到手机宽度才看得出来。

脚本自己起一个临时 uvicorn（8769 端口），跑完即停；依赖 playwright，
缺失时明确报错而不是静默跳过（与 capture.py 同一约定）。
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOST, PORT = "127.0.0.1", 8769

VIEWPORTS = [
    ("320 窄屏安卓", 320, 640),
    ("375 iPhone SE", 375, 667),
    ("390 iPhone 14", 390, 844),
    ("430 大屏安卓", 430, 932),
    ("768 平板", 768, 1024),
]
# 每档都要切过去确认没有横向滚动的视图
VIEWS = ["home", "tasks", "dashboard", "mobile", "conflicts", "sources"]


def port_open() -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((HOST, PORT)) == 0


results: list[tuple[bool, str]] = []


def check(ok: bool, name: str) -> None:
    results.append((bool(ok), name))


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[FATAL] 需要 playwright：pip install playwright && playwright install chromium")
        return 2

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", HOST,
         "--port", str(PORT), "--log-level", "warning"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(60):
            if port_open():
                break
            if proc.poll() is not None:
                raise RuntimeError("服务启动失败")
            time.sleep(0.3)
        else:
            raise RuntimeError("等待服务超时")

        base = f"http://{HOST}:{PORT}"
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for label, w, h in VIEWPORTS:
                page = browser.new_page(viewport={"width": w, "height": h})
                page.goto(base, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(600)
                for view in VIEWS:
                    page.evaluate("(v) => switchView(v)", view)
                    page.wait_for_timeout(120)
                    m = page.evaluate("""() => ({
                        sw: document.documentElement.scrollWidth,
                        iw: window.innerWidth
                    })""")
                    check(m["sw"] <= m["iw"] + 1,
                          f"{label} · {view} 无横向滚动（{m['sw']}/{m['iw']}）")

                # 展开「更多」面板后仍需贴合视口
                page.evaluate("openSheet()")
                page.wait_for_timeout(250)
                sh = page.evaluate("""() => {
                    const r = document.querySelector('#moreSheet').getBoundingClientRect();
                    return {left: r.left, right: r.right, iw: window.innerWidth};
                }""")
                check(sh["left"] >= -1 and sh["right"] <= sh["iw"] + 1,
                      f"{label} · 更多面板贴合视口")

                # 打开详情抽屉后仍需贴合视口。抽屉有 translateX 入场动画，
                # 必须等动画结束（>0.4s）再量，否则量到中途位置是假阴性。
                page.evaluate("closeSheet()")
                page.wait_for_timeout(400)
                opened = page.evaluate("""async () => {
                    const r = await fetch('/api/notices?size=1');
                    const d = await r.json();
                    if (d.items && d.items.length) { openNotice(d.items[0].id); return true; }
                    return false;
                }""")
                if opened:
                    page.wait_for_timeout(800)
                    dr = page.evaluate("""() => {
                        const r = document.querySelector('.drawer').getBoundingClientRect();
                        return {left: r.left, right: r.right, iw: window.innerWidth};
                    }""")
                    check(dr["left"] >= -1 and dr["right"] <= dr["iw"] + 1,
                          f"{label} · 详情抽屉贴合视口（{dr['left']:.0f}~{dr['right']:.0f}/{dr['iw']}）")
                page.close()
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    passed = sum(1 for ok, _ in results if ok)
    print("=" * 56)
    for ok, name in results:
        if not ok:
            print(f"  [FAIL] {name}")
    print("=" * 56)
    print(f"多机型视口自检：通过 {passed} 项，失败 {len(results) - passed} 项")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
