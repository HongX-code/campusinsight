"""多轨道截止时间轴（Capability: Multi-track Deadline Timeline）。

## 单值 deadline 装不下真实通知

全站原来的时间字段只有一个 `deadline`，学生会看到「截止 2026-10-08」。
但真实的学位授予通知是这样写的：

    7月2日（秋季）和10月8日（冬季）前，博士学位申请人预答辩截止。
    7月7日（秋季）和10月10日（冬季）前，有申请意向的硕士学位申请人须提交答辩意向申请。
    7月6日（秋季）和10月10日（冬季），博士学位论文"答辩前检测"截止；
    7月8日（秋季）和10月12日（冬季），硕士学位论文"答辩前检测"截止。

一条通知里有两条并行时间轴（秋季/冬季）、十几个办理节点、每类节点还绑不同身份。
把 14 个节点压成一个 `deadline` 之后出了两个错：

1. **日期是错的**——单值挑选器按上下文分数取最高分，抽中的是「博士预答辩 10-08」，
   硕士生看到的却是博士生的日期，他真正的答辩意向截止是 10-10。
2. **秋季整条轨道消失**——7 月的 7 个节点全部丢失，等学生发现时已经过期。

这不是抽取算法不准，是**数据结构装不下**：`deadline: str` 只能放一个值，
而事实本身是 (轨道 × 身份 × 事项) 的三维网格。所以本模块把字段升级为节点列表。

## 绑定规则：一条日期必须说清楚「谁的、哪件事、哪条轨道」

裸日期是有歧义的，三个限定词缺一不可：

* **轨道**：`（秋季）`/`（冬季）` 这类括号标记，紧跟在日期后面；
* **身份**：节点所在分句里出现的「博士/硕士/学院」，决定这个节点该推给谁；
* **事项**：日期之后那个办理动作，决定提醒文案写什么。

抽不到事项的日期一律丢弃（宁缺勿滥，与全站一致）——宁可少一个节点，
也不给出一个学生看不出要干什么的裸日期。

## 两种节点：学生节点与管理节点

同一份通知里混排着「学生要做什么」和「学院要做什么」：

    10月8日（冬季），各学院学位分会学位授予审议截止

把管理节点推给学生会造成虚假紧迫感，所以节点带 `scope` 字段区分
`student` / `admin`，个人时间轴只取 `student`。
"""
from __future__ import annotations

import datetime as dt
import re

from app.services.tables import CELL_SEP
from app.services.textutil import find_dates, infer_year, normalize_text

# 轨道关键词，按公文里最常见的学期命名；只在紧邻日期的括号里才算标记，
# 避免把正文里的「秋季学期」误当成日期轨道。
TRACKS = ("秋季", "冬季", "春季", "夏季")
_TRACK_RE = re.compile(r"[（(]\s*(秋季|冬季|春季|夏季)\s*[)）]")

# 身份归一化：顺序敏感（先博士再硕士，最后才是本专科）
_ROLE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("博士研究生", ("博士",)),
    ("硕士研究生", ("硕士",)),
    ("本科生", ("本科",)),
    ("专科生", ("专科", "高职")),
)

# 出现这些词说明节点是「管理侧要做的」，不是学生要去办的
# 「培养处」「学位办」与「培养办」「学位办公室」是同一类机构的不同写法，
# 表格里「完成单位」一列常常只写「培养处」。漏了它们，一条「培养处下发…
# 答辩登记表」会被当成学生自己的待办，推给学生一条他办不了的事。
_ADMIN_WORDS = ("各学院", "学院学位分会", "培养办", "培养处", "研究生院",
                "学位办公室", "学位办", "校学位办", "导师")

# 管理节点常见的主语是机构名，后面跟着「根据…情况，确定…」这类从属小句，
# 真正的动作在逗号之后。不剥掉的话节点会变成「研究生院学位办公室根据系统
# 答辩意向提交情况」，学生看不出这条要办什么。
_ORG_PREFIX = (
    "研究生院学位办公室", "研究生院培养办公室", "研究生院", "学位办公室",
    "各学院学位分会", "学院学位分会", "各培养单位", "各学院", "学院",
)
_SUBORDINATE_HEAD = ("根据", "依据", "按照", "参照", "结合")

