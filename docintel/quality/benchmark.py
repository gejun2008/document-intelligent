"""离线评测：预测 md 和人工金标 md 逐文件对比。用于选模型、调 prompt、回归。

指标：
- text_ned：归一化编辑距离（越低越好），比之前先统一空白和 md 语法
- table_cell_f1：表格单元格多重集 F1（TEDS 的轻量替代；要算 TEDS 可接 apted 包）
- heading_f1：(层级, 标题文本) 集合 F1
- number_f1：数字多重集 F1（财务类文档最关心的指标）

金标目录里有 manifest.jsonl（docintel synth 生成）时，按 kind / difficulty 分组汇总。
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from rapidfuzz.distance import Levenshtein

from docintel.quality.checks import split_row
from docintel.quality.reference import numbers

METRICS = ("text_ned", "table_cell_f1", "heading_f1", "number_f1")

_WS = re.compile(r"\s+")
_MD = re.compile(r"[#*_`>|]|-{3,}|<!--.*?-->", re.S)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$", re.M)


def normalize(md: str) -> str:
    return _WS.sub(" ", _MD.sub(" ", md)).strip().lower()


def _f1(pred: Counter, gold: Counter) -> float:
    if not pred and not gold:
        return 1.0
    overlap = sum((pred & gold).values())
    p = overlap / sum(pred.values()) if pred else 0.0
    r = overlap / sum(gold.values()) if gold else 0.0
    return 2 * p * r / (p + r) if p + r else 0.0


def _table_cells(md: str) -> Counter[str]:
    cells: Counter[str] = Counter()
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("|") and not re.match(r"^\|?\s*:?-{3,}", s):
            cells.update(_WS.sub(" ", c).strip().lower() for c in split_row(s))
    return cells


def _headings(md: str) -> Counter[tuple[int, str]]:
    return Counter((len(h), _WS.sub(" ", t).lower()) for h, t in _HEADING.findall(md))


@dataclass
class BenchRow:
    name: str
    text_ned: float
    table_cell_f1: float
    heading_f1: float
    number_f1: float
    group: str = ""


def score_pair(pred: str, gold: str, name: str = "") -> BenchRow:
    return BenchRow(
        name=name,
        text_ned=Levenshtein.normalized_distance(normalize(pred), normalize(gold)),
        table_cell_f1=_f1(_table_cells(pred), _table_cells(gold)),
        heading_f1=_f1(_headings(pred), _headings(gold)),
        number_f1=_f1(numbers(pred), numbers(gold)),
    )


def _mean(rows: list[BenchRow]) -> dict[str, float]:
    return {k: round(sum(asdict(r)[k] for r in rows) / len(rows), 4) for k in METRICS} | {"n": len(rows)}


def run_benchmark(pred_dir: Path, gold_dir: Path, names: set[str] | None = None,
                  ) -> tuple[list[BenchRow], dict[str, dict[str, float]]]:
    """返回逐文件结果和分组汇总（"all" 加上 manifest 里的 kind、kind/difficulty）。
    names 不为空时只评这些文件（不带扩展名）。"""
    manifest = {}
    mpath = gold_dir / "manifest.jsonl"
    if not mpath.exists():
        mpath = gold_dir.parent / "manifest.jsonl"
    if mpath.exists():
        for line in mpath.read_text("utf-8").splitlines():
            item = json.loads(line)
            manifest[item["name"]] = item

    rows = []
    for gold in sorted(gold_dir.glob("*.md")):
        if names is not None and gold.stem not in names:
            continue
        pred = pred_dir / gold.name
        pred_md = pred.read_text("utf-8") if pred.exists() else ""
        row = score_pair(pred_md, gold.read_text("utf-8"), gold.name)
        meta = manifest.get(gold.stem)
        row.group = f"{meta['kind']}/{meta['difficulty']}" if meta else ""
        rows.append(row)
    if not rows:
        return rows, {}

    groups: dict[str, list[BenchRow]] = defaultdict(list)
    for r in rows:
        groups["all"].append(r)
        if r.group:
            groups[r.group.split("/")[0]].append(r)
            groups[r.group].append(r)
    return rows, {g: _mean(rs) for g, rs in sorted(groups.items())}
