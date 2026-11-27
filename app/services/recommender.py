"""精准推荐：在匹配打分之上做「召回 → 排序 → 重排」三段式，输出可直接展示的推荐列表。

为什么要在 matcher 之外再包一层：
    match_one 回答的是"这条通知与我合不合"，逐条独立判断，不关心整体列表长什么样。
    但真正的推荐位关心的是整屏效果——48 条都"符合"等于没推荐。
    所以这里补三件事：
      1) 召回（recall）：先按可办理性、截止状态、主题相邻做粗筛，把明显无关的挡在门外；
      2) 排序（ranking）：复用 matcher 的多因子 fit，保证"合不合"的判断只有一份实现；
      3) 重排（re-rank）：同分类去重（MMR 思路）+ 临期插队，让首屏既有相关性又有覆盖面。

冷启动策略：用户没填兴趣时，兴趣因子退化为中性 0.5，此时排序主要由
"适用对象 + 专业限制 + 时效"驱动——这正是新生打开系统时唯一可靠的信号。
"""
from __future__ import annotations

import datetime as dt
import re

from app.services.matcher import (
    CATEGORY_AFFINITY,
    FACTOR_LABELS,
    INTEREST_MAP,
    build_reasons,
    score_factors,
)
from app.services.textutil import safe_iso_date

# 首屏推荐的默认条数。与首页"为你推荐"卡片的展示位一致。
DEFAULT_LIMIT = 6
# 同一分类在首屏最多连续占几席，避免"整屏都是竞赛"。
MAX_PER_CATEGORY = 2
# 不同分类之间的相关性折扣：MMR 的 λ，越大越看重相关性，越小越看重多样性。
DIVERSITY_LAMBDA = 0.72


def _user_interests(user: dict) -> set[str]:
    raw = user.get("interests") or []
    if isinstance(raw, str):
        raw = [x for x in raw.replace("，", ",").split(",") if x]
    out = set()
    for item in raw:
        text = str(item).strip()
        if text:
            out.add(INTEREST_MAP.get(text, text))
    return out


def _recall_reason(scored: dict, user: dict, analysis: dict) -> str:
    """给推荐位一句"为什么推给你"的短句，优先说兴趣与时效，其次说资格匹配。"""
    interests = _user_interests(user)
    category = scored["category"]
    if category in interests:
        head = f"你关注「{category}」"
    elif CATEGORY_AFFINITY.get(category, set()) & interests:
        head = f"与你关注的「{'、'.join(sorted(CATEGORY_AFFINITY.get(category, set()) & interests))}」相关"
    else:
        head = f"属于「{category}」"

    day = safe_iso_date(analysis.get("deadline"))
    if day is not None:
        left = (day - dt.date.today()).days
        if left == 0:
            return f"{head}，今天截止"
        if 1 <= left <= 3:
            return f"{head}，且 {left} 天内截止"
        if 3 < left <= 14:
            return f"{head}，{day.isoformat()} 截止"
    return f"{head}，条件与你的年级专业相符"


def _title_key(title: str) -> str:
    """标题归一化：去掉标点、空白与「关于/的通知」这类外壳，用于判定同一件事的重复副本。

    教务处与学院往往把同一份通知各发一次，标题只差一两个字。推荐位只有 6 个位置，
    让两份副本占掉两格，等于少推一条真正不同的通知。
    """
    text = str(title or "")
    text = re.sub(r"[\s\u3000]+", "", text)
    text = re.sub(r"[（(【\[].*?[）)】\]]", "", text)
    text = re.sub(r"[，,。.、：:；;！!？?\-—_/\\|·…\"'“”‘’《》<>]", "", text)
    text = re.sub(r"^关于", "", text)
    text = re.sub(r"(的)?(通知|公告|启事|公示|说明)$", "", text)
    return text