# 动作串里要剥掉的限定语：剥完剩下的才是「干什么」
_NOISE_PREFIX = (
    "有申请学位意向的", "有申请意向的", "有申请意愿的", "被抽查评阅的", "被抽查的",
    "需要重新外送评阅的", "重新送专家评阅的", "各类型", "各类", "所有", "相关", "本学院",
    "各学院", "各培养单位", "各", "并", "同时", "须", "需", "应", "要",
    "在系统中", "在系统", "在", "根据系统", "其", "按时", "及时",
)

# 兜底动作词：分句里既没有「截止」也没有「前」时（"各学院报送…名单至…"），
# 只有出现这些办理动词才仍然算一个节点，否则是纯叙述。
# 这份表同时充当「动作准入」：抽出来的串里没有动词，就不是学生能执行的一件事。
_ACTION_VERBS = (
    "报送", "提交", "申请", "审核", "审议", "答辩", "检测", "登记", "评阅",
    "盖章", "打印", "公告", "公布", "反馈", "确认", "领取", "缴费",
    "登录", "邮寄", "寄至", "送交", "签订", "填写", "发送", "上报", "汇总",
    "反映", "参加", "报名", "下载", "上传", "办理", "完成", "交纳", "缴纳",
    "核对", "填报", "递交", "寄送", "提交至", "备案", "注册", "选课", "退宿",
    # 进度表类通知里高频、但公文正文里不常出现的动词。
    # 漏了它们，表格里「各学院上交学位材料至学位办」这种真节点会被当成叙述丢掉。
    "上交", "下发", "返回", "开会", "受理", "送审", "报送至", "汇总至",
)

_MAX_ACTION = 44
_MAX_NODES = 40
# 单元格分隔符也算分句边界。
# 表格行（`序号\x1f截止日期\x1f工作内容\x1f完成单位\x1f星期\x1f周次`）里
# 真正的动作只在「工作内容」这一格，若不在这里截断，动作串会一路吞到行尾，
# 变成「各学院返回毕业答辩登记表至培养处各学院星期三第二周」——把星期和周次
# 当成办理事项的一部分推给学生。普通正文里没有 \x1f，加进来不影响任何既有行为。
_CLAUSE_SEP = ("，", ",", "；", "。", "、", CELL_SEP)


# 不是动作的收尾：这些串结尾说明抽到的是「某一类人」而不是「要做的事」。
# `已经提交送审版论文的学位申请人` 里有「提交」二字，但整句是主语不是动作。
_NOT_ACTION_TAIL = ("的学位申请人", "的申请人", "的研究生", "的学生", "的人员", "者", "情况")
# 附件引用、跳转指引不是可执行动作，学生照着做不了任何事
_NOT_ACTION_HEAD = ("详见", "参见", "见附件", "另见", "具体见")


def _has_verb(text: str) -> bool:
    """动作串里必须真的有一个办理动词。

    没有动词的片段是被日期切出来的残句（`含民族`、`组织聘用的助管`），
    它们不是「学生要办的事」，留在时间轴上只会让整条轴显得像机器切的。
    """
    return any(v in text for v in _ACTION_VERBS)


def _clean_action(raw: str) -> str:
    """把分句片段压成一句可读的「办什么事」。"""
    text = _TRACK_RE.sub("", raw or "")
    # 引号要保留：`"答辩前检测"` 是事项名的一部分，strip 掉会变成半个引号
    text = text.strip(" \t，,。；;：:、（）()")
    # 「…（冬季）前，X 截止」里开头的「前」属于日期条件，不是动作的一部分
    if text.startswith("前"):
        text = text[1:].lstrip(" \t，,。；;：:、")

    # 「X须/需/应在系统提交Y」里 X 是主语、须/需/应 之后才是动作。
    # 主语拨给身份判定，动作只留动词短语。
    for marker in ("须", "需", "应", "要"):
        pos = text.find(marker)
        if pos > 0:
            text = text[pos + 1:]
            break

    changed = True
    while changed:
        changed = False
        for prefix in _NOISE_PREFIX:
            if text.startswith(prefix):
                text = text[len(prefix):].lstrip(" 的、，,")
                changed = True

    # 「博士学位申请人预答辩」→「预答辩」：身份已经单独存在 role 字段里，
    # 动作串再重复一遍会让时间轴读起来像公文摘抄。
    text = re.sub(r"^(?:博士|硕士|本科|专科)(?:研究生)?(?:学位)?申请人(?:（含留学生）)?", "", text)
    text = re.sub(r"^学位申请人", "", text)

    # 「…后研究生院培养办公室下发…」里的「后」属于上一句的时间条件，不是动作
    text = re.sub(r"^(?:之)?后(?=[\u4e00-\u9fa5])", "", text)

    text = re.sub(r"\s+", "", text)
    if len(text) > _MAX_ACTION:
        text = text[:_MAX_ACTION]
    # 截断可能切在括号里，留下半个「（附件4」；括号不配对就回退到括号前
    if text.count("（") != text.count("）"):
        pos = text.rfind("（")
        if pos > 0:
            text = text[:pos]
    return text.strip("，,。；;：:、")


