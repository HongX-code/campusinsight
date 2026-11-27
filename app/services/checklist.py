"""办理清单拆解（Capability: Actionable Checklist Decomposition）。

## 为什么「材料清单」不够

现有解析结果里的 `materials` 是一个名词数组：["报名表", "成绩单", "推荐信"]。
对已经知道流程的人是够的，对第一次办事的学生不够——他还需要知道：

* 先做什么（成绩单要先去教务处盖章，才能随报名表一起交）
* 哪些必须线下跑（盖章、签字、体检）
* 哪些有前置依赖（没有成绩单，报名表交上去也会被退回）

所以这里把名词清单升级为「带顺序的动词短语 + 线上/线下标记 + 前置依赖」，
且每一步都保留原文依据，仍符合全站「宁缺勿造」的原则。

## 抽取策略：先规则，规则抽不到才让大模型补

规则优先的原因与全站一致——清单是「要照着做」的东西，幻觉的代价比抽取不全
高得多。规则覆盖不到时宁可少一条，也不凭空发明一个步骤。

线上/线下的判定用的是院校通知里稳定出现的动词：
    盖章/签字/提交纸质/送至/交至/体检/面试/领取原件 → 线下
    填报/上传/发送邮件/在线/系统提交 → 线上
"""
from __future__ import annotations

import re

from app.services.textutil import normalize_text, section_bodies, snippet

# 章节标题里可能承载「怎么办」的段落
_MATERIAL_KEYS = ("材料", "清单", "提交", "报送", "报名", "方式", "流程", "程序", "要求", "事项")

# 线下动作词：出现即认为该步骤必须到现场
_OFFLINE_WORDS = (
    "盖章", "签字", "签名", "纸质", "原件", "交至", "送至", "报送至", "递交",
    "现场", "体检", "面试", "面谈", "领取", "领取原件", "教务处", "学院办公室",
    "辅导员", "指导教师签字", "导师签字", "加盖公章", "密封",
)

# 线上动作词
_ONLINE_WORDS = (
    "上传", "在线", "系统提交", "网上", "邮件", "发送至", "邮箱", "填报",
    "在线填报", "平台提交", "扫码", "小程序", "问卷",
)

# 依赖关系：先 A 后 B 的语义词对（前置词, 后置词）
_DEPENDS = (
    ("成绩单", "报名表"),
    ("盖章", "提交"),
    ("签字", "提交"),
    ("推荐信", "提交"),
    ("体检", "提交"),
    ("审核", "提交"),
    ("资格审核", "面试"),
)

# 条目型正则：以编号 / 圆点开头的短句
# 注意序号既有阿拉伯数字也有中文数字——「（二）网上报名…」在公文里非常常见，
# 只写 \d 的版本会把「（二）」留在正文里，让步骤标题凭空多出一个序号。
_ITEM_RE = re.compile(
    r"^\s*(?:\d{1,2}[、.．)]|[（(][\d一二三四五六七八九十]{1,2}[）)]|[①-⑩]|[-·•*])\s*(.+)$"
)

# 动词开头才认为是「可执行的步骤」，纯名词短语（"报名表"）不算
_ACTION_START = re.compile(
    r"^(?:填写|填报|提交|上传|下载|打印|准备|准备齐全|携带|盖章|签字|领取|缴纳|缴费|"
    r"发送|寄送|递交|报送|交至|送至|登录|登陆|注册|报名|参加|完成|确认|联系|咨询|扫描|"
    r"复印|装订|密封|体检|面试|参加面试|线上|线下|通过|进入|打开|访问)"
)

# 句中出现这些强动作词，即使不以动词开头也值得作为步骤
_ACTION_ANY = (
    "登录", "登陆", "提交", "上传", "填写", "填报", "发送至", "交至", "送至",
    "盖章", "签字", "打印", "下载", "报名", "缴费", "领取", "扫描", "确认",
)

# 这些行是联系方式/声明/附件目录/时间说明，不是「要去做的事」
_NOT_STEP_PREFIX = (
    "联系人", "联系电话", "联系邮箱", "联系方式", "咨询电话", "地址", "邮编",
    "特此通知", "特此公告", "附件", "附：", "备注", "说明", "以上", "本通知",
    "请各单位", "各单位要", "各单位请", "鼓励", "倡导", "希望",
)
_NOT_STEP_CONTAINS = (
    "联系人：", "联系电话：", "邮箱：", "未尽事宜", "如有疑问",
    # 「...时间为 2024年12月17日-22日」是客观陈述，不是动作指令
    "时间为", "日期为", "截止时间为", "截止时间：", "系统关闭后", "逾期不再", "逾期视为",
    # 公文里的「保证真实」「一经发现取消资格」是声明条款，学生没有对应动作
    "必须保证", "一经发现", "凡提供虚假", "按照…执行", "按照有关要求执行",
)