def recommend(user: dict, notices: list[dict], limit: int = DEFAULT_LIMIT,
              include_expired: bool = False, today: dt.date | None = None) -> dict:
    """对一批（notice, analysis）组合做推荐。

    notices 每项形如 {"id", "category", "publish_time", "created_at", "analysis"}。
    返回 {"items": [...], "meta": {...}}，items 每项含 score/factors/reasons/why。
    """
    today = today or dt.date.today()
    limit = max(1, min(20, int(limit or DEFAULT_LIMIT)))

    recalled: list[dict] = []
    skipped = {"no_analysis": 0, "not_actionable": 0, "expired": 0, "vetoed": 0,
               "duplicate": 0}
    seen_titles: set[str] = set()

    for row in notices:
        analysis = row.get("analysis")
        if not analysis:
            skipped["no_analysis"] += 1
            continue
        # 新闻资讯没有可办理事项，进推荐位只会浪费一个位置
        if not analysis.get("actionable", True):
            skipped["not_actionable"] += 1
            continue

        scored = score_factors(user, row, analysis, today=today)
        if scored["expired"] and not include_expired:
            skipped["expired"] += 1
            continue
        if scored["conflict"]:
            # 硬冲突（学历/专业不符）不占推荐位，但仍在匹配页完整可见
            skipped["vetoed"] += 1
            continue

        # 同一份通知的两个来源副本只保留一条（notices 已按发布时间倒序，
        # 先到的那条就是较新的），否则首屏 6 格里会有一格被白白浪费。
        key = _title_key(row.get("title") or "")
        if key and key in seen_titles:
            skipped["duplicate"] += 1
            continue
        if key:
            seen_titles.add(key)

        recalled.append({
            "notice": row,
            "analysis": analysis,
            "scored": scored,
            "category": scored["category"],
        })

    recalled.sort(key=lambda x: (-x["scored"]["fit"], x["notice"]["id"]))

    # MMR 重排：每次从候选里挑「相关性 - 与已选内容的重复度」最高的一条。
    # 折扣项刻意做得很小（约 0.03），只用来打破近似同分的平局；
    # 真正的分类上限靠 MAX_PER_CATEGORY 生效——否则同分类的高分项会被
    # 硬压到低分项之后，首屏出现「89 分排在 76 分下面」这种反直觉顺序。
    picked: list[dict] = []
    picked_categories: dict[str, int] = {}
    pool = list(recalled)
    while pool and len(picked) < limit:
        best_idx, best_val = 0, -1e9
        for idx, cand in enumerate(pool):
            cat = cand["category"]
            same = picked_categories.get(cat, 0)
            penalty = same * (1.0 - DIVERSITY_LAMBDA) * 0.12
            if same >= MAX_PER_CATEGORY:
                # 到上限后才重罚，把剩下的位置让给别的主题
                penalty += 0.5
            value = cand["scored"]["fit"] - penalty
            if value > best_val:
                best_idx, best_val = idx, value
        chosen = pool.pop(best_idx)
        picked_categories[chosen["category"]] = picked_categories.get(chosen["category"], 0) + 1
        picked.append(chosen)

    items = []
    for cand in picked:
        scored = cand["scored"]
        row = cand["notice"]
        items.append({
            "notice_id": row["id"],
            "level": "match",
            "level_text": "符合",
            "score": scored["score"],
            "fit": scored["fit"],
            "category": scored["category"],
            "why": _recall_reason(scored, user, cand["analysis"]),
            "factors": scored["factors"],
            "reasons": build_reasons(scored),
            "top_factor": next(
                (FACTOR_LABELS[f["key"]] for f in sorted(
                    scored["factors"], key=lambda f: -f["contribution"])),
                "",
            ),
        })

    meta = {
        "candidate_total": len(notices),
        "recalled": len(recalled),
        "returned": len(items),
        "limit": limit,
        "max_per_category": MAX_PER_CATEGORY,
        "diversity_lambda": DIVERSITY_LAMBDA,
        "skipped": skipped,
        "cold_start": not _user_interests(user),
        "weights": {k: v for k, v in ((f["key"], f["weight"]) for f in (items[0]["factors"] if items else []))},
    }
    return {"items": items, "meta": meta}

def explain(weights: dict | None = None) -> list[dict]:
    """对外暴露推荐模型说明，供「推荐算法说明」面板展示，保证可解释、可审计。"""
    from app.services.matcher import FACTOR_WEIGHTS

    data = weights or FACTOR_WEIGHTS
    desc = {
        "target": "通知写明的适用对象/年级/学历是否覆盖你，硬冲突直接否决",
        "major": "通知是否限定专业方向，完全不命中即否决",
        "interest": "通知类目与你填写的兴趣方向的重合度，相邻主题给部分分",
        "condition": "是否存在成绩、四六级、无挂科等需要自行核对的硬门槛",
        "urgency": "距报名截止的天数，临期加分，已截止否决",
        "freshness": "通知的发布时间，新通知优先展示",
        "quality": "解析出的关键字段数量与置信度，信息越完整越靠前",
    }
    return [
        {"key": k, "label": FACTOR_LABELS[k], "weight": round(v, 3), "desc": desc[k]}
        for k, v in data.items()
    ]
