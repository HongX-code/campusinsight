"""轻量语义向量：TF-IDF + 余弦相似度，纯标准库实现。

为什么不用现成的 embedding 服务：
    1) 无外部依赖才能降级（AGENTS.md #6）——没配 MaaS 时推荐位仍要能工作；
    2) 校内通知语料只有几十到几百条，词表规模极小，TF-IDF 已足够表达"同一个主题"；
    3) 稀疏字典比稠密向量省内存，也不需要在 requirements 里引入 numpy。

三个用途：
    - 用户画像向量：把用户加入待办、近期浏览过的通知向量加权平均，得到「兴趣质心」；
    - 语义相似度：候选通知与质心的余弦相似度，补上"兴趣词表"覆盖不到的近义表达；
    - 近邻识别：同一件事的多篇报道语义相似度极高，重排时可以据此压低重复内容。
"""
from __future__ import annotations

import math
import re

# 中文按双字切分：单字信息量太低（"的"、"关"），三字以上过于稀疏（语料只有几十条）。
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_WORD = re.compile(r"[a-z0-9]+")
# 出现频次极高、几乎不携带主题信息的词，直接丢弃（只影响相似度，不影响展示）。
_NOISE = {
    "通知", "公告", "关于", "举办", "开展", "组织", "进行", "有关", "工作", "要求",
    "如下", "现将", "事宜", "学生", "学校", "学院", "我校", "全校",
}


def tokens(text: str) -> list[str]:
    """把一段文本切成检索用词项：英文数字词 + 中文双字。"""
    body = (text or "").lower()
    out = [w for w in _ASCII_WORD.findall(body) if len(w) > 1]
    for run in _CJK_RUN.findall(body):
        if len(run) == 1:
            out.append(run)
            continue
        out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return [t for t in out if t not in _NOISE]


def _normalize(vec: dict[str, float]) -> dict[str, float]:
    norm = math.sqrt(sum(v * v for v in vec.values()))
    if norm <= 0:
        return {}
    return {t: v / norm for t, v in vec.items()}


class TfidfIndex:
    """一次性构建的 TF-IDF 索引，文档以 id -> 文本 传入。"""

    def __init__(self, docs: dict[str, str]):
        self.ids = list(docs)
        self._vec: dict[str, dict[str, float]] = {}
        counts: dict[str, dict[str, int]] = {}
        df: dict[str, int] = {}
        for key in self.ids:
            bag: dict[str, int] = {}
            for tok in tokens(docs[key]):
                bag[tok] = bag.get(tok, 0) + 1
            counts[key] = bag
            for tok in bag:
                df[tok] = df.get(tok, 0) + 1
        total = max(1, len(self.ids))
        # 平滑 IDF：只出现在一篇里的词权重最高，遍地都是的词趋近 1
        self._idf = {t: math.log((1 + total) / (1 + c)) + 1.0 for t, c in df.items()}
        for key in self.ids:
            weighted = {
                tok: (1.0 + math.log(cnt)) * self._idf[tok]
                for tok, cnt in counts[key].items()
                if tok in self._idf
            }
            self._vec[key] = _normalize(weighted)

    def vector(self, key: str) -> dict[str, float]:
        return self._vec.get(key) or {}

    def vector_from_text(self, text: str) -> dict[str, float]:
        """把任意一段短文本（例如用户的兴趣词）投到同一向量空间。

        语料里没出现过的词按「最稀有」赋权，这样"支教""保研"这类只在用户画像里
        出现、通知正文里偶尔一提的词，反而能拉到真正提到它的那几条通知。
        """
        bag: dict[str, int] = {}
        for tok in tokens(text):
            bag[tok] = bag.get(tok, 0) + 1
        ceiling = max(self._idf.values()) if self._idf else 2.0
        weighted = {
            tok: (1.0 + math.log(cnt)) * self._idf.get(tok, ceiling)
            for tok, cnt in bag.items()
        }
        return _normalize(weighted)

    def centroid(self, keys, weights: dict[str, float] | None = None) -> dict[str, float]:
        """把若干篇通知的向量加权平均成用户兴趣质心。

        权重用于区分「真的处理过」和「只是点开看过」——加入待办的通知更能代表兴趣。
        """
        acc: dict[str, float] = {}
        for key in keys:
            weight = 1.0 if weights is None else float(weights.get(key, 1.0))
            if weight <= 0:
                continue
            for tok, val in self.vector(key).items():
                acc[tok] = acc.get(tok, 0.0) + val * weight
        return _normalize(acc)

    @staticmethod
    def similarity(a: dict[str, float], b: dict[str, float]) -> float:
        """稀疏余弦相似度；两向量都已归一化，点积即余弦。"""
        if not a or not b:
            return 0.0
        if len(a) > len(b):
            a, b = b, a
        dot = sum(val * b.get(tok, 0.0) for tok, val in a.items())
        return max(0.0, min(1.0, dot))

    def sim_to(self, centroid: dict[str, float], key: str) -> float:
        return self.similarity(centroid, self.vector(key))

    def neighbours(self, key: str, threshold: float = 0.55) -> list[str]:
        """返回与某篇通知语义极近的其它文档 id（用于识别同一件事的多篇报道）。"""
        target = self.vector(key)
        if not target:
            return []
        return [
            other for other in self.ids
            if other != key and self.similarity(target, self.vector(other)) >= threshold
        ]