# 否定句描述的是「哪些人可以不做什么」（免提交、无需盖章），不是要去做的动作。
# 漏掉这一条，清单里就会出现「1. 重新评阅的申请人无需提交答辩意向申请」这种
# 反向动作——学生照做反而少交了材料。
_NEGATION_WORDS = ("无需", "不需", "不必", "免于", "不再需要", "不再", "可不")

# 纯政策性陈述：「按照教育部和湖北省有关要求执行」这类句子里有「执行」，
# 但它规定的是谁按什么办，学生没有可执行的动作。
_STATEMENT_PAIRS = (("按照", "执行"), ("依据", "执行"), ("参照", "执行"))

# 附件/文档名（以这些后缀结尾且不含动词的，是材料而不是步骤）
_DOC_SUFFIX = ("表", "指南", "模板", "汇总表", "申报表", "证明", "附件", "办法", "细则", "清单")

# 「准备XX办法/细则/条例」里的 XX 其实是参考文件，不是要去准备的东西
_DOC_ONLY_SUFFIX = ("办法", "细则", "条例", "规定", "指南", "法", "章程", "规范")

_MAX_STEPS = 12
_MAX_TITLE = 60


def _is_offline(text: str) -> bool:
    return any(w in text for w in _OFFLINE_WORDS)


def _is_online(text: str) -> bool:
    return any(w in text for w in _ONLINE_WORDS)


def _channel(text: str) -> str:
    """判断办理渠道：线下优先（只要出现盖章/纸质这类词就必然是线下）。"""
    if _is_offline(text):
        return "offline"
    if _is_online(text):
        return "online"
    return "unknown"


def _is_step(text: str) -> bool:
    """判断一句到底是不是「要去做的事」。

    真实通知里混着大量非动作行：联系人电话、附件目录、免责声明。
    把它们当成办理步骤会直接毁掉清单的可信度——学生看到「1. 联系电话：027-xxx」
    就再也不会相信后面几步了。宁可少一条，也不能错一条。
    """
    text = (text or "").strip()
    if not (4 <= len(text) <= _MAX_TITLE):
        return False

    if text.startswith(_NOT_STEP_PREFIX):
        return False
    if any(k in text for k in _NOT_STEP_CONTAINS):
        return False
    if any(w in text for w in _NEGATION_WORDS):
        return False
    if any(a in text and b in text for a, b in _STATEMENT_PAIRS):
        return False

    has_action = bool(_ACTION_START.match(text)) or any(w in text for w in _ACTION_ANY)
    if not has_action:
        # 纯文档名（以「表」「指南」结尾）是材料，交给 materials 分支处理
        return False

    if _prepare_is_reference(text):
        return False

    # 「附件2：报名表」这类行虽有「报名」二字，但本质是附件目录
    if text.startswith("附件") or (len(text) < 12 and text.endswith(_DOC_SUFFIX)):
        return False
    return True


def _dedupe_key(text: str) -> str:
    """去重键：去掉标点、空白与常见异写后比较。

    「登录xx首页」和「登陆xx首页」只差一个字，语义完全相同，必须合并，
    否则清单里会出现两条看起来一样但编号不同的步骤。
    这里还顺带掐掉「学生申请人」这类主语前缀——同一句话换个人称说是同一件事。
    """
    key = re.sub(r"[\s\u3000，,。、；;：:！!？?（）()《》“”\"'【】\[\]—\-·…]+", "", text)
    for a, b in (("登陆", "登录"), ("填报", "填写"), ("报送", "提交"), ("递交", "提交")):
        key = key.replace(a, b)
    for prefix in ("学生申请人", "申请人", "学生本人", "各学院", "各培养单位", "各单位"):
        key = key.replace(prefix, "")
    return key


def _prepare_is_reference(text: str) -> bool:
    """判断「准备XX」里的 XX 是不是只是参考文件（办法/细则/条例），而非需备材料。

    「准备武汉理工大学研究生助学金管理办法」这句是废话——学生不需要"准备"一
    份校规，他只是要参照它。真正要准备的是报名表、成绩单这类实体材料。
    """
    if not text.startswith("准备"):
        return False
    name = text[2:].strip()
    return any(name.endswith(suffix) for suffix in _DOC_ONLY_SUFFIX)


