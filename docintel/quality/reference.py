"""拿原生文本当"免费参考答案"，衡量视觉路径的漏字和幻觉。

用词袋而不是编辑距离：原生文本的顺序常常不可靠（文本框、多栏），但"有哪些字"是可靠的。
中日韩字符按单字计，拉丁文按词计，数字单独统计——财务类文档最怕数字错。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

_TOKEN = re.compile(r"[㐀-鿿豈-﫿぀-ヿ가-힯]|[A-Za-z]+|\d+(?:[.,]\d+)*")
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:,\d{3})*(?:\.\d+)?%?")
_MD_NOISE = re.compile(r"!\[[^\]]*\]\([^)]*\)|<!--.*?-->|<[^>]+>|\$+", re.S)


def _clean(text: str) -> str:
    return _MD_NOISE.sub(" ", text)


def tokens(text: str) -> Counter[str]:
    return Counter(t.lower() for t in _TOKEN.findall(_clean(text)))


def numbers(text: str) -> Counter[str]:
    return Counter(n.replace(",", "") for n in _NUMBER.findall(_clean(text)))


def _pr(pred: Counter[str], ref: Counter[str]) -> tuple[float, float]:
    overlap = sum((pred & ref).values())
    recall = overlap / sum(ref.values()) if ref else 1.0
    precision = overlap / sum(pred.values()) if pred else (1.0 if not ref else 0.0)
    return recall, precision


@dataclass
class RefMetrics:
    recall: float          # 原文的字有多少出现在输出里 → 漏内容
    precision: float       # 输出的字有多少能在原文里找到 → 幻觉
    number_recall: float
    number_precision: float
    ref_numbers: int

    @property
    def f1(self) -> float:
        s = self.recall + self.precision
        return 2 * self.recall * self.precision / s if s else 0.0


def compare(pred_md: str, ref_text: str) -> RefMetrics:
    r, p = _pr(tokens(pred_md), tokens(ref_text))
    ref_nums = numbers(ref_text)
    nr, np_ = _pr(numbers(pred_md), ref_nums)
    return RefMetrics(r, p, nr, np_, sum(ref_nums.values()))
