"""文体判别：把「要办事的通知」和「只是报道的新闻」分开。

这是 42 份真实站点快照逼出来的一层。规则引擎原先对所有页面一视同仁地抽
「报名截止 / 报名材料 / 资格条件」，结果在新闻稿上闹了三类笑话：

    ·「学校捧得挑战杯优胜杯」→ 把「10月30日至11月2日」的赛事举办期当报名截止；
    ·「第14届人力资源管理论坛在武汉举行」→ 把会议结束日当报名截止；
    ·「夏梦玲教授在 Nature Photonics 发表成果」→ 把学科名「材料」当报名材料。

根因不是抽取算法不准，而是**先问错了问题**：一篇纯报道压根没有「可办理事项」，
对它做报名类字段抽取，无论规则多精细都只能产出看似合理的假数据。

因此解析链路前置一道闸门：先判文体，只有事务通知 / 公示公告才进入报名类字段
抽取；新闻资讯只做分类与摘要，报名类字段一律留空并写明原因。

判别刻意做成「硬信号一票否决」而非打分：只要正文出现报名、申报、截止、附件、
联系人、请各、现将等任何一个办理信号，就不判新闻。宁可漏拦一条新闻，也绝不
把一条真通知的截止日期抹掉。栏目 URL 只作弱先验，换一所学校后仍以内容为准。
"""
from __future__ import annotations

import re

# 出现任意一个，就说明这份文本里存在「要办的事」，不允许判为新闻
HARD_ACTION = (
    "报名", "报考", "申报", "截止", "截至", "提交", "报送", "附件", "联系人",
    "联系电话", "请各", "现将", "逾期", "投递", "填报", "递交", "申请", "受理",
    "不予", "资格条件", "报名方式", "材料要求", "有关事项通知如下",
)

# 新闻稿导语：出现在正文前 200 字内才算（正文中段引用不算）
NEWS_LEADS = (
    "新闻网讯", "本网讯", "本报讯", "通讯员", "摄影：", "摄影:",
    "编辑：", "编辑:", "审稿：", "学校新闻网",
)

# 强新闻信号：出现在标题或正文开头，命中任意一个（且全文无办理要求）即判新闻。
# 这类词是结果报道 / 喜报的专属表述，真正"要办事"的通知不会这样写；
# 单独"获奖/举行"太宽（"关于表彰获奖同学的通知"会被误伤），故收窄为成词短语。
NEWS_STRONG = (
    "喜获佳绩", "再创佳绩", "喜报", "捷报", "再传捷报", "载誉",
    "结果公布", "表示祝贺", "落下帷幕", "圆满落幕", "圆满结束",
    "摘得桂冠", "摘得冠军", "蝉联", "折桂", "勇夺", "喜夺", "斩获",
)

# 新闻栏目的 URL 特征：弱先验，只在无法从正文判定时兜底
NEWS_CHANNEL_RE = re.compile(
    r"(?:^|[/.])(?:news|xww|xwzx|xwdt|zhxw|xsdt|lgzx|media|mtbd)(?:[/.]|$)", re.I
)

# 标题里典型的报道动词，两个以上才构成旁证
EDITORIAL_TITLE = (
    "荣获", "获奖", "捧得", "夺冠", "发表", "刊发", "揭牌", "签约", "来访",
    "调研", "做客", "一行赴", "赴", "开幕", "举行", "召开", "入选", "获批",
    "落成", "启动仪式", "走访", "荣获", "获评",
)

GENRE_LABELS = {
    "notice": "事务通知",
    "publicity": "公示公告",
    "news": "新闻资讯",
}


def detect(title: str, content: str, source_url: str = "") -> dict:
    """返回文体、是否可办理，以及一句可展示给人看的判定理由。

    >>> detect("关于举办数学建模竞赛的通知", "请各学院于11月20日前提交报名表，联系人：张老师")["genre"]
    'notice'
    >>> detect("我校团队在Nature发表封面文章", "新闻网讯 近日，我校团队……")["genre"]
    'news'
    """
    title = (title or "").strip()
    body = content or ""
    head = body[:200]

    hard = [w for w in HARD_ACTION if w in body]
    lead = [w for w in NEWS_LEADS if w in head]
    # 强新闻词看标题或正文开头，与导语同等权重
    strong = [w for w in NEWS_STRONG if w in (title + head[:120])]
    channel = bool(NEWS_CHANNEL_RE.search(source_url or ""))
    editorial = [w for w in EDITORIAL_TITLE if w in title]

    if "公示" in title:
        genre = "publicity"
        reason = "标题含「公示」：保留办理字段，截止日期按异议/补报口径解读"
    elif hard:
        genre = "notice"
        reason = "正文含办理要求（%s等），按事务通知解析" % "、".join(hard[:3])
    elif lead or strong or channel or len(editorial) >= 2:
        cues = []
        if lead:
            cues.append("新闻导语「%s」" % lead[0])
        if strong:
            cues.append("报道式表述「%s」" % strong[0])
        if channel:
            cues.append("新闻栏目来源")
        if len(editorial) >= 2:
            cues.append("标题为报道式表述")
        genre = "news"
        reason = "%s；全文无报名/申报/截止等办理要求" % "、".join(cues)
    else:
        # 既无办理信号也无新闻信号：保守按事务通知处理，避免抹掉潜在字段
        genre = "notice"
        reason = "未发现明确的新闻特征，按事务通知保守处理"

    return {
        "genre": genre,
        "label": GENRE_LABELS[genre],
        # 新闻资讯没有「可办理事项」，不应进入匹配与待办
        "actionable": genre != "news",
        "reason": reason,
        "signals": {
            "hard_action": hard[:6],
            "news_lead": lead,
            "news_strong": strong,
            "news_channel": channel,
            "editorial_title": editorial[:4],
        },
    }


def labels() -> dict:
    return dict(GENRE_LABELS)