# 材料名里可能混进来的动词前缀：上游抽到的是「形成自评材料」「审核相关材料」
# 这类短语，直接接在「准备」后面会变成「准备形成自评材料」这种不像人话的步骤。
_MATERIAL_VERB_PREFIX = (
    "形成", "审核", "组织", "参加", "提交", "报送", "填写", "提供", "出具",
    "整理", "准备", "完成", "携带",
)

# 材料名前面挂的主语：「各学院审核相关材料」里的「各学院」是执行者不是材料的一部分
_MATERIAL_SUBJECT_PREFIX = ("各学院", "各培养单位", "各单位", "学院", "学生", "申请人")

# 材料名的合法结尾。上游把「考试大纲汇总」里的科目名（材料科学基础、高分子化学）
# 也抽成了材料，接上「准备」就变成「准备高分子化学」这种莫名其妙的待办。
# 材料一定是可递交的实体：表格、证件、证明、文书、方案、作品。
_MATERIAL_SUFFIX_OK = (
    "表", "证", "书", "单", "册", "卡", "照", "件", "明", "簿", "信",
    "方案", "作品", "简历", "论文", "报告", "材料", "模板", "协议", "合同",
    "情况说明", "推荐函", "档案", "回执",
)


def _clean_material_name(name: str) -> str:
    """把材料名里的动词前缀与主语剥掉，只留实体名称。

    「形成自评材料」→「自评材料」，「各学院审核相关材料」→「相关材料」。
    剥完若只剩「材料」「相关材料」这种没有信息量的词，返回空串交给调用方跳过
    ——清单里写「准备相关材料」等于没写。
    最后还要过一次「像不像可递交的实体」的判据：科目名（高分子化学）、
    信息项（身份证号码）都不是要去准备的件。
    """
    cleaned = name.strip(" 。；;，,")
    changed = True
    while changed:
        changed = False
        for prefix in _MATERIAL_SUBJECT_PREFIX:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip(" 的、，,")
                changed = True
        for prefix in _MATERIAL_VERB_PREFIX:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip(" 的、，,")
                changed = True
    if cleaned in ("", "材料", "相关材料", "有关材料", "证明材料"):
        return ""
    if any(w in cleaned for w in _NEGATION_WORDS):
        return ""
    # 信息项不是材料：「身份证号码」「联系方式」这类抽出来照着准备毫无意义
    if cleaned.endswith(("号码", "信息", "时间", "地点", "方式", "要求")):
        return ""
    if not cleaned.endswith(_MATERIAL_SUFFIX_OK):
        return ""
    return cleaned


def _candidates(content: str) -> list[tuple[str, str]]:
    """从材料/流程相关章节里摘出候选步骤，返回 [(步骤文本, 原文片段)]。"""
    bodies = section_bodies(content, _MATERIAL_KEYS)
    if not bodies:
        bodies = [content]

    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    for body in bodies:
        for raw in body.split("\n"):
            line = raw.strip()
            if not line:
                continue

            # 情况一：编号条目，如「1. 填写《申报表》并加盖学院公章」
            m = _ITEM_RE.match(line)
            if m:
                text = m.group(1).strip(" 。；;，,")
                if not _is_step(text):
                    continue
                key = _dedupe_key(text)
                if key in seen:
                    continue
                seen.add(key)
                start = body.find(line)
                out.append((text, snippet(body, start, start + len(line))))
                continue

            # 情况二：无编号但确实是可执行动作的短句
            text = line.strip(" 。；;，,")
            if not _is_step(text):
                continue
            key = _dedupe_key(text)
            if key in seen:
                continue
            seen.add(key)
            start = body.find(line)
            out.append((text, snippet(body, start, start + len(line))))

    return out[:_MAX_STEPS]


def _depends_on(text: str, earlier: list[str]) -> int:
    """判断当前步骤依赖前面的第几步（1-based），无明确依赖返回 0。

    只在「前置词出现在更早的步骤里、且当前步骤是后置词」时才建立依赖，
    避免把所有步骤串成一条必然的链——那样清单就失去并行办理的信息。
    """
    for pre, post in _DEPENDS:
        if post not in text:
            continue
        for idx, prev in enumerate(earlier, start=1):
            if pre in prev:
                return idx
    return 0


