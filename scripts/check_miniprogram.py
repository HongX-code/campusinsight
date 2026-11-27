"""小程序工程静态自检：配置完整性、页面四件套、WXML/JS 基本语法。

微信开发者工具不做 CI，所以这里补一层能进流水线的检查：
- app.json 声明的页面必须存在 .js/.json/.wxml/.wxss 四件套
- tabBar 图标文件必须存在
- 每个 .json 必须能解析，每个 .js 必须能过 node --check
- WXML 标签必须配平（简易栈式检查，够用且不引入依赖）
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MP = os.path.join(ROOT, "miniprogram")

results: list[tuple[bool, str]] = []


def check(ok: bool, name: str) -> None:
    results.append((bool(ok), name))


def read(rel: str) -> str:
    path = os.path.join(MP, rel)
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------- app.json
app_json_path = os.path.join(MP, "app.json")
check(os.path.exists(app_json_path), "app.json 存在")

app_cfg = {}
if os.path.exists(app_json_path):
    with open(app_json_path, "r", encoding="utf-8") as fh:
        app_cfg = json.load(fh)
check(bool(app_cfg.get("pages")), "app.json 声明了页面列表")

pages = app_cfg.get("pages") or []
check(len(pages) >= 4, f"页面数量充足（实际 {len(pages)}）")
check("pages/index/index" in pages, "首页在页面列表中")

# 四件套齐备
for page in pages:
    for ext in (".js", ".json", ".wxml", ".wxss"):
        check(os.path.exists(os.path.join(MP, page + ext)), f"页面文件存在 {page}{ext}")

# ---------------------------------------------------------------- tabBar
tab_list = (app_cfg.get("tabBar") or {}).get("list") or []
check(len(tab_list) == 4, f"tabBar 有 4 个标签（实际 {len(tab_list)}）")
for tab in tab_list:
    check(bool(tab.get("text")), f"tabBar 标签有文案 {tab.get('pagePath')}")
    for key in ("iconPath", "selectedIconPath"):
        icon = tab.get(key, "")
        check(bool(icon) and os.path.exists(os.path.join(MP, icon)),
              f"tabBar 图标存在 {icon}")

# 图标规格：微信要求 81x81 以内且小于 40KB
try:
    from PIL import Image

    for tab in tab_list:
        for key in ("iconPath", "selectedIconPath"):
            icon = tab.get(key, "")
            if not icon:
                continue
            path = os.path.join(MP, icon)
            if not os.path.exists(path):
                continue
            with Image.open(path) as img:
                w, h = img.size
            check(w <= 81 and h <= 81, f"tabBar 图标尺寸合规 {icon} ({w}x{h})")
            check(os.path.getsize(path) < 40 * 1024, f"tabBar 图标体积合规 {icon}")
except ImportError:
    results.append((True, "跳过图标尺寸检查（Pillow 不可用）"))

# ---------------------------------------------------------------- JSON
json_files = []
for base, _dirs, files in os.walk(MP):
    for name in files:
        if name.endswith(".json"):
            json_files.append(os.path.join(base, name))
for path in json_files:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            json.load(fh)
        check(True, f"JSON 可解析 {os.path.relpath(path, MP)}")
    except Exception as exc:  # noqa: BLE001
        check(False, f"JSON 可解析 {os.path.relpath(path, MP)} -> {exc}")

# ---------------------------------------------------------------- JS
js_files = []
for base, _dirs, files in os.walk(MP):
    for name in files:
        if name.endswith(".js"):
            js_files.append(os.path.join(base, name))
check(len(js_files) >= 6, f"JS 文件数量（实际 {len(js_files)}）")

node = None
for candidate in ("node", "node.exe"):
    try:
        subprocess.run([candidate, "--version"], capture_output=True, timeout=10, check=True)
        node = candidate
        break
    except Exception:  # noqa: BLE001
        continue

if node:
    for path in js_files:
        proc = subprocess.run([node, "--check", path], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30)
        label = os.path.relpath(path, MP)
        check(proc.returncode == 0, f"JS 语法正确 {label}")
        if proc.returncode != 0:
            print(f"[JS ERROR] {label}\n{proc.stderr[:400]}")
else:
    results.append((True, "跳过 JS 语法检查（node 不可用）"))

# ---------------------------------------------------------------- WXML
WXML_VOID = {"input", "image", "icon", "import", "include", "wxs"}


def check_wxml(path: str) -> tuple[bool, str]:
    text = read(os.path.relpath(path, MP))
    # 去掉注释，避免注释里的尖括号干扰
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    stack: list[str] = []
    for m in re.finditer(r"<(/?)([a-zA-Z][\w-]*)([^>]*?)(/?)>", text):
        closing, tag, _attrs, self_close = m.groups()
        if self_close or tag in WXML_VOID:
            continue
        if closing:
            if not stack:
                return False, f"多余的闭合标签 </{tag}>"
            if stack[-1] != tag:
                return False, f"标签不匹配：期望 </{stack[-1]}>，实际 </{tag}>"
            stack.pop()
        else:
            stack.append(tag)
    if stack:
        return False, f"未闭合标签：{stack}"
    return True, "配平"


for base, _dirs, files in os.walk(MP):
    for name in files:
        if not name.endswith(".wxml"):
            continue
        full = os.path.join(base, name)
        ok, msg = check_wxml(full)
        check(ok, f"WXML 标签配平 {os.path.relpath(full, MP)}")
        if not ok:
            print(f"[WXML ERROR] {os.path.relpath(full, MP)}: {msg}")

# ---------------------------------------------------------------- 内容红线
all_js = "\n".join(read(os.path.relpath(p, MP)).replace("\\", "/") for p in js_files)
all_wxml = "\n".join(
    read(os.path.relpath(os.path.join(b, n), MP))
    for b, _d, fs in os.walk(MP) for n in fs if n.endswith(".wxml")
)

# 1) 禁止 Emoji（与 Web 端同一约束）
emoji = re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", all_wxml + all_js)
check(not emoji, f"小程序端不含 Emoji（发现 {len(set(emoji))} 个）")

# 2) 不允许在页面里硬编码后端地址（必须走 app.js 的 BASE_URL）
hard = re.findall(r"https?://(?!127\.0\.0\.1)", all_wxml)
check(not hard, f"WXML 无硬编码外链（发现 {len(hard)} 个）")

# 3) 页面必须通过 app.request 访问后端，不直接 wx.request
page_js = [p for p in js_files if os.sep + "pages" + os.sep in p]
direct = [os.path.relpath(p, MP) for p in page_js
          if "wx.request(" in read(os.path.relpath(p, MP))]
check(not direct, f"页面不直连 wx.request（越权文件：{direct}）")

# 4) 必须复用统一登录
check("ensureLogin" in all_js, "复用统一登录 ensureLogin")
check("requestSubscribeMessage" in all_js, "调用订阅消息授权")

# 5) 多机型适配：WXSS 布局用 rpx（750rpx = 屏宽，自动适配不同手机），
#    不允许出现裸 px 宽度/高度，否则在小屏上会被撑破、大屏上又偏小。
all_wxss = "\n".join(
    read(os.path.relpath(os.path.join(b, n), MP))
    for b, _d, fs in os.walk(MP) for n in fs if n.endswith(".wxss")
)
raw_px = re.findall(r"(?:width|height|min-width|max-width|font-size)\s*:\s*\d+px\b", all_wxss)
check(not raw_px, f"WXSS 布局尺寸均用 rpx（裸 px：{sorted(set(raw_px))}）")
check("rpx" in all_wxss, "WXSS 使用 rpx 自适应单位")
# 视口与刘海屏：navigationBar 走系统默认，页面内容需给顶部胶囊留安全区
check("safe-top" in all_wxss or "safe-top" in all_wxml,
      "自定义顶部留 safe-top 安全区")

# 6) 截止时间轴：多轨道通知里一条通知有多个截止节点，小程序端必须把节点取回来
#    并标出哪几条是本人要办的，否则学生只看单值截止卡片会读到别人的日期。
detail_js = read(os.path.join("pages", "notice-detail", "notice-detail.js"))
detail_wxml = read(os.path.join("pages", "notice-detail", "notice-detail.wxml"))
check("/timeline`" in detail_js or "/timeline\"" in detail_js,
      "详情页请求截止时间轴接口")
check("roleFromGrade" in detail_js and "博士研究生" in detail_js,
      "小程序把画像年级归一为时间轴身份词")
check("timelineGroups" in detail_js and "timelineGroups" in detail_wxml,
      "时间轴按轨道分组后渲染")
check("tl-mine" in detail_wxml and "tl-admin" in detail_wxml,
      "时间轴区分本人节点与学院节点")
# 剩余天数在 JS 里算好写进数据，小程序模板不能调函数，模板里现算会渲染成空白
check("left_text" in detail_js and "left_text" in detail_wxml,
      "时间轴剩余天数在 JS 内预设")

print("=" * 56)
passed = sum(1 for ok, _ in results if ok)
failed = [(ok, name) for ok, name in results if not ok]
for _ok, name in results:
    if not _ok:
        print(f"  [FAIL] {name}")
print("=" * 56)
print(f"小程序自检：通过 {passed} 项，失败 {len(failed)} 项")
raise SystemExit(0 if not failed else 1)
