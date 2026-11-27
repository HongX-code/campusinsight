"""表格不是段落：把 <table> 的二维结构一路带到字段层。

立论（与「多轨道截止时间轴」一脉相承，都是数据结构层面的修正）：

采集器过去把所有块级标签一视同仁地换行，`<td>` 边界在 `get_text("")` 里
根本不存在——一张二维表被压成一维字符串，列语义**不可逆丢失**。招生计划表
「学院 × 专业代码 × 专业名称 × 拟招生计划 × 咨询电话」压平之后：

    '学院名称专业代码专业名称'
    '拟招生计划咨询电话材料科学与工程学院'      ← 表头末列与首行首列粘连
    '085600材料与化工1027-87164677'             ← 代码/名称/电话三段粘连
    '1027-87218976'                             ← 电话被挤成独立行，与学院错位

后果是 `major_requirement` 为空（5 个学院、5 个专业代码一个都没抽出来）、
`target` 判反了、电话被 `[:3]` 截断且丢掉 `027-` 区号。

这不是抽取算法不聪明，是**采集阶段就已经把结构扔了**——一维字符串里没有
「列」这个概念，下游再怎么改正则也补不回来。所以修在采集：`<table>` 序列化
成「行用 \\n、单元格用 \\t」的规整文本，再由本模块解析成带列语义的表，最后
映射到业务字段，并逐格回原文接地（C11）。

关于存量正文，这里必须说清楚一件事：**旧版采集器已经入库的压平正文，
列边界不可复原，只能重新采集。**

早先这里写过一个 `recover()`，声称能按「重复出现的列宽模式」把压平的表格
复原出来。实际压测后它对自己声称要修的那种正文完全无效——它只在「上一行
本来就带列分隔符」时才动手，而压平正文里根本没有分隔符；它唯一会动的场景
是正文本就有列边界、根本不需要复原。更糟的是它会把电话拼到上一行末尾，
拼出来的字符串在原文里并不存在，与 C11「逐格回放原文」直接冲突。

按「未验证不许写成已完成」的规矩，那段代码已删除。压平之后
`学院名称专业代码专业名称` 与 `085600材料与化工1027-87164677` 里既没有
可数的列宽，也没有稳定的切分点：`085600` 是 6 位代码、`027-87164677` 是
电话、`1027-87164677` 是粘连结果，硬切只会造出原文没有的事实。所以
`parse()` 对这类正文返回空表，由界面提示「该通知需重新采集」。

删除而不是「修好它」，是因为复原在信息论上就不成立：一条 `085600材料与化工
1027-87164677` 既可能是三个字段，也可能是「085600 材料与化工 1027」加一个
电话，没有外部信息可判定。承认不可复原，比给出一个看起来能用的猜测诚实。
"""
from __future__ import annotations

import re

# 单元格分隔符用 U+001F（Unit Separator）而不是 \t：
# textutil.normalize_text 的 _SPACE_RE = [ \t\u3000]+ 会把制表符压成空格，
# 列边界还没走到这里就被抹平了。\x1f 不在任何空白类里，能穿过清洗链路；
# 正文里也不会自然出现这个控制符，所以切分没有歧义（`||`、`|` 会被正文引用）。
CELL_SEP = "\x1f"
MAX_TABLES = 8
MAX_ROWS = 60
MAX_COLS = 12

# ---------------------------------------------------------------- 列语义
# 顺序有意义：先判「专业代码」再判「专业名称」，否则「专业代码」会被
# 「专业」这个更宽的词吃掉，代码列被误当名称列。
COL_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("seq", ("序号", "编号", "序")),
    ("major_code", ("专业代码", "科目代码", "代码", "编码")),
    ("phone", ("咨询电话", "联系电话", "电话", "联系方式", "手机")),
    ("email", ("邮箱", "电子邮件", "email", "E-mail")),
    ("college", ("学院", "培养单位", "单位", "部门", "院系", "系所")),
    ("major_name", ("专业名称", "专业", "学科", "科目", "方向")),
    ("person", ("联系人", "导师", "姓名", "负责人", "教师")),
    ("plan", ("拟招生计划", "招生计划", "计划", "名额", "人数", "指标", "数量")),
    ("date", ("时间", "日期", "截止", "期限")),
    ("link", ("网址", "链接", "地址", "URL", "网站")),
    ("detail", ("备注", "说明", "要求", "内容", "事项", "办理")),
)

