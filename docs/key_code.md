# 关键代码解读

初赛评审要求"关键代码片段的解读"。本文档按解析链路挑出多处真正决定系统效果的代码（第 8—11 节为本轮
v1.3 新增的结构化字段契约、相对时间、规则校验与硬条件匹配），说明**它解决什么问题、
为什么这样写、换一种写法会怎样**。所有片段均可在仓库中按文件路径索引到。

---

## 1. 采集：正文容器的"优先级 + 包含关系"双判

### 问题

校园 CMS 的正文容器 class 名五花八门（`#art_content` / `.TRS_Editor` / `.view` / `#vsb_content`），
而且**外层包装容器总是比正文长**——它把面包屑、「发布时间」、浏览量一起包进来。
常见写法"遍历所有 div 取文字最多的"在这里必错。

### 代码

`app/services/collector.py`

```python
def _pick_content(soup, cfg):
    ordered = list(cfg.selectors("content_selector")) if cfg else []
    ordered += [s for s in CONTENT_SELECTORS if s not in ordered]

    hits = []
    for selector in ordered:
        try:
            node = soup.select_one(selector)
        except Exception:            # 配置里的选择器写错，不能让整条通知解析失败
            continue
        if node is None or any(node is h for h in hits):
            continue
        hits.append((selector, node))

    # 命中的容器之间再做一次收敛：谁只是"外层包装"，就把谁丢掉
    kept = []
    for selector, node in hits:
        wrapped = any(
            node is not other and node in other.parents
            and text_len(other) < text_len(node) * 1.3
            for _, other in hits
        )
        if wrapped:
            continue
        kept.append((selector, node))

    for selector, node in kept:
        if text_len(node) >= 40:
            return node
        if selector in PRIMARY_SELECTORS:
            return node              # 主容器命中即采用，宁可空也不要噪声
    return kept[0][1] if kept else None
```

### 为什么这样写

- **顺序优先**：选择器表按"精确到宽泛"排列，配置里写的选择器排最前，站点改版时只改 JSON。
- **体量比 1.3**：外层容器只比正文多出 30% 以内的文字，说明多出来的是面包屑/发布时间这类边角料，取内层更干净；
  如果多出很多（超过 1.3 倍），说明内层可能只是正文的一个段落，此时保留外层更安全。
- **主容器命中即采用**：图片型通知（讲座海报）正文里只有一个 `<img>`，文字长度为 0。
  如果这时继续往下找，会命中外层 `.content`，把「首页 > 综合新闻 + 标题 + 发布时间」当成正文写进库。

### 反例

`max(candidates, key=lambda x: len(x.get_text()))`：在武汉理工研究生院页面上，
会选中 `div.content-detail`（1470 字）而不是 `#art_content`（1405 字），多出来的正是面包屑与发布时间。
更糟的是新闻站的 `div.content` 含浏览量，每次访问都变，`content_hash` 随之抖动，
同一条通知会被反复判为"有更新"——而这个错误在日志里完全看不到。

### 配套修复：数字碎片必须回粘

正文容器选对了，还有第二个坑：CMS 常把数字、单位、日期分别包进 `<span>`，
`get_text("\n")` 之后「2025」「年11月20日」成了两行，「2」「人」也成了两行。
清洗阶段只要有一条"短数字行是噪声"的规则，这些数字就被静默删掉，
原文变成「报名截止时间 2025年月日20:00」——截止日期整条消失。

```python
BLOCK_TAGS = ("p", "div", "tr", "li", "h1", …, "table", "section")

def _node_text(node) -> str:
    """块级换行、行内拼接：中文场景下 inline 元素之间不需要补空格。"""
    for br in node.find_all("br"):
        br.replace_with(NavigableString("\n"))
    for tag in node.find_all(BLOCK_TAGS):
        tag.insert_before(NavigableString("\n"))
        tag.insert_after(NavigableString("\n"))
    return node.get_text("")

def _merge_fragments(lines):
    """把被排版拆散的数字/字母碎片粘回相邻行。"""
    out, buf = [], ""
    for line in lines:
        if FRAGMENT_RE.match(line) and len(line) <= 6:
            buf += line
            continue
        if buf:
            line, buf = buf + line, ""
        out.append(line)
    return out
```

效果：正文数字不再残缺，截止日期恢复可抽取。真实集随后改用分文体口径衡量
（事务与公示类办理截止覆盖 20/24，新闻资讯误报 0/18，见 `docs/real_audit.md`）。
这个 bug 在构造样本上永远测不出来，只有接入真实网页才会暴露——这也是我们坚持把
真实采集链路做通、而不是只用手写样本演示的原因。

---

## 2. 截止日期：上下文打分 + 行内窗口

### 问题

一份通知里通常有三到五个日期：发布日、报名开始、报名截止、举办日、公示日。
"取第一个日期"或"取最后一个日期"都会错，而截止日期错了，后面所有的时间提醒和匹配都是错的。
这是这类系统最容易翻车的地方，也是最能体现工程含量的地方。

### 代码

`app/services/extractor.py`

