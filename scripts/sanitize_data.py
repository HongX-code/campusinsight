#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集快照脱敏：把真实站点抓下来的个人信息替换成占位值。

为什么需要这一步
----------------
`data/real_notices.json` 是从学校官网抓下来的**真实**通知快照，原文里带着
工作人员的私人手机号、个人邮箱和校内系统的内网 IP。这些内容出现在学校内网
页面里是正常的，但一旦随代码仓库公开，就变成了「把别人的联系方式再发布一次」：
号码会被爬虫收走，当事人也没有同意过。所以入库到公开仓库之前必须换成占位值。

只替换四类**个人信息**，不碰业务事实：

    个人手机号   1[3-9]xxxxxxxxx        → 1380000XXXX（保留 11 位手机形态）
    个人邮箱     个人 QQ / 126 邮箱      → exampleN@example.com
    个人姓名     联系人姓名              → 「姓 + 老师」
    内网 IP      内网地址                → host-N.example.edu.cn

刻意**不改**的三类：

    校名与机构名     这是作品要解决的问题域，抹掉就看不懂场景了
    机构角色邮箱     xgb@ / yjsy@ 这类是部门对外公开信箱，不是个人信息
    带区号的办公电话 027-87xxxxxx 是单位总机，公开可查

替换后必须保证解析行为不变——`scripts/real_audit.py` 的报告要逐字一致，
否则说明这几个占位值改变了字段抽取结果，那就得重新挑占位值。

真值为什么不写在代码里
----------------------
这个脚本本身会进公开仓库。如果映射表里写着真实手机号，脱敏就变成了
「先把真号抄进代码，再用它去替换真号」——泄漏只是换了个文件继续存在。
所以真值一律放在工作区根目录的 `.sanitize_map.json`（已在 `.gitignore` 中），
仓库里只有「怎么替换」的规则，没有「替换谁」。

    .sanitize_map.json 结构：
        {
          "mobile": {"1xxxxxxxxxx": "13800000001"},
          "email":  {"someone@<个人邮箱域名>": "example1@example.com"},
          "name":   {"张三": "张老师"},
          "ip":     {"<内网地址>": "host-1.example.edu.cn"}
        }

映射文件缺失时，手机号 / 邮箱 / 内网 IP 仍能靠正则兜底并生成稳定的占位值
（同一号码每次算出的占位值相同，不会来回抖动）；姓名没有可靠的通用特征，
只能靠映射文件，缺失时脚本会明确提示「姓名类未启用」而不是假装扫过了。

用法：
    python scripts/sanitize_data.py            # 就地脱敏
    python scripts/sanitize_data.py --check    # 只检查，不写入（有残留则退出码 1）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "data" / "real_notices.json"
MAP_FILE = ROOT / ".sanitize_map.json"

_MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL_RE = re.compile(r"[\w.+-]+@(?:qq|126|163|sina|gmail)\.com", re.IGNORECASE)
_IP_RE = re.compile(r"(?<![\d.])(?:218|10|192\.168|172\.(?:1[6-9]|2\d|3[01]))"
                    r"(?:\.\d{1,3}){3}(?::\d{2,5})?(?![\d.])")

# 占位手机号统一落在 1380000 段：保留 11 位形态，正则照样识别，
# 但这一万个子号不指向任何真实用户，不会被当成真人号码。
_PLACEHOLDER_PREFIX = "1380000"


def load_map() -> dict[str, dict[str, str]]:
    """读取本地映射文件；不存在时返回空表（只走正则兜底）。"""
    if not MAP_FILE.exists():
        return {"mobile": {}, "email": {}, "name": {}, "ip": {}}
    try:
        data = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 配置文件坏了要给出可读原因
        print("[WARN] %s 解析失败：%s，改用正则兜底" % (MAP_FILE.name, exc))
        return {"mobile": {}, "email": {}, "name": {}, "ip": {}}
    out = {}
    for key in ("mobile", "email", "name", "ip"):
        value = data.get(key)
        out[key] = value if isinstance(value, dict) else {}
    return out


def _stable(value: str, modulus: int) -> int:
    """按内容算一个稳定的序号：同一个原值每次得到同一个占位值。"""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % modulus