def _trim_clause(text: str) -> str:
    """截出「一个动作」，但要跳过从属小句。

    `研究生院学位办公室根据系统答辩意向提交情况，确定…名单并公布`
    里真正的动作在逗号之后；直接按逗号截会只剩机构名和条件。
    只有逗号后面确实出现办理动词时才跳过，避免把并列事项切丢。
    """
    head = _cut(text)
    rest = text[len(head):]
    if any(w in head for w in _SUBORDINATE_HEAD) and rest:
        tail = rest.lstrip("，,、；; ")
        if tail and any(v in tail for v in _ACTION_VERBS):
            return _cut(tail)
    return head


_STUDENT_WORDS = ("研究生", "学生", "同学")


def _role_of_table(action: str, unit: str) -> tuple[str, str]:
    """表格行的身份判定：先看「工作内容」里写明的执行者，再看「完成单位」列。

    进度表类通知里，「完成单位」列常写「各学院、各研究生」这种混合值，
    但「工作内容」那一格会写明是谁动手（「研究生在系统里上传送审论文」）。
    如果只看完成单位，学生自己的事会被判成学院办理，个人时间轴里就消失了。
    反过来，动作里出现机构名（「培养处下发…」）仍按管理侧处理。
    """
    act = action or ""
    if not any(w in act for w in _ADMIN_WORDS) and any(w in act for w in _STUDENT_WORDS):
        for name, keys in _ROLE_RULES:
            if any(k in act for k in keys):
                return name, "student"
        return "学生", "student"
    return _role_of(unit, act)


def _role_of(subject: str, action: str) -> tuple[str, str]:
    """判定 (身份, 范围)。

    先看动作后的主语片段（分句去掉日期后的开头），再看动作本身。
    管理侧优先：一条节点里同时出现「学院」和「硕士」时，执行者是学院，
    学生只是被审议对象，不能推给学生。
    """
    head = subject[:40]
    if any(w in head for w in _ADMIN_WORDS):
        return "学院", "admin"
    for name, keys in _ROLE_RULES:
        if any(k in head or k in action for k in keys):
            return name, "student"
    if any(w in head for w in ("全体", "全校", "同学", "学生")):
        return "学生", "student"
    return "学生", "student"


def _split_clauses(text: str) -> list[str]:
    """按分号/句号/换行切分句，保留每句内部的日期并列结构。

    不能按逗号切——「7月2日（秋季）和10月8日（冬季）前，博士…」里的逗号
    是日期与事项的分界，切掉就拿不到事项了。
    """
    out: list[str] = []
    for piece in re.split(r"[；;。\n\r]+", text or ""):
        if piece.strip():
            out.append(piece)
    return out


def _cut(text: str) -> str:
    cut = len(text)
    for sep in _CLAUSE_SEP:
        idx = text.find(sep)
        if idx != -1:
            cut = min(cut, idx)
    return text[:cut]


def _table_cell_action(seg: str, dates: list[dict]) -> str:
    """表格行：动作只取「截止日期」右边那一格，且只取第一件事。

    表格行和自然句的分句方式不一样。`序号\x1f截止日期\x1f工作内容\x1f完成单位\x1f
    星期\x1f周次` 里，只有「工作内容」那格是学生要办的事；右边几格是执行单位与
    校历（「星期五」「第二周」），左边是序号。若把整行当一个句子去截，既会把
    星期和周次当进动作里，也会因为格内出现顿号而截断在句子中间
    （「各类型博士、硕士办理答辩手续」会被截成「各类型博士」）。

    同一格里常有「①… ②… ③…」并列多件事，只取第一件：一条通知的一个日期
    对应一整套动作时，把三件事压成一句读起来是乱的，只留第一件更接近
    「这个日期之前必须开始做什么」。
    """
    cells = [c.strip() for c in seg.split(CELL_SEP)]
    if len(cells) < 2:
        return ""
    raw = seg[dates[-1]["start"]:dates[-1]["end"]]
    idx = -1
    for i, cell in enumerate(cells):
        if raw and raw in cell:
            idx = i
            break
    if idx < 0 or idx + 1 >= len(cells):
        return ""
    body = cells[idx + 1]
    if not body:
        return ""
    # 「截止」在这格里时，动作在它前面
    if "截止" in body:
        body = body[: body.index("截止")]
    for part in re.split(r"[①②③④⑤⑥⑦⑧⑨⑩]", body):
        piece = _clean_action(part)
        if piece and _has_verb(piece):
            return piece
    return ""