```python
DEADLINE_POS = [("报名截止", 6), ("截止", 4.5), ("截至", 4.5), ("前提交", 4), ("报名", 2), ...]
DEADLINE_NEG = [("发布时间", -8), ("印发", -5), ("举行", -5), ("举办", -5), ("公示", -1), ...]
RANGE_END    = [("至", 5), ("到", 4), ("—", 4), ("～", 5), ("~", 5)]

# 范围结束符只在「办理类时间范围」里才是截止线索
RANGE_ACTION = ("报名", "报考", "申报", "提交", "报送", "征集", "投稿", "填报", "受理", ...)
RANGE_BLOCK  = ("公示期", "公示时间", "活动期间", "会期", "赛期", "新闻网讯",
                "举行时间", "举办时间", "会议时间", "考试时间", ...)


def _line_span(text, start, end, left_max=32, right_max=24):
    """取日期所在行的上下文范围。"""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    # 日期独占一行时（"五、截止时间\n2026年11月14日"），线索在上一行的标签里
    if not text[line_start:start].strip() and line_start > 0:
        prev_start = text.rfind("\n", 0, line_start - 1) + 1
        prev_line = text[prev_start: line_start].strip()
        if prev_line and len(prev_line) <= 24:
            line_start = prev_start
    return max(line_start, start - left_max), min(line_end, end + right_max)


def _pick_deadline(dates, text):
    scored = []
    for item in dates:
        lo, hi = _line_span(text, item["start"], item["end"])
        left, around = text[lo:item["start"]], text[lo:hi]
        score = _score_context(around, DEADLINE_POS)

        # 「X至Y」的 +5 分不是白给的：必须能在本行或上一行标签里找到办理动词，
        # 且不能是公示期 / 会期 / 活动期 / 新闻导语这类「不是报名时间」的范围。
        range_score = _score_context(left[-6:], RANGE_END)
        if range_score > 0:
            context = text[max(0, lo - 40):hi]      # 向前多取一行，覆盖独占行的标签
            if not any(k in context for k in RANGE_ACTION) \
               or any(k in context for k in RANGE_BLOCK):
                range_score = 0
        score += range_score

        score += _score_context(around, DEADLINE_NEG)
        if not item["has_year"]:
            score -= 0.5                     # 无年份的日期更可能是某个区间的一端
        scored.append((score, item["date"], item))
    scored.sort(key=lambda x: (x[0], x[1].toordinal()), reverse=True)
    best_score, _, best = scored[0]
    if best_score <= 0:
        return None
    evidence = snippet(text, best["start"], best["end"], pad=24, limit=120)
    return {"value": best["date"].isoformat(), "evidence": evidence,
            "kind": deadline_kind(evidence), "score": round(best_score, 2)}
```

### 为什么这样写

1. **打分而不是取第一个**：把"线索词 → 权重"显式列出来，命中"报名截止"加 6 分，命中"举办"减 5 分。
   这样一条通知里出现五个日期也不会乱，且规则可解释、可调、可写测试。
2. **区间结束符单独加分，但必须有办理语境**："2026年11月3日至11月20日"里两个日期都没有"截止"二字，
   靠 `RANGE_END` 区分起止。但真实站点上大量出现"公示期为X至Y""新闻网讯 10月30日至11月2日"
   "活动期间X至Y"，无条件 +5 分会把它们全部推成报名截止。现在要求该范围所在行（含上一行标签）
   出现报名/申报/提交/征集等办理动词，且不含公示期/会期/新闻导语等排除词，加分才生效；
   标签独占一行时语境向前多取一行，否则会把范围起始日错当成截止日。
   这一处修正让开发集截止日期准确率 **91.7% → 100%**、召回 90% → 100%，留出集保持 100%。
3. **窗口必须行内**：早期版本按固定 32 字符开窗，遇到
   `投稿截止：2026年12月20日 / 录用通知：2027年1月25日 / 终稿提交：2027年2月10日` 这种并列三行时，
   上一行的"截止"会渗到下一行，把"录用通知"判成截止日——这正是留出集 H02 暴露的缺陷。
   改成行内取上下文后，这一类的错误全部消失（开发集截止日期准确率 87.5% → 91.7%）。
4. **但行内太严**：有的通知把标签和日期拆成两行（`五、截止时间` 换行 `2026年11月14日24:00`）。
   所以补一条规则：日期独占一行时上溯一行，且只上溯短行（≤24 字，即标签行特征）。

### 反例

如果直接用正则 `(\d{4}年)?\d{1,2}月\d{1,2}日` 抓第一个日期，在这类通知上的准确率不到 40%——
因为大多数高校通知的第一句就是"现将有关事项通知如下"，第一个日期往往是发布日期。

---

## 3. 资格条件：把句子归一到"原子约束"

### 问题

资格条件在原文里长这样：

```
学习成绩排名与综合考评排名均位于本专业前10%，且无不及格科目；
个人或组合（不超过4人）均可；
经学校认定的家庭经济困难全日制本科生。
```

如果把逗号切分出的整句当作一条条件，会出现两个后果：**精确率低**（整句混着主语、修饰、并列，
和人工标注的原子条目对不上）、**不可判定**（匹配器没法回答"本专业前10%"到底满不满足）。

### 代码

`app/services/conditions.py`

