"""前端接线自检：确认 app.js 引用的每个 DOM id 都真实存在于 index.html。

这是纯静态检查，但能挡住最常见的一类线上故障——
JS 里给一个不存在的元素绑事件，页面加载即抛 TypeError，整站白屏。
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

results: list[tuple[bool, str]] = []


def check(ok: bool, name: str) -> None:
    results.append((bool(ok), name))


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as fh:
        return fh.read()


html = read(os.path.join("app", "templates", "index.html"))
js = read(os.path.join("app", "static", "js", "app.js"))
css = read(os.path.join("app", "static", "css", "style.css"))
css2 = read(os.path.join("app", "static", "css", "mobile.css"))

# ---------------------------------------------------------------- 1. id 对齐
html_ids = set(re.findall(r'\bid="([A-Za-z][\w-]*)"', html))
# JS 动态渲染的节点（抽屉、表单）也在 JS 模板串里声明 id，一并算作「已定义」
js_inline_ids = set(re.findall(r'\bid="([A-Za-z][\w-]*)"', js))
known_ids = html_ids | js_inline_ids

# JS 里所有 $("#xxx") / $$("#xxx ...") 的引用
js_ids = set(re.findall(r'\$\$?\("#([A-Za-z][\w-]*)', js))

missing = sorted(js_ids - known_ids)
# 少数 id 是运行时拼出来的（#qaPane + 页签名），无法静态枚举，显式豁免
DYNAMIC_PREFIXES = ("qaPane",)
real_missing = [m for m in missing if not m.startswith(DYNAMIC_PREFIXES)]
check(not real_missing, f"JS 引用的 id 均有定义（缺失：{real_missing}）")

# 静态 HTML 里的 id 与 JS 动态 id 不得冲突，否则重渲染会互相覆盖
conflict_ids = sorted(html_ids & js_inline_ids)
check(not conflict_ids, f"静态与动态 id 无冲突（冲突：{conflict_ids}）")

# ---------------------------------------------------------------- 2. 视图对齐
html_views = set(re.findall(r'id="view-([a-z-]+)"', html))
nav_views = set(re.findall(r'data-view="([a-z-]+)"', html))
meta_views = set(re.findall(r'^\s{2}([a-z-]+): \["', js, re.M))

check(
    nav_views <= html_views,
    f"导航项都有对应视图（多出：{sorted(nav_views - html_views)}）",
)
check(
    nav_views <= meta_views,
    f"导航项都有 VIEW_META（多出：{sorted(nav_views - meta_views)}）",
)
check(
    html_views <= meta_views,
    f"视图都有 VIEW_META（多出：{sorted(html_views - meta_views)}）",
)

# ---------------------------------------------------------------- 3. 渲染分发
# qa 视图不靠 renderXxx 渲染（对话区由 askQuestion 增量维护），单列豁免
NO_RENDER = {"qa"}
for view in sorted(html_views & meta_views):
    if view in NO_RENDER:
        check(f'if (name === "{view}"' in js, f"switchView 处理 {view} 视图")
        continue
    camel = "".join(p.capitalize() for p in view.split("-"))
    pattern = f'if (name === "{view}") render{camel}()'
    check(pattern in js, f"switchView 分发到 render{camel}")

# ---------------------------------------------------------------- 4. SVG 图标
html_symbols = set(re.findall(r'<symbol id="(i-[\w-]+)"', html))
js_icons = set(re.findall(r'href="#(i-[\w-]+)"', js))
check(
    js_icons <= html_symbols,
    f"JS 引用的图标均已定义（缺失：{sorted(js_icons - html_symbols)}）",
)

# PLATFORM_META 里的图标名
platform_icons = set(re.findall(r'icon: "(i-[\w-]+)"', js))
check(
    platform_icons <= html_symbols,
    f"多端平台图标均已定义（缺失：{sorted(platform_icons - html_symbols)}）",
)

# ---------------------------------------------------------------- 5. 样式表
check("/static/css/mobile.css" in html, "已引入 mobile.css")
for cls in ["page-head", "kpi-row", "mobile-grid", "platform-row",
            "conflict-card", "cf-compare", "nav-badge", "cfg-row"]:
    check(f".{cls}" in css2, f"mobile.css 定义了 .{cls}")

# ---------------------------------------------------------------- 6. 内容红线
# 禁止 Emoji（项目统一约束）。注意 ✕ ✦ 等排版符号属于 Unicode 符号区，
# 不是 Emoji，但这里仍统一用一个更严的白名单把它们排除在检测之外。
SYMBOL_ALLOW = {"✕", "✓", "·", "—", "›", "≠", "…", "→", "←", "「", "」", "《", "》"}
emoji = [
    ch for ch in re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\u2190-\u21FF\u2B00-\u2BFF]", html + js)
    if ch not in SYMBOL_ALLOW
]
check(not emoji, f"前端不含 Emoji（发现 {sorted(set(emoji))}）")

# ---------------------------------------------------------------- 7. 冲突视图字段
check("by_severity" in js, "冲突 KPI 读 by_severity")
check("unresolved" in js, "冲突徽标读 unresolved")
check("summary.unresolved" in js or "summary && res.summary.unresolved" in js,
      "徽标取未裁决数")

# ---------------------------------------------------------------- 8. 移动端视口回归
# 这几条是 2026-11-25 真机视口诊断（320/375/390/430/768）发现的横向溢出根因，
# 纯静态钉住，避免以后改样式又把手机上撑出视口。
check(
    'name="viewport"' in html and "width=device-width" in html,
    "声明 viewport 以 device-width 布局",
)
# .card 作为 grid/flex 子项必须 min-width:0，否则内部 nowrap 标题的
# min-content 会把卡片撑到 480px+，小屏出现横向滚动。
card_block = re.search(r"\.card\s*\{([^}]*)\}", css)
check(
    bool(card_block) and re.search(r"min-width:\s*0", card_block.group(1)) is not None,
    "卡片 min-width:0 防止 nowrap 内容撑破网格",
)
# grid 轨道用 minmax(0,1fr)，不许裸 1fr（裸 1fr 的 auto 最小尺寸=min-content）
grid2 = re.search(r"\.grid-2\s*\{([^}]*)\}", css)
check(
    bool(grid2) and "minmax(0, 1fr)" in grid2.group(1),
    "双列网格用 minmax(0,1fr) 不撑破视口",
)
# .sheet 在移动端切成 display:flex，必须显式 column，
# 否则 6 个 width:100% 的 .sheet-item 横向排开溢出。
sheet_block = re.search(r"\.sheet\s*\{([^}]*)\}", css)
check(
    bool(sheet_block) and "flex-direction: column" in sheet_block.group(1),
    "更多面板 flex-direction:column 防止横向铺开",
)

# 表格列分隔符 \x1f 是控制字符，浏览器不为它排版。
# 直接塞进 innerHTML 会让「学院名称\x1f专业代码」在界面上粘成一串，
# 看上去像解析出错——所以正文必须走 pretty() 把它渲染成可见竖线。
check("\\u001f" in js, "正文渲染把列分隔符转成可见竖线")
check(
    "pretty(n.content)" in js,
    "通知原文走 pretty() 而不是裸 esc()",
)
check(
    re.search(r"\.cell-sep\s*\{", css) is not None,
    "列分隔符样式 .cell-sep 已定义",
)
# 存量压平正文不可复原，界面必须显式提示重新采集。
# 若这里静默返回空，用户会以为「这条通知本来就没有表格」，
# 而真实原因是我们早期的采集器把二维结构扔掉了。
check(
    "table_needs_recollect" in js and "需重新采集" in js,
    "存量压平正文提示重新采集而不是静默空着",
)

print("=" * 56)
passed = sum(1 for ok, _ in results if ok)
for ok, name in results:
    if not ok:
        print(f"  [FAIL] {name}")
print("=" * 56)
print(f"前端接线自检：通过 {passed} 项，失败 {len(results) - passed} 项")
raise SystemExit(0 if passed == len(results) else 1)
