"""精准推荐：内容匹配 + 语义画像 + 协同行为 + 探索重排，五路信号融合的五段式推荐。

为什么要在 matcher 之外再包一层：
    match_one 回答的是"这条通知与我合不合"，逐条独立判断，不关心整体列表长什么样。
    但真正的推荐位关心的是整屏效果——48 条都"符合"等于没推荐。
    所以这里补四件事：

      1) 召回（recall）：先按可办理性、截止状态、重复副本做粗筛，把明显无关的挡在门外；
      2) 多路信号（signal）：内容七因子 fit / TF-IDF 语义相似度 / 同学协同行为 / 全站热度；
      3) 融合排序（fusion）：把五路信号按内容为主的权重合成一个可解释的最终得分；
      4) 探索与重排（explore + MMR）：给冷门主题一点曝光（UCB 思路），再按同分类上限收敛。

为什么不是纯协同过滤：
    校内通知场景只有几十个用户，协同信号稀疏且容易把新生推进"什么都推不出来"。
    所以协同只占 0.16 的权重，且缺失时自动归零——内容与语义两路始终可用。

为什么还要探索：
    只按相关性排序会形成信息茧房：关注竞赛的同学永远看不到奖助学金。
    这里对「尚未出现且不在你兴趣列表里」的主题给一点探索加成，量级远小于
    fit 之间的正常差距，只用于把"边界上也还合适"的条目提到首屏末位。

冷启动策略：用户没填兴趣、也没有行为数据时，兴趣与协同两路归零，
排序由"适用对象 + 专业限制 + 时效 + 全站热度"驱动——这正是新生打开系统时唯一可靠的信号。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re

from app.services.matcher import (
    CATEGORY_AFFINITY,
    FACTOR_LABELS,
    INTEREST_MAP,
    build_reasons,
    score_factors,
)
from app.services.textutil import safe_iso_date
from app.services.vectorizer import TfidfIndex

# 首屏推荐的默认条数。与首页"为你推荐"卡片的展示位一致。
DEFAULT_LIMIT = 6
# 同一分类在首屏最多连续占几席，避免"整屏都是竞赛"。
MAX_PER_CATEGORY = 2
# 不同分类之间的相关性折扣：MMR 的 λ，越大越看重相关性，越小越看重多样性。
DIVERSITY_LAMBDA = 0.72

# 五路信号的融合权重（和为 1）。内容七因子占一半以上，保证"合不合"仍是主因；
# 语义补兴趣词表覆盖不到的近义表达；行为与协同体现"你实际做了什么"；热度做冷启动兜底。
FUSION_WEIGHTS = {
    "content": 0.54,
    "semantic": 0.22,
    "behavior": 0.12,
    "peer": 0.06,
    "popular": 0.06,
}

FUSION_LABELS = {
    "content": "内容匹配",
    "semantic": "语义相近",
    "behavior": "历史偏好",
    "peer": "同学也在看",
    "popular": "全站关注度",
}

# 余弦相似度的「满分」参照值。中文双字向量在短文本上很难超过 0.3，
# 直接用原始余弦作权重等于这一路信号形同虚设，所以做一次线性缩放。
SEMANTIC_FULL = 0.30

# 探索加成上限：只够把"边界合适"的条目往上提一两位，绝不能压过真正的强相关。
EXPLORE_BONUS = 0.035
# 探索的目标：首屏里希望出现的不同分类数，达到后不再给加成。
EXPLORE_TARGET_CATEGORIES = 4

ALGORITHM_VERSION = "fusion-v2"


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


def _interest_terms(user: dict) -> str:
    """把用户的兴趣原文拼成一段文本，投到语义空间里。

    只用类目名（"学科竞赛"）会丢掉"数学建模""大创"这类具体指向，
    所以保留原始词，同时补上映射后的类目词。
    """
    raw = user.get("interests") or []
    if isinstance(raw, str):
        raw = [x for x in raw.replace("，", ",").split(",") if x]
    parts = [str(x).strip() for x in raw if str(x).strip()]
    parts.extend(sorted(_user_interests(user)))
    major = str(user.get("major") or "").strip()
    if major:
        parts.append(major)
    return " ".join(parts)


def _doc_text(row: dict, analysis: dict) -> str:
    """语义索引用的文档文本：标题权重最高，其次是抽取出的结构化字段与正文前段。"""
    pieces = [str(row.get("title") or ""), str(analysis.get("category") or "")]
    for key in ("target", "conditions", "materials", "major_requirement", "grade_requirement"):
        value = analysis.get(key)
        if isinstance(value, (list, tuple)):
            pieces.append(" ".join(str(x) for x in value))
        elif value:
            pieces.append(str(value))
    body = str(analysis.get("summary") or row.get("content") or "")
    if body:
        pieces.append(body[:400])
    return " ".join(p for p in pieces if p)


def _norm_scores(mapping: dict[int, float]) -> dict[int, float]:
    """把计数类信号压到 0~1。用 log 平滑而不是线性归一。

    线性归一的问题：全站只有 1 个人加入过待办时，那条会拿到 1.0（满分热度），
    与"50 人都关注"在排序上毫无区别。log 平滑让"1 人→0.5 左右、多人→接近 1"，
    更接近"热度"的真实语义，也避免个别数据把首屏带偏。
    """
    if not mapping:
        return {}
    import math

    top = max(mapping.values())
    if top <= 0:
        return {}
    scale = math.log1p(top)
    return {k: round(min(1.0, math.log1p(max(0.0, v)) / scale), 4) for k, v in mapping.items()}


def _recall_reason(scored: dict, user: dict, analysis: dict,
                   signals: dict | None = None, extra: dict | None = None) -> str:
    """给推荐位一句"为什么推给你"的短句，优先说兴趣与时效，其次说行为与协同。"""
    signals = signals or {}
    extra = extra or {}
    interests = _user_interests(user)
    category = scored["category"]

    if extra.get("peer", 0) >= 0.6:
        head = f"关注「{category}」的同学也加入了待办"
    elif category in interests:
        head = f"你关注「{category}」"
    elif CATEGORY_AFFINITY.get(category, set()) & interests:
        head = f"与你关注的「{'、'.join(sorted(CATEGORY_AFFINITY.get(category, set()) & interests))}」相关"
    elif extra.get("semantic", 0) >= 0.35:
        head = "与你处理过的通知主题相近"
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
    if extra.get("explore", 0) > 0:
        return f"{head}，同时为你补充一个新方向"
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


def _seed(user: dict, today: dt.date) -> int:
    """探索用的确定性种子：同一用户同一天得到同一份推荐，便于复现与截图。"""
    raw = f"{user.get('id', 0)}|{today.isoformat()}|{ALGORITHM_VERSION}"
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8], 16)


def recommend(user: dict, notices: list[dict], limit: int = DEFAULT_LIMIT,
              include_expired: bool = False, today: dt.date | None = None,
              signals: dict | None = None) -> dict:
    """对一批（notice, analysis）组合做推荐。

    notices 每项形如 {"id", "category", "publish_time", "created_at", "analysis"}。
    signals 可选，来自 interest.build_signals：{"engaged", "engaged_weights",
    "co_engaged", "popular"}。缺失时行为/协同两路自动归零，不影响可用性。

    返回 {"items": [...], "meta": {...}}，items 每项含 score/fit/fusion/why/factors/reasons。
    """
    today = today or dt.date.today()
    limit = max(1, min(20, int(limit or DEFAULT_LIMIT)))
    signals = signals or {}
    engaged = set(signals.get("engaged") or [])
    co_engaged = _norm_scores({int(k): float(v) for k, v in (signals.get("co_engaged") or {}).items()})
    popular = _norm_scores({int(k): float(v) for k, v in (signals.get("popular") or {}).items()})

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

    # ---- 语义画像：用"你已加入待办的通知 + 你填的兴趣词"构建兴趣质心 ----
    docs = {str(c["notice"]["id"]): _doc_text(c["notice"], c["analysis"]) for c in recalled}
    index = TfidfIndex(docs) if docs else None
    centroid: dict[str, float] = {}
    if index is not None:
        engaged_keys = [str(i) for i in engaged if str(i) in docs]
        if engaged_keys:
            centroid = index.centroid(engaged_keys, signals.get("engaged_weights"))
        interest_vec = index.vector_from_text(_interest_terms(user))
        # 行为质心比兴趣词更可信：有权重时以行为为主（0.7），否则纯靠兴趣词
        if engaged_keys and interest_vec:
            centroid = {
                tok: 0.7 * centroid.get(tok, 0.0) + 0.3 * val
                for tok, val in interest_vec.items()
            }
            centroid = {
                tok: val for tok, val in centroid.items()
                if abs(val) > 1e-6
            }
            _n = sum(v * v for v in centroid.values()) ** 0.5
            if _n > 0:
                centroid = {tok: v / _n for tok, v in centroid.items()}
        elif interest_vec:
            centroid = interest_vec

    engaged_weights = {int(k): float(v) for k, v in (signals.get("engaged_weights") or {}).items()}
    for cand in recalled:
        key = str(cand["notice"]["id"])
        nid = int(cand["notice"]["id"])
        # 原始余弦在中文短文本上普遍落在 0.05~0.3，除以参照值放大到 0~1 才有区分度
        cosine = index.sim_to(centroid, key) if (index and centroid) else 0.0
        semantic = round(min(1.0, cosine / SEMANTIC_FULL), 4) if cosine > 0 else 0.0
        behavior = round(min(1.0, engaged_weights.get(nid, 0.0)), 4)
        peer = co_engaged.get(nid, 0.0)
        pop = popular.get(nid, 0.0)
        content = cand["scored"]["fit"]
        fusion = (
            FUSION_WEIGHTS["content"] * content
            + FUSION_WEIGHTS["semantic"] * semantic
            + FUSION_WEIGHTS["behavior"] * behavior
            + FUSION_WEIGHTS["peer"] * peer
            + FUSION_WEIGHTS["popular"] * pop
        )
        cand.update({
            "signals": {
                "content": round(content, 4),
                "semantic": semantic,
                "cosine": round(cosine, 4),
                "behavior": behavior,
                "peer": peer,
                "popular": pop,
            },
            "fusion": round(fusion, 4),
        })

    recalled.sort(key=lambda x: (-x["fusion"], x["notice"]["id"]))

    # ---- 探索加成（UCB 思路）：给"尚未出现的冷门主题"一点曝光机会 ----
    # 加成量级（≤0.035）刻意远小于常见 fusion 差距，只影响近似同分的边界条目。
    interests = _user_interests(user)
    seed = _seed(user, today)
    explored: list[int] = []
    if recalled:
        categories = sorted({c["category"] for c in recalled})
        # 用种子打散候选顺序，避免永远只有同一个冷门主题拿到曝光
        shuffled = sorted(categories, key=lambda c: (hashlib.md5(f"{seed}|{c}".encode()).hexdigest(), c))
        rare = [c for c in shuffled if c not in interests][:max(0, EXPLORE_TARGET_CATEGORIES)]
        for cand in recalled:
            if cand["category"] in rare:
                # 越靠后的冷门主题拿到越少加成，形成"先补缺口、再补边角"的顺序
                boost = EXPLORE_BONUS * (1.0 - rare.index(cand["category"]) / max(1, len(rare)))
                cand["explore"] = round(boost, 4)
                explored.append(cand["notice"]["id"])
                cand["value"] = round(cand["fusion"] + boost, 4)
                continue
            cand["explore"] = 0.0
            cand["value"] = cand["fusion"]
    # ---- MMR 重排：相关性优先，同分类到上限后重罚，把剩余位置让给别的主题 ----
    # 折扣项刻意做得很小，只用来打破近似同分的平局；真正的分类上限靠
    # MAX_PER_CATEGORY 生效——否则同分类的高分项会被硬压到低分项之后，
    # 首屏出现「89 分排在 76 分下面」这种反直觉顺序。
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
                penalty += 0.5
            value = cand.get("value", cand["fusion"]) - penalty
            if value > best_val:
                best_idx, best_val = idx, value
        chosen = pool.pop(best_idx)
        picked_categories[chosen["category"]] = picked_categories.get(chosen["category"], 0) + 1
        picked.append(chosen)

    items = []
    for cand in picked:
        scored = cand["scored"]
        row = cand["notice"]
        extra = {**cand["signals"], "explore": cand.get("explore", 0.0)}
        items.append({
            "notice_id": row["id"],
            "level": "match",
            "level_text": "符合",
            "score": scored["score"],
            "fit": scored["fit"],
            "fusion": cand["fusion"],
            "explore": cand.get("explore", 0.0),
            "value": cand.get("value", cand["fusion"]),
            "signals": cand["signals"],
            "category": scored["category"],
            "why": _recall_reason(scored, user, cand["analysis"], signals, extra),
            "factors": scored["factors"],
            "reasons": build_reasons(scored),
            "top_factor": next(
                (FACTOR_LABELS[f["key"]] for f in sorted(
                    scored["factors"], key=lambda f: -f["contribution"])),
                "",
            ),
        })

    signals_meta = {
        "engaged": len(engaged),
        "co_engaged": len(co_engaged),
        "popular": len(popular),
        "semantic_centroid_terms": len(centroid),
        "explored": len(set(explored)),
    }
    meta = {
        "candidate_total": len(notices),
        "recalled": len(recalled),
        "returned": len(items),
        "limit": limit,
        "max_per_category": MAX_PER_CATEGORY,
        "diversity_lambda": DIVERSITY_LAMBDA,
        "skipped": skipped,
        "cold_start": not _user_interests(user) and not engaged,
        "weights": {k: v for k, v in ((f["key"], f["weight"]) for f in (items[0]["factors"] if items else []))},
        "fusion_weights": dict(FUSION_WEIGHTS),
        "signals": signals_meta,
        "algorithm": ALGORITHM_VERSION,
        "explore_bonus": EXPLORE_BONUS,
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


def fusion_explain() -> list[dict]:
    """五路信号融合说明，与 explain() 的七因子并列展示，解释"最终排序怎么来的"。"""
    desc = {
        "content": "七因子加权后的内容匹配度，权重最高，决定「合不合」",
        "semantic": "与你已加入待办的同类通知、兴趣词的 TF-IDF 余弦相似度",
        "behavior": "该通知是否已被你加入待办（已加入的会排前，但不会重复推）",
        "peer": "和你关注过同一批通知的同学，还关注了这条多少次（协同过滤）",
        "popular": "全站加入待办的人次，冷启动时用于兜底排序",
    }
    return [
        {"key": k, "label": FUSION_LABELS[k], "weight": round(v, 3), "desc": desc[k]}
        for k, v in FUSION_WEIGHTS.items()
    ]


def pipeline() -> list[str]:
    """五段式推荐流程，供文档与界面共用同一份描述。"""
    return [
        "召回：过滤新闻稿、不可办理、已截止、硬冲突与同题重复副本",
        "画像：用已加入待办的通知加权构建 TF-IDF 兴趣质心，叠加兴趣词向量",
        "融合：内容七因子 + 语义相似 + 你的行为 + 同学协同 + 全站热度五路加权",
        "探索：给首屏尚未出现的冷门主题一点加成，避免信息茧房",
        "重排：同分类最多两席，保证首屏既有相关性又有覆盖面",
    ]