# 一张表要能判成「有结构」，至少要有两个可识别列，否则就是排版用的伪表格
MIN_TYPED_COLS = 2


def col_kind(header: str) -> str:
    """把表头文字归到列语义。归不上返回空串（保留原始表头，不硬猜）。"""
    head = re.sub(r"[\s\u3000（）()：:]", "", header or "")
    if not head:
        return ""
    for kind, keys in COL_RULES:
        if any(k in head for k in keys):
            return kind
    return ""


def _clean_cell(cell: str) -> str:
    cell = (cell or "").replace("\u3000", " ").strip()
    cell = re.sub(r"\s{2,}", " ", cell)
    return cell


def parse(text: str) -> list[dict]:
    """从正文里切出所有「制表符分隔」的表。

    只认同时满足两条的块：①至少 2 行；②每行列数一致或首行列数最多。
    返回 [{headers, rows, columns, kinds, typed, kind, raw}]，按出现顺序。
    """
    out: list[dict] = []
    block: list[list[str]] = []

    def flush() -> None:
        nonlocal block
        if len(block) >= 2:
            table = _build(block)
            if table:
                out.append(table)
        block = []

    for line in (text or "").split("\n"):
        if CELL_SEP in line:
            cells = [_clean_cell(c) for c in line.split(CELL_SEP)]
            cells = cells[:MAX_COLS]
            if any(cells):
                block.append(cells)
                continue
        flush()
    flush()
    return out[:MAX_TABLES]


def _build(rows: list[list[str]]) -> dict | None:
    """把一组单元格行整理成表：定列数、认表头、判类型。"""
    width = max(len(r) for r in rows)
    if width < 2:
        return None
    norm = [r + [""] * (width - len(r)) for r in rows]
    headers = norm[0]
    body = norm[1:]
    if not body:
        return None
    kinds = [col_kind(h) for h in headers]
    typed = sum(1 for k in kinds if k)
    if typed < MIN_TYPED_COLS:
        return None
    body = [r for r in body if any(c for c in r)][:MAX_ROWS]
    if not body:
        return None
    return {
        "headers": headers,
        "rows": body,
        "columns": width,
        "kinds": kinds,
        "typed": typed,
        "kind": _table_kind(kinds),
        "raw": CELL_SEP.join(headers),
    }


def _table_kind(kinds: list[str]) -> str:
    """判表用途：招生计划 / 时间安排 / 联络方式 / 泛用清单。"""
    have = set(kinds)
    if "college" in have and ("major_name" in have or "major_code" in have):
        return "major_plan"
    if "date" in have and ("detail" in have or "major_name" in have):
        return "schedule"
    if "phone" in have or "email" in have:
        return "contact"
    return "list"


def _col(table: dict, kind: str) -> list[str]:
    """取某一语义列的全部单元格。同语义多列时合并（先到先得，去空）。"""
    idxs = [i for i, k in enumerate(table["kinds"]) if k == kind]
    if not idxs:
        return []
    vals: list[str] = []
    for row in table["rows"]:
        for i in idxs:
            v = row[i] if i < len(row) else ""
            if v:
                vals.append(v)
                break
    return vals


def _cell_lines(table: dict) -> list[str]:
    """把表的每一行还原成「单元格用顿号连接」的可读句，用作字段证据。

    证据必须能在原文里定位，所以这里只做分隔符替换（\\x1f → 、），
    不增删任何字符，保持与正文同源。
    """
    return ["、".join(c for c in row if c) for row in table["rows"] if any(row)]