```python
CANON_RULES = [
    # 人数区间：3—5人 / 3-5人 / 3至5人
    ("scale",   re.compile(r"(\d{1,2})\s*[—\-~～至]\s*(\d{1,2})\s*[名人]"), lambda m: f"{m.group(1)}—{m.group(2)}人"),
    # 人数上限：不超过4人
    ("scale",   re.compile(r"(?:不超过|不多于|最多|至多)\s*(\d{1,2})\s*[名人]"), lambda m: f"不超过{m.group(1)}人"),
    # 固定人数 + 组织形式：3名学生组队
    ("scale",   re.compile(r"(\d{1,2})\s*名\s*(?:学生|队员|同学)\s*(?:组队|组成|联合)?"), lambda m: f"{m.group(1)}名学生组队"),
    # 名额限制：每人限报一队
    ("quota",   re.compile(r"每[人名]\s*限(?:报|选|申报)?\s*([一二三四五六七八九十\d]+)\s*([队项个])"), lambda m: f"每人限报{m.group(1)}{m.group(2)}"),
    # 成绩排名：专业前10%
    ("score",   re.compile(r"(?:本)?专业\s*前\s*(\d{1,3})\s*[%％]"), lambda m: f"专业前{m.group(1)}%"),
    # 年级：二年级及以上
    ("grade",   re.compile(r"([一二三四])\s*年级\s*(及)?\s*以上"), lambda m: f"{m.group(1)}年级及以上" if m.group(2) else f"{m.group(1)}年级以上"),
    # 经济状况 / 品行 / 身体 / 服从性 / 师资 / 优先项
    ("finance", re.compile(r"(?:家庭)?经济困难"), lambda m: "家庭经济困难"),
    ("conduct", re.compile(r"无(?:违纪|纪律处分|处分)(?:记录|行为)?"), lambda m: "无违纪记录"),
    ...
]


def canonicalize(clause, strict=False):
    """把一条候选子句归一为若干原子约束，返回 [(kind, value)]。"""
    text = (clause or "").strip()

    if SUBMIT_RE.search(text):          # 签署《承诺书》属于材料流程，不是资格
        return []
    if re.search(r"(择优|推荐|名额|限额)", text):   # 选拔结果，不是门槛
        return []

    if "优先" in text:                  # 优先项只保留优先语义，不当硬门槛
        return [hit for kind, hit in _match(text) if kind == "priority"]

    hits = _match(text)
    if hits:
        return hits

    # 无规则命中的兜底：片段必须短、含约束性词汇，且（非条件章节时）带情态动词
    if len(text) > FALLBACK_MAX_LEN or not any(t in text for t in FALLBACK_TERMS):
        return []
    if strict and not MODAL_RE.search(text):
        return []
    return [("fallback", _trim(text))]
```

### 为什么这样写

1. **按"约束类型"组织规则，而不是按样本组织**。规则表里有 19 条，覆盖规模、名额、成绩、年级、
   经济、纪律、身体、服从性、投入、师资、优先项 11 类——每一类都是高校通知里反复出现的能力项。
   新增一种通知体裁时补的是"类型"，不是在给某条样本打补丁。
2. **`kind` 区分强信号与弱兜底**。规则命中（如"3名学生组队"）即使与"适用对象"重叠也要保留；
   兜底片段则要求带情态动词（须/应/需/限），否则"全日制本科三年级、四年级学生"这种身份描述
   会被误当成资格条件。
3. **否定式与倡议式先过滤**。"专业不限""不设专业限制""无年级与专业限制"说的都是"没有限制"，
   "鼓励跨学院组队"是倡议，它们都不构成可判定的门槛。

### 效果

资格条件 F1：**48.1% → 94.3%**（开发集）。这是本项目单项提升最大的一处改动。

---

## 4. 防幻觉：日期接地校验

### 问题

大模型在"抽取截止日期"这件事上会编——尤其是原文只写"本月底前"时，它倾向于给一个具体日期。
一份编造了截止日期的通知，会让学生错过真实的时间窗口，比"没解析出来"危险得多。

### 代码

`app/services/analyzer.py`

```python
def date_grounded(deadline: str, text: str) -> bool:
    """接地校验：抽出的日期必须能在原文里找到对应表述。"""
    try:
        d = dt.date.fromisoformat(deadline)
    except (TypeError, ValueError):
        return False
    body = _norm(text)                      # 去掉全部空白，容忍换行与空格差异
    candidates = [
        f"{d.year}年{d.month}月{d.day}日", f"{d.year}年{d.month:02d}月{d.day:02d}日",
        f"{d.year}-{d.month:02d}-{d.day:02d}", f"{d.year}-{d.month}-{d.day}",
        f"{d.year}/{d.month}/{d.day}", f"{d.year}.{d.month}.{d.day}",
        f"{d.month}月{d.day}日", f"{d.month}月{d.day}号",   # 无年份写法
    ]
    return any(c in body for c in candidates)


# 在 _validate_llm 中：
if deadline:
    deadline = str(deadline)[:10]
    if not date_grounded(deadline, content):
        notes.append(f"截止日期 {deadline} 未能在原文定位，已置空待复核")
        deadline = None
```

### 为什么这样写

1. **校验的是"能不能在原文里指出来"，而不是"看起来像不像日期"**。前者是事实核查，后者只是格式核查。
2. **多写法枚举**。同一份通知可能写 `2026年11月20日`、`2026-11-20`、`11月20日`，
   枚举 8 种常见写法再比对，避免把正确的抽取误判为幻觉。
3. **校验失败不是报错，而是置空 + 标 `needs_review`**。业务上"这条通知截止日期待确认"
   比"接口 500"或"给个错日期"都更好：用户能看到原文自己判断，运营能在后台补录。
4. 分类与规则引擎分歧时也会打 `review_note`，把"两个引擎不一致"这件事显式暴露给人工。

### 反例