def _scrub(text: str, table: dict[str, dict[str, str]]) -> tuple[str, dict[str, int]]:
    """对一段文本做四类替换，返回新文本与各类命中次数。"""
    counts = {"mobile": 0, "email": 0, "name": 0, "ip": 0}

    # 显式映射优先：这些是人工确认过、需要固定占位值的条目
    for kind in ("mobile", "email", "name", "ip"):
        for old, new in table.get(kind, {}).items():
            if old and old in text:
                counts[kind] += text.count(old)
                text = text.replace(old, new)

    # 正则兜底：映射表没覆盖到的（例如下次重新抓取带进来的新号码）也能换掉
    # 已经是占位值的不能再替换一次：脚本要能反复跑，第二次跑的结果必须与第一次
    # 完全相同，否则每跑一次占位号就变一个，diff 全是噪声。
    def _mobile_sub(m: re.Match) -> str:
        if m.group(0).startswith(_PLACEHOLDER_PREFIX):
            return m.group(0)
        counts["mobile"] += 1
        return _PLACEHOLDER_PREFIX + "%04d" % _stable(m.group(0), 10000)

    def _email_sub(m: re.Match) -> str:
        if m.group(0).lower().startswith("example"):
            return m.group(0)
        counts["email"] += 1
        return "example%d@example.com" % (_stable(m.group(0).lower(), 900) + 10)

    def _ip_sub(m: re.Match) -> str:
        if m.group(0).startswith("host-"):
            return m.group(0)
        counts["ip"] += 1
        raw = m.group(0)
        tail = raw[raw.index(":"):] if ":" in raw else ""
        return "host-%04d.example.edu.cn%s" % (_stable(raw, 10000), tail)

    text = _MOBILE_RE.sub(_mobile_sub, text)
    text = _EMAIL_RE.sub(_email_sub, text)
    text = _IP_RE.sub(_ip_sub, text)
    return text, counts


def _walk(node, totals: dict[str, int], table: dict[str, dict[str, str]]):
    """递归改写 JSON 里所有字符串字段（含嵌套的 items / title / content）。"""
    if isinstance(node, str):
        new, c = _scrub(node, table)
        for k, v in c.items():
            totals[k] = totals.get(k, 0) + v
        return new
    if isinstance(node, list):
        return [_walk(x, totals, table) for x in node]
    if isinstance(node, dict):
        return {k: _walk(v, totals, table) for k, v in node.items()}
    return node


def residual(text: str, table: dict[str, dict[str, str]] | None = None) -> list[str]:
    """检查是否还有未脱敏的个人信息残留。

    占位值本身长得就像手机号 / 邮箱 / 主机名，检查时必须把它们排除掉，
    否则 `--check` 会把自己刚写进去的占位值报成残留，永远退不出非零。
    """
    table = table or load_map()
    allowed_mobile = set(table.get("mobile", {}).values())
    allowed_email = set(table.get("email", {}).values())

    out = []
    out += ["手机号 " + m for m in _MOBILE_RE.findall(text)
            if m not in allowed_mobile and not m.startswith(_PLACEHOLDER_PREFIX)]
    out += ["个人邮箱 " + m for m in _EMAIL_RE.findall(text)
            if m.lower() not in {e.lower() for e in allowed_email}
            and not m.lower().startswith("example")]
    out += ["内网IP " + m for m in _IP_RE.findall(text) if not m.startswith("host-")]
    out += ["姓名 " + n for n in table.get("name", {}) if n and n in text]
    return sorted(set(out))


def main() -> int:
    ap = argparse.ArgumentParser(description="采集快照脱敏")
    ap.add_argument("--check", action="store_true", help="只检查，不写入")
    args = ap.parse_args()

    table = load_map()
    has_map = any(table.get(k) for k in table)
    if not has_map:
        print("[WARN] 未找到 %s：手机号/邮箱/内网 IP 走正则兜底，姓名类不启用。"
              % MAP_FILE.name)

    if not TARGET.exists():
        print("找不到 %s" % TARGET)
        return 2

    raw = TARGET.read_text(encoding="utf-8")
    data = json.loads(raw)
    before = residual(raw, table)

    totals: dict[str, int] = {}
    clean = _walk(data, totals, table)
    out = json.dumps(clean, ensure_ascii=False, indent=2)
    after = residual(out, table)

    print("脱敏目标：%s" % TARGET.name)
    print("  替换：手机号 %d / 个人邮箱 %d / 姓名 %d / 内网 IP %d"
          % (totals.get("mobile", 0), totals.get("email", 0),
             totals.get("name", 0), totals.get("ip", 0)))
    print("  替换前残留 %d 项，替换后残留 %d 项" % (len(before), len(after)))
    for item in after:
        print("    [残留] %s" % item)

    if args.check:
        return 1 if after else 0
    if after:
        print("仍有残留，未写入。请补充映射规则。")
        return 1

    # 内容没变就不重写，避免 json.dumps 的格式化差异产生无意义 diff
    if out != raw:
        TARGET.write_text(out + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
        print("  已写入（%d 字节）" % TARGET.stat().st_size)
    else:
        print("  无需改动")
    return 0


if __name__ == "__main__":
    sys.exit(main())