def _action_from(seg: str, dates: list[dict]) -> tuple[str, str]:
    """取一个分句里的 (办理事项, 主语片段)。

    公文有三种写法，都要覆盖：
    * 「…前，X 截止」——动作在 `截止` 前面；
    * 「…前，X 须提交 Y」——没有「截止」二字，动作在 `前，` 后面；
    * 「…，各学院报送…名单至…」——既无截止也无前，靠办理动词兜底。
    """
    if not dates:
        return "", ""
    if CELL_SEP in seg:
        return _table_cell_action(seg, dates), seg[dates[-1]["end"]:].strip(CELL_SEP + " \t，,。；;：:、")
    # 日期后面紧跟的就是单元格分隔符时（表格行的「截止日期」列之后），
    # 先把它剥掉再取主语，否则 _cut() 会在第 0 个字符处就截断，动作变成空串。
    tail = seg[dates[-1]["end"]:].lstrip(CELL_SEP + " \t，,、")
    subject = tail.strip(" \t，,。；;：:、" + CELL_SEP)

    if "截止" in tail:
        return _clean_action(tail[: tail.index("截止")].strip(CELL_SEP + " \t，,、")), subject

    pos = tail.find("前")
    if pos != -1:
        rest = tail[pos + 1:].lstrip(" \t，,、" + CELL_SEP)
        return _clean_action(_trim_clause(rest)), subject

    head = _trim_clause(subject)
    if any(v in head for v in _ACTION_VERBS):
        return _clean_action(head), subject
    return "", subject


def _tracks_in(seg: str, count: int) -> list[str]:
    """按出现顺序取轨道标记，并补齐没有写标记的日期。

    「7月2日（秋季）和10月8日（冬季）」两个日期都带标记；
    也有写成「7月2日、10月8日（冬季）」的，此时按已出现的标记循环补齐。
    """
    pattern = [m.group(1) for m in _TRACK_RE.finditer(seg)]
    out: list[str] = []
    for idx in range(count):
        if not pattern:
            out.append("")
        elif idx < len(pattern):
            out.append(pattern[idx])
        else:
            out.append(pattern[idx % len(pattern)])
    return out


# 标题里的「YYYY年」是强证据：它由编辑提炼，且常常是全文唯一写明学年的地方。
# 页面上的发布时间标签却是采集来的元数据，可能出错——实测 2019 年秋季学位授予
# 通知的页面标签写着「发布时间：2020-09-04」，同一份通知的节点年份因此裂成
# 2019 与 2020 两截。标题年份与发布年份冲突时以标题为准。
_TITLE_YEAR_RE = re.compile(r"(20\d{2})\s*年")


def _title_year(title: str) -> int | None:
    m = _TITLE_YEAR_RE.search(title or "")
    return int(m.group(1)) if m else None