只在提示词里写"不要编造"是无效约束。模型输出是概率性的，唯一可靠的做法是**输出后做一次事实核查**。
`tests/test_smoke.py` 里有一条专门的用例：给一个原文中不存在的日期 `2026-12-31`，
断言系统必须拒绝并置空。

---

## 5. 个性化匹配：可解释的三级结论

### 问题

"个性化推荐"最容易做成黑盒：给一个分数，用户不知道为什么。而通知类场景里，
**误判为"不符合"的代价很高**——学生可能因此错过一个本来能报的奖学金。

### 代码

`app/services/matcher.py`

```python
def _check_target(user, target):
    """返回 (结论, 理由)。结论取值：ok / conflict / unknown"""
    text = " ".join(target)
    if not target:
        return "unknown", "通知未明确写出适用对象，需人工确认"

    grade, family = user.get("grade", "").strip(), _grade_family(user.get("grade", ""))
    if any(k in text for k in ("全体", "不限", "在校学生", "全体学生", "全校")):
        return "ok", f"通知面向全体学生（依据：{text[:40]}），与你身份不冲突"

    if "研究生" in text or "硕士" in text or "博士" in text:
        return ("ok", "通知面向研究生，与你当前学历层次一致") if family == "研究生" \
               else ("conflict", "通知仅面向研究生，你当前为本科生")

    if "本科生" in text or "本科" in text:
        if family == "研究生":
            return "conflict", "通知面向本科生，你当前为研究生"
        hit = [g for g in ("大一", "大二", "大三", "大四") if g in text]
        if hit and grade and grade not in hit:
            return "conflict", f"通知限定{'、'.join(hit)}，你当前为{grade}"
        ...
```

三态设计：

| 状态 | 触发条件 | 前端表现 |
| --- | --- | --- |
| `conflict` | 存在硬性冲突（学历层次不符、年级不在范围、已过截止） | 暂不符合，不给分数 |
| `ok` | 所有已知条件均满足 | 符合，给匹配分 |
| `unknown` | 有字段未抽取出来，无法判断 | **可能符合**，列出"待确认项" |

### 为什么这样写

1. **不确定要单独成一档**，不能并入"不符合"。规则引擎在留出集上条件召回约 60%，
   如果缺失字段一律判"不符合"，会把大量本可以报的通知过滤掉——这比不推荐更糟。
2. **每条理由都带依据**：`"通知限定大三、大四，你当前为大二"` 这种句式直接引用了抽取字段，
   用户能立刻判断系统是否理解错了，比"匹配度 62%"有用得多。
3. **冲突时不给分数**。给一个 12 分的"符合度"会让用户以为还有机会，直接说"仅面向研究生"更清楚。

---

## 6. 智能问答：宁可拒答，不可乱答

### 问题

"智能问答"最常见的失败不是答错，而是**答非所问却很像真的**。
测试中曾出现：问"量子计算机的超导材料怎么制备"，系统命中了含"材料"二字的高校通知并生成了一段回答。

### 代码

`app/services/qa.py`

```python
FILLER_RE = re.compile(r"(最近|近期|有哪些|哪些|有什么|怎么样|怎么|如何|请问|可以|能否|需要|要求|吗|呢|啊|吧)")

def _terms(query: str) -> set[str]:
    q = FILLER_RE.sub(" ", query or "")              # 剥离疑问填充词，它们不携带检索语义
    ...

# 检索打分后加相关性门槛
matched = {t for t in terms if t in title or t in body}
coverage = len(matched) / len(terms)
if score > 0 and matched and coverage >= 0.5:        # 命中率过半才算"相关"
    scored.append((score, n))
```

### 为什么这样写

1. **疑问词必须先剥离**。"最近有哪些奖学金可以申请"里只有"奖学金"是有检索价值的，
   如果把"最近""有哪些""可以"都算进查询词，分母被稀释，覆盖率永远上不去，结果是**全部拒答**。
2. **用覆盖率而不是绝对命中数**。绝对命中数对长问题不利，覆盖率对短问题不利——
   实测覆盖率单一阈值在 10 条测试问题上表现最稳。
3. **拒答是功能，不是失败**。`hit=false` 时明确告诉用户"已收录通知中没有相关内容"，
   并提示可以怎么问，比给一个编造的答案更符合校园场景的可信度要求。

### 效果

| 问题 | 结果 |
| --- | --- |
| 蓝桥杯什么时候截止报名 | 命中，给出具体日期与出处 |
| 支教志愿者需要什么条件 | 命中，列出条件与原文片段 |
| 最近有哪些奖学金可以申请 | 命中，返回条目清单 |
| 量子计算机的超导材料怎么制备 | **拒答**，提示语料范围 |
| 今天食堂有什么菜 | **拒答** |

---

## 7. 文体闸门：先问"要不要抽"，再问"抽得准不准"

真实站点跑通之后，最刺眼的错误不是抽错，而是**抽了不该抽的**：
「学校捧得挑战杯优胜杯」被抽出报名截止 `2026-11-02`（其实是赛事会期末端），
「夏梦玲教授在 Nature Photonics 发表成果」把学科名「材料」当成报名材料。
绕过闸门做对照，18 条新闻稿会产出 25 个假字段。

根因不是抽取算法不准，而是**先问错了问题**——新闻稿里没有"可办理事项"，
对它跑报名类字段抽取，规则再精细也只会产出看似合理的假数据。
所以解析链路前置一道文体判别，规则刻意采用"硬信号一票否决"而非打分：

