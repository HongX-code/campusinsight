"""一键重生成 docs/shot-*.png 界面截图（申报材料与 README 用）。

    python scripts/capture.py                 # 自动起一个临时服务，截完即停
    python scripts/capture.py --keep-server  # 复用 8000 端口上已有的服务
    python scripts/capture.py --only dashboard,notices

截图前会先调用 scripts/reset_db.py 的等价逻辑重置演示库吗？**不会**——
脚本只截图，绝不改动数据。要刷新演示数据请显式运行：
    python scripts/reset_db.py && python scripts/import_samples.py
"""
from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
HOST, PORT = "127.0.0.1", 8765

VIEWS = {
    "home": "shot-home.png",           # 学生端智能工作台（默认首页）
    "notices": "shot-notices.png",
    "match": "shot-match.png",
    "qa": "shot-qa.png",
    "tasks": "shot-tasks.png",
    "mobile": "shot-mobile-platform.png",   # 多端接入
    "conflicts": "shot-conflicts.png",      # 冲突治理
    "dashboard": "shot-dashboard.png",  # 信息治理 · 数据总览
    "sources": "shot-sources.png",
}


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((HOST, port)) == 0


def start_server(port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", HOST, "--port", str(port),
         "--log-level", "warning"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    for _ in range(60):
        if port_open(port):
            return proc
        if proc.poll() is not None:
            raise RuntimeError("服务启动失败，请手动运行 uvicorn 查看报错")
        time.sleep(0.3)
    raise RuntimeError("等待服务超时")


def main() -> None:
    ap = argparse.ArgumentParser(description="高校智讯 · 界面截图")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--only", default="", help="逗号分隔的视图名，缺省全部")
    ap.add_argument("--keep-server", action="store_true", help="复用已运行的服务，不自动启停")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright  # 延迟导入，缺依赖时报错更清楚

    wanted = [v.strip() for v in args.only.split(",") if v.strip()] or list(VIEWS)
    proc = None
    if args.keep_server and port_open(args.port):
        print(f"复用已有服务：http://{HOST}:{args.port}")
    elif port_open(args.port):
        raise RuntimeError(f"端口 {args.port} 已被占用；加 --keep-server 复用，或换 --port")
    else:
        proc = start_server(args.port)
        print(f"临时服务已启动：http://{HOST}:{args.port}")

    base = f"http://{HOST}:{args.port}"
    DOCS.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 960}, device_scale_factor=1)
            # 绕开系统代理对 127.0.0.1 的劫持
            page.goto(base, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1200)

            for view in wanted:
                if view not in VIEWS:
                    print(f"  跳过未知视图 {view}")
                    continue
                if view == "tasks":
                    # 「我的时间轴」要按身份筛选，未登录时只能看到登录提示。
                    # 这里临时以演示账号（研二 → 硕士研究生）登录，拍完再退出，
                    # 避免影响其他视图的默认未登录状态。
                    page.evaluate("""async () => {
                        const r = await fetch('/api/users');
                        const us = await r.json();
                        const u = us.find(x => /研/.test(x.grade || '')) || us[0];
                        if (u) setLogin(u.id);
                    }""")
                    page.wait_for_timeout(1200)
                page.evaluate("(v) => switchView(v)", view)
                page.wait_for_timeout(1400)
                out = DOCS / VIEWS[view]
                page.screenshot(path=str(out), full_page=True)
                print(f"  {view:10s} -> {out.name}")
                if view == "tasks":
                    page.evaluate("() => setLogin(0)")
                    page.wait_for_timeout(400)

            # 专项图：首页 AI 洞察 Hero 与推荐机会（学生端主视觉）
            page.evaluate("(v) => switchView(v)", "home")
            page.wait_for_timeout(1000)
            hero = page.locator(".hero")
            if hero.count():
                hero.screenshot(path=str(DOCS / "shot-home-ai.png"))
                print("  home(ai)    -> shot-home-ai.png")
            opps = page.locator("#homeOpps")
            if opps.count():
                opps.screenshot(path=str(DOCS / "shot-home-opps.png"))
                print("  home(opps)  -> shot-home-opps.png")
            # 完整长图：临时加高视口（.scroll 是内部滚动容器，元素截图会错位）
            page.set_viewport_size({"width": 1440, "height": 1800})
            page.wait_for_timeout(600)
            page.screenshot(path=str(DOCS / "shot-home-full.png"))
            page.set_viewport_size({"width": 1440, "height": 960})
            print("  home(full)  -> shot-home-full.png")

            # 专项图：看板「文体构成」卡片（整页截图截不到内部滚动区）
            page.evaluate("(v) => switchView(v)", "dashboard")
            page.wait_for_timeout(1000)
            genre_card = page.locator(".card:has(#genreBars)")
            if genre_card.count():
                genre_card.screenshot(path=str(DOCS / "shot-genres.png"))
                print("  genres      -> shot-genres.png")

            # 专项图：通知中心按「新闻资讯」筛选，展示闸门后的空字段列表
            page.evaluate("(v) => switchView(v)", "notices")
            page.wait_for_timeout(500)
            page.evaluate("""async () => {
                state.genre = 'news'; state.page = 1;
                document.querySelector('#noticeGenre').value = 'news';
                await renderNotices();
            }""")
            page.wait_for_timeout(1000)
            page.screenshot(path=str(DOCS / "shot-genre.png"), full_page=True)
            print("  genre(news) -> shot-genre.png")

            # 详情抽屉：优先截一条新闻稿，展示文体闸门效果
            ids = page.evaluate("""async () => {
                const r = await fetch('/api/notices?genre=news&size=1');
                const d = await r.json();
                return d.items && d.items.length ? d.items[0].id : null;
            }""")
            if ids:
                page.evaluate("""async () => { state.genre = ''; state.page = 1; await renderNotices(); }""")
                page.evaluate("(i) => openNotice(i)", ids)
                page.wait_for_timeout(1000)
                out = DOCS / "shot-detail.png"
                page.screenshot(path=str(out), full_page=True)
                print(f"  detail(news) -> {out.name}  (notice_id={ids})")
            else:
                print("  未找到新闻稿，跳过 detail 截图（先运行 import_samples.py）")

            # 专项图：结构化字段契约 + 规则校验 + 人工审核三态入口
            fid = page.evaluate("""async () => {
                const r = await fetch('/api/notices?size=100');
                const d = await r.json();
                const hit = d.items.find(x => x.analysis && x.analysis.actionable
                    && x.analysis.location && x.analysis.checks && x.analysis.checks.passed);
                return hit ? hit.id : null;
            }""")
            if fid:
                page.evaluate("(i) => openNotice(i)", fid)
                page.wait_for_timeout(1000)
                out = DOCS / "shot-fields.png"
                page.locator(".drawer").screenshot(path=str(out))
                print(f"  fields      -> {out.name}  (notice_id={fid})")
                review = page.locator(".check-panel")
                if review.count():
                    review.screenshot(path=str(DOCS / "shot-review.png"))
                    print("  review      -> shot-review.png")
                # 临时加高视口，把 14 类字段与审核区完整拍进一张图
                page.set_viewport_size({"width": 1440, "height": 2100})
                page.wait_for_timeout(350)
                page.locator(".drawer").screenshot(path=str(DOCS / "shot-fields-full.png"))
                page.set_viewport_size({"width": 1440, "height": 960})
                print("  fields(full)-> shot-fields-full.png")
            else:
                print("  未找到带结构化字段的事务通知，跳过 fields 截图")

            # 专项图：点击「原文依据」在正文中高亮定位（AI 结论 → 字段 → 原文）
            ev_id = page.evaluate("""async () => {
                const r = await fetch('/api/notices?size=100');
                const d = await r.json();
                const hit = d.items.find(x => x.analysis && x.analysis.evidence
                    && Object.keys(x.analysis.evidence).length > 0);
                return hit ? hit.id : null;
            }""")
            if ev_id:
                page.evaluate("(i) => openNotice(i)", ev_id)
                page.wait_for_timeout(800)
                clicked = page.evaluate("""() => {
                    const e = document.querySelector('.evi[data-evi]');
                    if (!e) return false;
                    e.click(); return true;
                }""")
                if clicked:
                    page.wait_for_timeout(3000)  # 等 Toast 淡出，避免拍进截图
                    page.locator(".content-box").screenshot(path=str(DOCS / "shot-evidence.png"))
                    print("  evidence    -> shot-evidence.png")
            page.evaluate("closeDrawer()")

            # 专项图：表格结构复原（采集期保住列边界后，学院/专业/电话从表格还原）
            # 这张图要能看出「表头 + 列语义 + 派生出的学院与电话」，
            # 所以截的是详情抽屉里标了「表格里的列边界」说明的那块面板。
            tb_id = page.evaluate("""async () => {
                const r = await fetch('/api/notices?size=100');
                const d = await r.json();
                const hit = d.items.find(x => x.analysis && x.analysis.table_stats
                    && x.analysis.table_stats.total > 0);
                return hit ? hit.id : null;
            }""")
            if tb_id:
                page.evaluate("(i) => openNotice(i)", tb_id)
                page.wait_for_timeout(1200)
                card = page.locator(".tl-panel:has-text('表格里的列边界')")
                if card.count():
                    card.first.screenshot(path=str(DOCS / "shot-table.png"))
                    # 再拍一张整抽屉：表格卡片 + 由它补全的字段并排出现，
                    # 才能说明「表格里的 6 个学院确实进了 major_requirement」。
                    page.set_viewport_size({"width": 1440, "height": 2400})
                    page.wait_for_timeout(400)
                    page.locator(".drawer").screenshot(path=str(DOCS / "shot-table-full.png"))
                    page.set_viewport_size({"width": 1440, "height": 960})
                    print(f"  table       -> shot-table.png / shot-table-full.png  (notice_id={tb_id})")
                else:
                    print("  详情里没有表格卡片，跳过 table 截图")
                page.evaluate("closeDrawer()")
            else:
                print("  未找到含表格的通知，跳过 table 截图")

            # 专项图：录入弹窗
            # 注意选择器要限定到 #modalMask：页面上还有一个登录弹窗（.modal.login-modal），
            # 直接写 .modal 会命中两个元素，触发 Playwright 的 strict mode violation。
            page.evaluate("openModal()")
            page.wait_for_timeout(400)
            page.locator("#modalMask .modal").screenshot(path=str(DOCS / "shot-ingest.png"))
            page.evaluate("closeModal()")
            print("  ingest      -> shot-ingest.png")

            # 专项图：移动端首屏（视口截图才能拍到 fixed 底部导航）+ 完整长页
            mob = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=1)
            mob.goto(base, wait_until="networkidle", timeout=30000)
            mob.wait_for_timeout(1200)
            mob.screenshot(path=str(DOCS / "shot-mobile.png"))
            mob.screenshot(path=str(DOCS / "shot-mobile-full.png"), full_page=True)
            mob.close()
            print("  mobile      -> shot-mobile.png / shot-mobile-full.png")

            browser.close()
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            print("临时服务已停止")


if __name__ == "__main__":
    main()