def build(notice: dict) -> dict:
    """生成办理清单。

    入参 notice 为 notice_to_dict 的结果（含 analysis）。
    返回 {steps, stats, degraded, reason}；steps 为空是合法结果（新闻资讯/无办理事项）。
    """
    analysis = notice.get("analysis") or {}
    content = notice.get("content") or ""

    if not analysis:
        return {"steps": [], "stats": _stats([]), "degraded": True, "reason": "该通知尚未解析"}

    if not analysis.get("actionable", True):
        return {"steps": [], "stats": _stats([]), "degraded": False,
                "reason": "新闻资讯类内容无可办理事项，不生成清单"}

    cands = _candidates(content)
    steps: list[dict] = []
    earlier_texts: list[str] = []

    # 报名截止是整张清单的时间锚点，单独作为最后一步「在 X 前完成提交」
    deadline = analysis.get("deadline") or ""

    for text, evidence in cands:
        channel = _channel(text)
        steps.append({
            "seq": len(steps) + 1,
            "title": text,
            "detail": "",
            "offline": channel == "offline",
            "depends_on": _depends_on(text, earlier_texts),
            "evidence": evidence,
            "source": "text",
        })
        earlier_texts.append(text)

    # 材料清单里没被任何步骤覆盖到的，补成「准备 X」步骤（这仍是从原文来的，不是发明）
    covered = " ".join(s["title"] for s in steps)
    for material in (analysis.get("materials") or [])[:8]:
        name = _clean_material_name(str(material))
        if not name or name in covered:
            continue
        # 校规条例类材料是「参照执行」而不是「准备上交」，不列入待办
        if _prepare_is_reference("准备" + name):
            continue
        title = f"准备{name}"
        if _dedupe_key(title) in {_dedupe_key(s["title"]) for s in steps}:
            continue
        steps.append({
            "seq": len(steps) + 1,
            "title": title,
            "detail": "报名材料",
            "offline": _is_offline(name),
            "depends_on": 0,
            "evidence": (analysis.get("evidence") or {}).get("materials", ""),
            "source": "material",
        })
        covered += " " + name

    if deadline or analysis.get("timeline"):
        for step in _deadline_steps(analysis, deadline, len(steps)):
            steps.append(step)

    return {
        "steps": steps,
        "stats": _stats(steps),
        "degraded": not steps,
        "reason": "" if steps else "原文未发现可拆解的办理步骤（可能为纯资讯或表述过于笼统）",
    }


def _deadline_steps(analysis: dict, deadline: str, offset: int) -> list[dict]:
    """截止锚点步骤。

    有单值 `deadline` 就一定能给出一个锚点，但只给单值是有问题的：像学位授予这类
    通知里，秋季与冬季各自有一整套截止日期，单值只能挑中其中一个，学生会照着
    别人的日期办事。所以只要多轨道时间轴里有学生侧节点，就按轨道各给一个锚点，
    并把身份写在标题里——「这条是博士的、那条是硕士的」比一个笼统的日期有用。
    """
    nodes = [n for n in (analysis.get("timeline") or []) if n.get("scope") == "student"]
    per_track: list[dict] = []
    seen: set[str] = set()
    for node in sorted(nodes, key=lambda n: (n.get("date") or "", n.get("track") or "")):
        track = node.get("track") or ""
        if track in seen:
            continue
        seen.add(track)
        per_track.append(node)
        if len(per_track) >= 3:  # 再多学生也看不过来，三条轨道足够
            break

    kind = analysis.get("deadline_kind") or "报名"
    evidence = (analysis.get("evidence") or {}).get("deadline", "")

    if not per_track:
        if not deadline:
            return []
        return [{
            "seq": offset + 1,
            "title": f"在 {deadline} 前完成提交",
            "detail": f"截止类型：{kind}",
            "offline": False,
            "depends_on": offset,
            "evidence": evidence,
            "source": "deadline",
        }]

    steps: list[dict] = []
    for idx, node in enumerate(per_track):
        track = node.get("track") or ""
        role = node.get("role") or "学生"
        action = node.get("action") or "完成提交"
        mark = f"{track} · {role}" if track else role
        steps.append({
            "seq": offset + idx + 1,
            "title": f"在 {node.get('date')} 前完成提交（{mark}）：{action}",
            "detail": f"截止类型：{kind} · 轨道：{track or '未标注'} · 适用身份：{role}",
            "offline": False,
            "depends_on": offset,
            "evidence": node.get("evidence") or evidence,
            "source": "deadline",
        })
    return steps


def _stats(steps: list[dict]) -> dict:
    return {
        "total": len(steps),
        "offline": sum(1 for s in steps if s.get("offline")),
        "with_dependency": sum(1 for s in steps if s.get("depends_on")),
    }


def describe() -> dict:
    return {
        "capability": "办理清单拆解",
        "strategy": "规则抽取编号条目与办理动词开头的短句，再补材料清单，最后挂截止锚点",
        "offline_words": len(_OFFLINE_WORDS),
        "online_words": len(_ONLINE_WORDS),
        "max_steps": _MAX_STEPS,
    }