```python
# app/services/genre.py
HARD_ACTION = ("报名", "申报", "截止", "提交", "附件", "联系人", "请各", "现将", ...)

if "公示" in title:
    genre = "publicity"                     # 公示仍抽字段，但截止标为"异议截止"
elif hard:                                  # 只要有一个办理信号，绝不判新闻
    genre = "notice"
elif lead or channel or len(editorial) >= 2:
    genre = "news"                          # 新闻导语 / 新闻栏目 / 报道式标题
else:
    genre = "notice"                        # 无明确特征时保守处理
```

宁可漏拦一条新闻，也绝不抹掉一条真通知的截止日期。栏目 URL 只作弱先验，
换一所学校后仍以内容为准。闸门同时作用于规则引擎与大模型两条链路：

```python
# app/services/analyzer.py
def _apply_genre_gate(result, info):
    result["genre"] = info["genre"]
    result["actionable"] = info["actionable"]
    if info["actionable"]:
        result["deadline_kind"] = extractor.deadline_kind(evidence)
        return result
    result.update({"deadline": None, "target": [], "conditions": [],
                   "materials": [], "evidence": {}, "needs_review": False})
    result["review_note"] = "判定依据：" + info["reason"]
    return result
```

下游也随之收敛：新闻稿不会出现在个性化匹配里，调用待办接口直接返回 400，
界面上显示"无办理事项"而不是"未识别到截止日期，请核对原文"这种误导性提示。
效果由 `scripts/real_audit.py` 复算：**新闻资讯报名类字段误报 0/18**。

---

## 8. 结构化字段契约：字面事实不交给模型生成

### 问题

设计文档 8.2 要求输出十四个字段，早期版本只落地了八个：专业 / 年级限制混在 `target` 的自由文本里，
匹配引擎只能靠字符串包含做模糊猜测；活动时间、报名开始、地点、联系人电话也一并缺失。
更麻烦的是，电话、邮箱、专业名这类**字面值**恰恰是生成式模型最容易编造的东西——
让它"补全"一个不存在的联系人，比不给这个字段危险得多。

### 代码

`app/services/fields.py` 先把契约写死成六个可判定字段：

```python
# 设计文档 8.2 里「约束类 / 时间类 / 联络类」字段的统一契约。
# 这些是字面事实，由本模块确定性抽取，不交给生成式模型（见 analyzer 的说明）。
FIELD_KEYS: tuple[str, ...] = (
    "grade_requirement", "major_requirement", "registration_start",
    "event_time", "location", "contact",
)


def empty_fields() -> dict:
    return {
        "grade_requirement": [],
        "major_requirement": [],
        "registration_start": None,
        "event_time": None,
        "location": "",
        "contact": {},
    }
```

统一入口 `extract_structured()` 复用 `extractor` 已经找好的日期候选，避免二次扫描产生年份推断口径不一致：

```python
def extract_structured(text: str, dates: list[dict], target: list[str],
                       conditions: list[str], deadline: str | None) -> tuple[dict, dict]:
    """结构化字段统一入口，返回 (字段字典, 证据字典)。"""
    ctx = _constraint_text(text, target, conditions)
    grade = extract_grade(ctx)
    major = extract_major(text, ctx)
    reg_start, event, time_ev = extract_times(text, dates, deadline)
    location, loc_ev = extract_location(text)
    contact, contact_ev = extract_contact(text)

    fields = {
        "grade_requirement": grade,
        "major_requirement": major,
        "registration_start": reg_start,
        "event_time": event,
        "location": location,
        "contact": contact,
    }
```

每个非空字段都必须给出**原文中的连续片段**作为证据，归一化只在"同一事实的写法差异"范围内进行：

```python
    evidence: dict[str, str] = {}
    # 证据必须是原文中的连续片段，不能是拼接出来的归一化结果，
    # 否则 analyzer 的 grounding 校验会把合法字段误判成模型编造。
    if grade:
        grade_ev = next((ln.strip()[:80] for ln in text.split("\n")
                         if any(k in ln for _, keys in GRADE_TOKENS for k in keys)), "")
        if grade_ev:
            evidence["grade_requirement"] = grade_ev
```

挂载到结果时再逐条做接地校验，证据对不上就整字段置空（`app/services/analyzer.py`）：

```python
def _attach_literal_fields(result: dict, fields: dict, content: str) -> dict:
    ev = dict(result.get("evidence") or {})
    src_ev = fields.get("evidence") or {}
    for key in fields_mod.FIELD_KEYS:
        value = fields.get(key)
        fragment = str(src_ev.get(key) or "")
        if value and fragment and evidence_grounded(fragment, content):
            result[key] = value
            ev[key] = fragment[:120]
        else:
            result[key] = fields_mod.empty_fields()[key]
            ev.pop(key, None)
    result["evidence"] = ev
    return result
```

### 为什么这样写

1. **契约先行**：`FIELD_KEYS` 是唯一事实来源，规则引擎与大模型两条链路都按同一组字段挂载，
   不会出现"模型链路多给一个字段、规则链路少给一个"的口径漂移。
2. **只捕获、不推断**：`extract_grade()` 在"博士研究生 / 硕士研究生"已表达层次时不再单列泛化的
   "研究生"；专业只在约束语境（`scoped_text()`）里采信，新闻稿里的"聚焦学科""知识体系"不会被当成专业限制。