def extract(content: str, publish: str = "", title: str = "") -> dict:
    """从通知原文抽多轨道节点时间轴。

    返回 {nodes, tracks, roles, stats, degraded, reason}；nodes 为空是合法结果
    （新闻资讯、单节点通知都不需要时间轴）。每个节点都带原文依据，可追溯。

    `title` 只用来定年份锚：标题写了「2027年」而页面发布时间是 2026 年时，
    补出来的年份应当是 2027。标题没写年份就完全退回原来的 publish 逻辑。
    """
    text = normalize_text(content or "")
    publish = (publish or "")[:10]
    if not text:
        return {"nodes": [], "tracks": [], "roles": [], "stats": _stats([]),
                "degraded": True, "reason": "无正文"}

    ref_year = dt.date.today().year
    if publish:
        try:
            ref_year = dt.date.fromisoformat(publish).year
        except ValueError:
            pass

    # 标题年份与发布年份不一致时，以标题为准；两者一致则沿用原逻辑（零回归）
    year_base = publish
    tyear = _title_year(title)
    if tyear and tyear != ref_year:
        ref_year = tyear
        year_base = "%d-01-01" % tyear

    nodes: list[dict] = []
    for seg in _split_clauses(text):
        raw_dates = find_dates(seg, ref_year)
        if not raw_dates:
            continue
        # 与 extractor 保持一致：无年份的日期按发布时间补年份，跨年交给 infer_year
        dates = [
            {**d, "date": dt.date(infer_year(d["date"].month, year_base), d["date"].month, d["date"].day)}
            if not d["has_year"] else d
            for d in raw_dates
        ]
        action, subject = _action_from(seg, dates)
        if not action or not _has_verb(action):
            continue
        if action.startswith(_NOT_ACTION_HEAD) or action.endswith(_NOT_ACTION_TAIL):
            continue
        tracks = _tracks_in(seg, len(dates))
        if CELL_SEP in seg:
            role, scope = _role_of_table(action, subject)
        else:
            role, scope = _role_of(subject, action)
        for d, track in zip(dates, tracks):
            nodes.append({
                "track": track,
                "role": role,
                "scope": scope,
                "action": action,
                "date": d["date"].isoformat(),
                "evidence": seg.strip()[:120],
            })
        if len(nodes) >= _MAX_NODES:
            break

    # 同一天同一事项可能被分句边界重复切出，按 (日期, 事项) 去重
    uniq: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for node in nodes:
        key = (node["date"], node["action"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(node)

    uniq.sort(key=lambda n: (n["date"], n["track"], n["action"]))
    for idx, node in enumerate(uniq, start=1):
        node["seq"] = idx

    tracks = sorted({n["track"] for n in uniq if n["track"]}, key=TRACKS.index)
    roles = sorted({n["role"] for n in uniq})
    return {
        "nodes": uniq[:_MAX_NODES],
        "tracks": tracks,
        "roles": roles,
        "stats": _stats(uniq[:_MAX_NODES]),
        "degraded": False,
        "reason": "" if uniq else "原文未出现带截止语义的并列日期节点",
    }


def _stats(nodes: list[dict]) -> dict:
    students = [n for n in nodes if n.get("scope") == "student"]
    admins = [n for n in nodes if n.get("scope") == "admin"]
    dates = sorted({n["date"] for n in nodes})
    return {
        "total": len(nodes),
        "tracks": len({n["track"] for n in nodes if n.get("track")}),
        "roles": len({n["role"] for n in nodes}),
        "student": len(students),
        "admin": len(admins),
        "earliest": dates[0] if dates else "",
        "latest": dates[-1] if dates else "",
    }


def for_role(nodes: list[dict], role: str, track: str = "") -> list[dict]:
    """个人时间轴：只保留该身份的节点，可按轨道筛选。

    身份匹配用包含关系而不是相等：「博士研究生」的节点对「博士」用户可见，
    反之亦然；「学院」这类管理节点永远不出现在个人时间轴里。
    """
    out = []
    for node in nodes or []:
        if node.get("scope") != "student":
            continue
        node_role = node.get("role", "")
        if role and role not in node_role and node_role not in role:
            continue
        if track and node.get("track") != track:
            continue
        out.append(node)
    return out


def role_from_user(grade: str, major: str = "") -> str:
    """把用户画像里的年级归一到时间轴的身份词。

    用户画像存的是「研一/研二/博一/大三」这类在读阶段，时间轴上写的是
    「博士研究生/硕士研究生」。两者必须在同一套词汇里比对，否则
    `for_role` 永远匹配不上，个人时间轴会空着——学生会以为没有自己的事。
    """
    text = f"{grade or ''}{major or ''}"
    if any(k in text for k in ("博士", "博一", "博二", "博三", "博四", "博五")):
        return "博士研究生"
    if any(k in text for k in ("硕士", "研一", "研二", "研三")):
        return "硕士研究生"
    if any(k in text for k in ("本科", "大一", "大二", "大三", "大四", "大五")):
        return "本科生"
    if any(k in text for k in ("专科", "高职")):
        return "专科生"
    return ""


def describe() -> dict:
    return {
        "capability": "多轨道截止时间轴",
        "strategy": "按分句切出（轨道 × 身份 × 事项）三元组，日期与事项必须同源",
        "year_anchor": "标题写了年份就用标题年份，否则用发布时间；两者冲突时以标题为准",
        "tracks": list(TRACKS),
        "max_nodes": _MAX_NODES,
    }
