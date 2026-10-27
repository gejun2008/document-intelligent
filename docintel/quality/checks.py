"""无参考答案的结构检查：截断、复读、表格列数、乱码、拒答。"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

_REFUSAL = re.compile(
    r"(I(?:'m| am) (?:sorry|unable)|I can(?:no|')t (?:help|assist|transcribe|read)|"
    r"无法(?:识别|转写|读取|提供)|抱歉，?我)", re.I)
_LOOP = re.compile(r"(.{8,200}?)\1{4,}", re.S)  # 同一片段连续出现 5 次以上
_TABLE_SEP = re.compile(r"^\|?\s*:?-{3,}")


_EMPTY_ROW = re.compile(r"^[|\s]*$")
LOOP_ROWS = 5


def repetition_ratio(md: str) -> float:
    """重复行占比 + 片段死循环。VLM 退化时最典型的症状。

    表格单独判断：重复的单元格、合并单元格填充出的重复值都是正常内容，
    只有连续 5 行完全相同的非空数据行才算死循环。
    """
    table, prose = [], []
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)  # 页标记、解析器的注释标记不算内容
    for raw in md.splitlines():
        line = raw.strip()
        (table if line.startswith("|") else prose).append(line)

    run, prev = 1, None
    for line in table:
        if _TABLE_SEP.match(line) or _EMPTY_ROW.match(line):
            run, prev = 1, None
            continue
        run = run + 1 if line == prev else 1
        prev = line
        if run >= LOOP_ROWS:
            return 1.0

    if _LOOP.search("\n".join(prose)[:30000]):
        return 1.0
    lines = [l for l in prose if len(l) >= 8]
    if len(lines) < 5:
        return 0.0
    counts = Counter(lines)
    dup = sum(c - 1 for c in counts.values() if c > 1)
    return dup / len(lines)


def split_row(line: str) -> list[str]:
    """拆 md 表格行。只去掉首尾各一个竖线：strip("|") 会把末尾的空单元格也吃掉。"""
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return re.split(r"(?<!\\)\|", body)


def _cells(line: str) -> int:
    return len(split_row(line))


def table_consistency(md: str) -> tuple[float, int]:
    """所有表格里，列数等于该表众数的行的占比；返回 (占比, 表格数)。"""
    tables: list[list[str]] = []
    current: list[str] = []
    for line in md.splitlines():
        if line.lstrip().startswith("|"):
            current.append(line)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    tables = [t for t in tables if len(t) >= 2]
    if not tables:
        return 1.0, 0
    good = total = 0
    for t in tables:
        widths = [_cells(l) for l in t]
        mode = Counter(widths).most_common(1)[0][0]
        good += sum(1 for w in widths if w == mode)
        total += len(widths)
        if not any(_TABLE_SEP.match(l.strip()) for l in t[:3]):
            total += 1  # 缺表头分隔行，记一次扣分
    return good / total, len(tables)


def garbage_ratio(md: str) -> float:
    chars = [c for c in md if not c.isspace()]
    if not chars:
        return 0.0
    bad = sum(1 for c in chars if c == "�" or unicodedata.category(c) in ("Co", "Cn", "Cc"))
    return bad / len(chars)


def looks_like_refusal(md: str) -> bool:
    return len(md) < 400 and bool(_REFUSAL.search(md))