3. **证据即准入条件**：字段值本身不做接地校验，校验的是它的 `evidence` 能不能在原文中定位。
   这样既防住了模型编造，也防住了"归一化结果被当成原文片段"这种更隐蔽的假接地。

### 效果

14 类结构化字段全部落地并带证据（`docs/shot-fields.png`），专业 / 年级限制成为可被匹配引擎
直接判定的枚举；用例见 `tests/test_smoke.py` 第 [13] 组。

---

## 9. 相对时间：结合发布时间换算，不确定就置空

### 问题

通知里写「请于本周五前提交」「三个工作日内报送」时，原文并没有绝对日期。
让模型凭感觉补一个日期是最危险的做法——它看起来合理，却会让学生错过真实窗口。
反过来，新闻稿里的「本月底组织评审」「近日举办」根本不是办理截止，也不该被换算。

### 代码

`app/services/reltime.py` 先用两道正则把"办理语境"和"排除语境"划清楚：

```python
# 办理语境：相对词前后 24 字内必须出现办理动词或截止标签
ACTION = re.compile(r"报名|报考|申报|申请|提交|报送|投递|上传|填报|递交|受理|截止|截至|材料|回执")
# 明确排除非办理语境（评审、举办、公示、审批、新闻）
BLOCK = re.compile(r"组织评审|举行|举办|召开|公示期|审批|验收|公布|新闻网讯|本网讯|启动|开展")
```

换算主体 `resolve_relative()` 返回 `(ISO 日期或 None, 命中原文片段, 说明)`：

```python
def resolve_relative(text: str, publish: str) -> tuple[str | None, str, str]:
    """返回 (ISO 日期或 None, 命中的原文片段, 说明)。

    说明为空表示「没有相对时间表述」；非空表示需要向复核人员解释为何置空。
    """
    try:
        pub = dt.date.fromisoformat((publish or "")[:10])
    except (TypeError, ValueError):
        pub = None

    for pattern, kind in REL_PATTERNS:
        for m in pattern.finditer(text):
            lo = text.rfind("\n", 0, m.start()) + 1
            hi = text.find("\n", m.end())
            line = text[lo: hi if hi != -1 else len(text)]
            window = text[max(0, m.start() - 24): min(len(text), m.end() + 24)]
            if BLOCK.search(window) or not ACTION.search(window):
                continue
            frag = line.strip()[:80]
            if pub is None:
                return None, frag, "原文使用相对时间「%s」且缺少发布时间，已置空待复核" % m.group(0)
            d = _resolve_date(kind, m, pub)
            if d is None:
                return None, frag, "相对时间「%s」无法换算为具体日期，已置空待复核" % m.group(0)
            if d < pub or (d - pub).days > 120:
                return None, frag, "相对时间换算结果 %s 超出合理区间，已置空待复核" % d.isoformat()
            return d.isoformat(), frag, ""
    return None, "", ""
```

### 为什么这样写

1. **语境闸门在前**：窗口里没有办理动词、或命中评审 / 举办 / 公示 / 新闻导语，直接 `continue`，
   新闻稿的「本月底组织评审」不会被凭空造出一个截止日。
2. **缺发布时间就置空**：没有 `publish_time` 就没有换算基准，此时返回 `None` 并附说明，
   由 `analyzer._finalize()` 把说明写进 `issues`、置 `needs_review=true`，
   而不是硬猜一个日期（`deadline_source="relative"` 只在成功换算时标记）。
3. **结果也要合理**：换算出的日期早于发布时间、或距发布超过 120 天，一律不采信。
   「三个工作日内」用 `_resolve_date()` 逐日跳过周末，不按自然日粗暴加三天。

### 效果

`extractor.extract()` 在找不到绝对截止日时调用它补位；C6 对 `deadline_source == "relative"`
的结果特意跳过"原文数字日期定位"（原文里本来就没有数字日期），改由 C5 校验命中的整行证据，
避免把合法换算误杀成幻觉。用例见 `tests/test_smoke.py` 第 [13] 组。

---

## 10. 规则校验统一出口：两条链路共用一个 `_finalize()`

### 问题

四层保护管的是"模型输出能不能信"，但结果本身是否自洽（截止早于发布、报名起止倒挂、
证据不接地、新闻稿仍带着办理字段）并没有被系统性地检查过。
如果规则链路和模型链路各写各的校验，换引擎时约束就会被绕过。

### 代码

`app/services/validator.py` 的 `audit()` 把结果分成通过 / 硬异常 / 软提示三档，
下面是 C8、C9 与返回值的真实片段：

```python
    # C8 置信度：软提示，不硬拦截
    conf = result.get("confidence")
    try:
        conf = float(conf or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf < 0.7:
        warnings.append("解析置信度 %.2f 偏低，建议人工核对关键字段" % conf)
    else:
        passed.append("C8 置信度达标")

    # C9 新闻稿不得携带办理类字段（复核文体闸门是否生效）
    if not result.get("actionable", True):
        leaked = [k for k in ("deadline", "target", "conditions", "materials",
                              "grade_requirement", "major_requirement", "location")
                  if result.get(k)]
        if leaked:
            issues.append("新闻资讯仍携带办理字段：%s" % "、".join(leaked))
        else:
            passed.append("C9 文体闸门无字段泄漏")

    return {"passed": passed, "issues": issues, "warnings": warnings, "ok": not issues}
```

九项检查对应设计文档的九条要求：