def extract(text: str) -> dict:
    """表格抽取总入口：返回表结构、派生字段、统计。

    派生字段只做「正文抽不到时的补位」，不覆盖正文已抽到的值——
    正文里显式写「面向材料科学与工程学院」比表里一列学院名更可信。
    """
    tables = parse(text)
    colleges = []
    majors = []
    phones = []
    for table in tables:
        for v in _col(table, "college"):
            if _looks_college(v) and v not in colleges:
                colleges.append(v)
        for v in _col(table, "major_name"):
            if _looks_major(v) and v not in majors:
                majors.append(v)
        for kind in ("major_code", "major_name"):
            for v in _col(table, kind):
                if _looks_major(v) and v not in majors:
                    majors.append(v)
        for v in _col(table, "phone"):
            for p in re.findall(r"(?:0\d{2,3}[- ]?)?\d{7,8}|1[3-9]\d{9}", v):
                if p not in phones:
                    phones.append(p)
    stats = {
        "total": len(tables),
        "kinds": sorted({t["kind"] for t in tables}),
        "rows": sum(len(t["rows"]) for t in tables),
        "cells": sum(len(t["rows"]) * t["columns"] for t in tables),
        "colleges": len(colleges),
        "majors": len(majors),
        "phones": len(phones),
    }
    # 一张表都没解析出来，但正文明显带着表格特征：说明这是旧版采集器压平的
    # 存量正文，列边界已经丢了。这里如实标出来，让界面提示重新采集，
    # 而不是让它悄悄表现为「这条通知没有表格」。
    stale = not tables and needs_recollect(text)
    return {
        "tables": tables,
        "colleges": colleges[:12],
        "majors": majors[:12],
        "phones": phones[:12],
        "stats": stats,
        "needs_recollect": stale,
        "degraded": stale,
        "reason": ("正文像是从表格压平来的，列边界已丢失且无法复原，需要重新采集这条通知"
                   if stale else ""),
    }


NOISE_COLLEGE = (
    "学院名称", "学院", "单位名称", "序号", "合计", "总计", "小计", "备注",
    "联系人", "联系电话", "名称",
)
NOISE_MAJOR = (
    "专业名称", "专业代码", "专业", "学科", "名称", "代码", "合计", "总计",
    "备注", "研究方向", "考试科目",
)


def _looks_college(value: str) -> bool:
    """学院名必须有「学院/学部/系」后缀且不是表头本身。"""
    v = (value or "").strip()
    if not v or len(v) > 30:
        return False
    if v in NOISE_COLLEGE:
        return False
    return v.endswith(("学院", "学部", "系", "研究院", "中心"))


def _looks_major(value: str) -> bool:
    """专业名：2-30 字中文/字母，且不是表头、不是纯数字、不是纯代码。"""
    v = (value or "").strip()
    if not v or len(v) > 30:
        return False
    if v in NOISE_MAJOR:
        return False
    if re.fullmatch(r"[\d\W_]+", v):
        return False
    return bool(re.search(r"[\u4e00-\u9fa5A-Za-z]", v))


# ---------------------------------------------------------------- 存量正文识别
# 旧版采集器已经把表格压平入库，列边界丢了，且**不可复原**（见模块 docstring）。
# 这里不做复原，只做一件能站得住的事：把这种正文认出来，告诉用户要重新采集。
#
# 判据是「正文里同时出现表格才会有的三种东西，但一处列分隔符都没有」：
# 六位专业代码、以「学院」结尾的单位名、带区号的电话。三者同现而 `\x1f`
# 缺席，说明这张表被压平了——新版采集器不会产出这种正文。
_CODE_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")
_COLLEGE_HINT = re.compile(r"[\u4e00-\u9fa5]{2,12}学院")
# 电话前面不能加 (?<!\d) 断言：压平之后电话常常正前一位就是数字
# （`085600材料与化工1027-87164677` 里 `027-87164677` 前面紧挨着 `1`），
# 加了断言恰好漏掉最该识别的这一种。前面允许粘连，只要求末尾不是数字。
_PHONE_HINT = re.compile(r"0\d{2,3}-\d{7,8}(?!\d)")


def needs_recollect(content: str) -> bool:
    """正文像是从表格压平来的、却没有列边界 —— 需要重新采集才能复原。

    只在「三种表格特征同时出现且无 CELL_SEP」时返回 True。单个特征太容易
    误伤：正文里提到一个电话、一处学院名，或引用一个六位编号，都很常见。
    """
    text = content or ""
    if CELL_SEP in text:
        return False
    return bool(_CODE_RE.search(text)
                and _COLLEGE_HINT.search(text)
                and _PHONE_HINT.search(text))


def describe() -> dict:
    return {
        "capability": "表格结构复原",
        "strategy": "采集期按单元格序列化保留列边界，解析期为每列打语义标签，逐格回原文接地",
        "cell_sep": "\\x1f",
        "max_tables": MAX_TABLES,
        "col_kinds": [k for k, _ in COL_RULES],
        # 存量压平正文不可复原，只做识别后提示重新采集——这条写在这里，
        # 是为了别人读 describe() 时不会以为「旧数据也能救」。
        "stale_policy": "旧版压平的存量正文列边界不可复原，仅标记 needs_recollect 提示重新采集",
    }