| 编号 | 检查项 | 分档 |
| --- | --- | --- |
| C1 | 日期格式与真实性 | 硬异常 |
| C2 | 截止 ≥ 发布时间 | 硬异常 |
| C3 | 报名起止顺序 | 硬异常 |
| C4 | 时间跨度合理（≤ 730 天） | 硬异常 |
| C5 | 证据片段原文接地 | 硬异常 |
| C6 | 截止日期原文可定位（相对时间换算走 `deadline_source="relative"`） | 硬异常，否则置空 |
| C7 | 活动时间与截止同日 | 软提示 |
| C8 | 置信度偏低 | 软提示 |
| C9 | 文体闸门字段泄漏 | 硬异常 |

`app/services/analyzer.py` 的 `_finalize()` 是规则与大模型两条链路的**统一校验出口**：

```python
def _finalize(result: dict, content: str, publish: str) -> dict:
    """解析链路的统一出口：跑规则校验，把硬异常写回复核状态。

    技术文档2 第 57 章「AI 负责理解，程序负责约束，原文负责证明」在这里落地：
    无论走规则还是大模型，最后都要过同一套校验，换引擎不会绕过约束。
    """
    report = validator.audit(result, content, publish)
    if result.get("relative_note"):
        report["issues"].append(result["relative_note"])
        report["ok"] = False
    result["checks"] = report
    if report["issues"]:
        result["needs_review"] = True
        extra = "规则校验：" + "；".join(report["issues"])
        result["review_note"] = "；".join(
            x for x in (result.get("review_note") or "", extra) if x
        )
    return result
```

`analyze()` 里规则链路（`_rule_result`）与模型链路（`_validate_llm`）最后都 `return _finalize(...)`，
报告随解析结果一起写入 `notice_analysis.checks`，前端 `.check-panel` 据此渲染「通过 / 拦截 / 提示」计数。

### 为什么这样写

1. **软硬分档**：规则引擎置信度天然偏低（典型 0.6—0.8），若把 `confidence < 0.7` 当硬拦截，
   几乎全部通知都会被标红，复核反而失去意义；所以 C8 只进 `warnings`，硬拦截留给时间倒挂、
   证据不接地这类真异常。
2. **统一出口**：校验不挂在某条链路内部，而是放在 `analyze()` 的最后一步，
   "换上 MaaS 之后同样的漏检重现"这件事就不会发生。
3. **报告可回写**：`issues` 不只是给人看的文字，它同时驱动 `needs_review` 与 `review_note`，
   把校验结果接进 `docs/architecture.md` 2.4 的人工审核闭环。

---

## 11. 硬条件匹配：从"字符串包含猜"到枚举冲突判定

### 问题

早期版本只能拿 `target` 的自由文本做包含判断：判断"信息管理与信息系统"是否满足
「电子信息」专业限制时，取前两字「信息」会命中「电子信息」，造成假命中；
而明确写着"仅面向计算机、电子信息、自动化"的通知，对一个信息管理专业学生也只给一个低分，
结论含糊成"可能符合"。

### 代码

`app/services/matcher.py` 的 `_check_major_hard()` 直接读结构化的 `major_requirement`：

```python
def _check_major_hard(user: dict, req: list[str]) -> tuple[str, str]:
    """硬条件：专业 / 学院限制。明确列出且完全不命中时直接判「暂不符合」。"""
    if not req:
        return "", ""
    major = (user.get("major") or "").strip()
    if not major:
        return "unknown", "尚未填写专业，无法核对通知的专业限制（%s）" % "、".join(req[:3])
    # 只做词级双向包含，不用「前两个字」这类短前缀：
    # 「信息管理与信息系统」的前两字「信息」是「电子信息」的子串，短前缀会造成假命中。
    stem = re.sub(r"(?:专业|学院|系)$", "", major)
    for r in req:
        key = re.sub(r"(?:专业|学院|学科)$", "", r)
        if len(key) >= 2 and (key in stem or stem in key):
            return "ok", "通知限定专业「%s」，与你的专业「%s」相符" % (r, major)
    return "conflict", "通知限定专业 %s，你的专业「%s」不在范围内" % (
        "、".join("「%s」" % r for r in req[:4]), major)
```

`match_one()` 中结构化字段优先于自由文本兜底，冲突直接置 `conflict`：

```python
    # 3) 专业硬条件：结构化字段优先；明确冲突时与学历冲突一样直接判「暂不符合」
    m_state, m_text = _check_major(user, analysis.get("conditions") or [], analysis.get("target") or [])
    hard_m = _check_major_hard(user, analysis.get("major_requirement") or [])
    if hard_m[0]:
        m_state, m_text = hard_m
    if m_state == "conflict":
        conflict = True
        score -= 30
```

配套的 `_check_grade_hard()` 对 `grade_requirement` 做同样处理：把"本科生 / 硕士研究生 / 博士研究生"
等学历层次与"大一…研三"具体年级分开判定，命中冲突返回 `conflict`；`match_one()` 里
`if conflict: level = "no"`，即"暂不符合"。

### 为什么这样写

1. **先剥后缀再比对**：`stem` 与 `key` 都去掉「专业 / 学院 / 系」后缀，只在词级做双向包含，
   且要求 `len(key) >= 2`，从根上避免了短前缀造成的假命中。
2. **明确冲突不给分**：`conflict` 一旦置位，结论直接是 `level="no"` 而不参与分数排序，
   用户看到的是"通知限定 X，你的专业 Y 不在范围内"，而不是一个 12 分的"符合度"。
3. **缺失信息单独成档**：用户没填专业时返回 `unknown`（可能符合），不把"无法判断"误判成"不符合"。

### 效果

`tests/test_smoke.py` 第 [13] 组断言：专业硬条件冲突判 `no`、「信息管理 vs 电子信息」不产生
短前缀假命中、命中时仍保持 `match`；学历 / 年级硬条件同理。

---

## 12. 表格行的时间轴：动作只取一格，年份以标题为准

### 问题

时间轴抽取器是按自然句写的：拿到日期之后的尾巴，找「截止」或「前」截出事项。
但进度表类通知的办理安排全在一张六列表格里
（`序号 / 截止日期 / 工作内容 / 完成单位 / 星期 / 周次`），日期右边还有四格。
用自然句的规则去切二维数据，结果是动作串把「星期」「周次」一起吞掉：

```
各学院返回毕业答辩登记表至培养处各学院星期三第二周
```

同一段代码还有第二个坑：把「7月2日」补成完整日期需要参考年，原来直接取页面上的
发布时间，而站点自己会把年份写错——《关于2019年秋季…工作安排的通知》的标签写着
「发布时间：2020-09-04」，于是同一份通知的节点裂成 2019 与 2020 两截。

### 代码

```python
def _table_cell_action(seg: str, dates: list[dict]) -> str:
    """表格行：动作只取「截止日期」右边那一格，且只取第一件事。"""
    cells = [c.strip() for c in seg.split(CELL_SEP)]
    if len(cells) < 2:
        return ""
    raw = seg[dates[-1]["start"]:dates[-1]["end"]]
    idx = -1
    for i, cell in enumerate(cells):          # 先定位日期落在哪一格
        if raw and raw in cell:
            idx = i
            break
    if idx < 0 or idx + 1 >= len(cells):
        return ""
    body = cells[idx + 1]                     # 右边一格才是「工作内容」
    if not body:
        return ""
    if "截止" in body:                        # 「X 截止」的动作在截止前面
        body = body[: body.index("截止")]
    for part in re.split(r"[①②③④⑤⑥⑦⑧⑨⑩]", body):
        piece = _clean_action(part)           # 并列多件事只取第一件
        if piece and _has_verb(piece):
            return piece
    return ""
```

```python
_TITLE_YEAR_RE = re.compile(r"(20\d{2})\s*年")


def _title_year(title: str) -> int | None:
    m = _TITLE_YEAR_RE.search(title or "")
    return int(m.group(1)) if m else None


# extract() 里：标题年份与发布年份冲突时以标题为准
year_base = publish
tyear = _title_year(title)
if tyear and tyear != ref_year:
    ref_year = tyear
    year_base = "%d-01-01" % tyear        # infer_year 的基准日同步换掉
```

### 为什么这样写

1. **分句模型和表格不是一回事**：表格里「一列」才是语义单元。用自然句的截断规则切二维
   数据，必然要么吞掉右侧列，要么在格内顿号处断在句子中间（「各类型博士、硕士办理答辩
   手续」会被截成「各类型博士」）。所以按单元格取，而不是给正则打补丁。
2. **身份判定也要看动作格**：「完成单位」列常写「各学院、各研究生」这种混合值，只看这
   一列会把学生自己的事判成学院办理，个人时间轴上就消失了（「研究生在系统里上传送审
   论文」）。改成先看动作格写明的执行者，再退回完成单位列。
3. **标题年份是编辑提炼过的强证据**：它常常还是全文唯一写明学年的地方；发布时间是采集
   来的元数据，可能出错。两者冲突时以标题为准，标题没写年份就完全退回原逻辑——42 条
   真实通知里只有 1 条结果会变，回归面极小。
4. **并列多件事只取第一件**：一个日期对应「①… ②… ③…」三件事时，三件压成一句读起来
   是乱的，只留第一件更接近「这个日期之前必须开始做什么」。

### 效果

2019 秋季通知：11 个节点、年份全部 2019、列泄漏 0 条；2020 秋季：8 个节点、8 条全 2020。
「培养处下发…」「各学院返回…」正确判为管理侧，个人时间轴不再混入学生办不了的事。
`tests/test_smoke.py` 为此新增 11 项断言。

---

## 小结

上述代码的同一条主线是：**在"能自动"和"可信任"之间选择可信任**。

- 日期不取第一个，而做上下文打分 + 行内窗口；
- 文体上先判"有没有可办的事"，没有就不抽，而不是抽完再挑；
- 条件不做整句匹配，而归一到可判定的原子约束；
- 模型输出不直接入库，而做一次原文接地核查；
- 匹配不追求单一分数，而把"不确定"显式成一档；
- 问答不追求有问必答，而把"未收录"作为正常结果；
- 字面事实（专业 / 年级 / 时间 / 地点 / 联络）不交给模型生成，而由确定性抽取器逐字段接地；
- 相对时间不凭感觉补日期，而结合发布时间换算，无法确定就置空待复核；
- 规则与大模型两条链路不各自入库，而过同一个 `_finalize()` 校验出口（C1—C11）；
- 专业 / 年级 / 学历硬条件不做短字符串包含猜，而按结构化枚举判冲突。

对校园通知这类**错一次的代价明显高于漏一次**的场景，这个取舍方向比模型本身更重要。
