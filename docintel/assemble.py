"""把页级结果拼成整篇 markdown：去页眉页脚、接跨页表格、回填图片占位符。"""

from __future__ import annotations

import re

from docintel.models import PageResult, Route
from docintel.quality.checks import split_row

_DIGITS = re.compile(r"\d+")
# 单独成行的页码：短文档凑不够页数做统计，也要能去掉
_PAGE_NUMBER = re.compile(r"^\s*(?:第\s*\d+\s*页(?:\s*[,，/]?\s*共\s*\d+\s*页)?|[-–—]?\s*\d{1,4}\s*[-–—]?|"
                          r"\d{1,4}\s*/\s*\d{1,4}|page\s+\d+(?:\s+of\s+\d+)?)\s*$", re.I)
_IMG_REF = re.compile(r"!\[[^\]]*\]\((docintel-img:\d+)\)")
_TABLE_SEP = re.compile(r"^\|?\s*:?-{3,}")
_PAGED = {Route.PDF_TEXT, Route.AZURE_DI, Route.VLM, Route.VLM_GROUNDED}
EDGE_LINES = 2


def _norm(line: str) -> str:
    return _DIGITS.sub("#", line.strip().lower())


def _edges(nonblank: list[int]) -> set[int]:
    # 短页只看首尾各一行，保证中间至少留一行正文不被当成页眉页脚
    n = min(EDGE_LINES, max(len(nonblank) - 1, 0) // 2)
    return set(nonblank[:n] + nonblank[len(nonblank) - n:]) if n else set()


def strip_headers_footers(pages: list[str], min_pages: int = 4, ratio: float = 0.6) -> list[str]:
    """首尾行里的页码直接去掉；在 ≥60% 页面的首尾行反复出现的行（数字归一化后），视为页眉页脚。"""
    repeated: set[str] = set()
    if len(pages) >= min_pages:
        counts: dict[str, int] = {}
        for p in pages:
            lines = p.splitlines()
            for key in {_norm(lines[i]) for i in _edges([i for i, l in enumerate(lines) if l.strip()])}:
                counts[key] = counts.get(key, 0) + 1
        repeated = {k for k, c in counts.items() if c >= ratio * len(pages)}

    out = []
    for p in pages:
        lines = p.splitlines()
        nonblank = [i for i, l in enumerate(lines) if l.strip()]
        edge = _edges(nonblank)
        first_last = {nonblank[0], nonblank[-1]} if len(nonblank) >= 2 else set()
        out.append("\n".join(l for i, l in enumerate(lines)
                             if not ((i in edge and _norm(l) in repeated)
                                     or (i in first_last and _PAGE_NUMBER.match(l)))).strip())
    return out


def _cols(line: str) -> int:
    return len(split_row(line))


def _continues_table(prev: str, nxt: str) -> bool:
    prev_lines = [l for l in prev.splitlines() if l.strip()]
    next_lines = [l for l in nxt.splitlines() if l.strip()]
    return bool(prev_lines and next_lines and prev_lines[-1].lstrip().startswith("|")
                and next_lines[0].lstrip().startswith("|") and _cols(prev_lines[-1]) == _cols(next_lines[0]))


def _drop_repeated_header(prev: str, nxt: str) -> str:
    """续表如果重复了表头（表头 + 分隔行），去掉；只有分隔行时，把它删掉让首行变成数据行。"""
    lines = nxt.strip().splitlines()
    prev_table = [l for l in prev.splitlines() if l.lstrip().startswith("|")]
    header = prev_table[0].strip() if prev_table else None
    if len(lines) >= 2 and _TABLE_SEP.match(lines[1].strip()):
        if header and lines[0].strip() == header:
            return "\n".join(lines[2:])
        return "\n".join([lines[0], *lines[2:]])
    return "\n".join(lines)


def assemble(results: list[PageResult], keep_page_markers: bool) -> str:
    images = {r.task.placeholder: r.markdown for r in results if r.task.route is Route.IMAGE and r.task.placeholder}
    body = sorted((r for r in results if not (r.task.route is Route.IMAGE and r.task.placeholder)),
                  key=lambda r: r.task.order)

    texts = [r.markdown.strip() for r in body]
    paged_idx = [i for i, r in enumerate(body) if r.task.route in _PAGED]
    for i, cleaned in zip(paged_idx, strip_headers_footers([texts[i] for i in paged_idx])):
        texts[i] = cleaned

    chunks: list[str] = []
    for i, (r, text) in enumerate(zip(body, texts)):
        if chunks and r.task.route in _PAGED and _continues_table(chunks[-1], text):
            chunks[-1] = chunks[-1].rstrip() + "\n" + _drop_repeated_header(chunks[-1], text)
            continue
        marker = f"<!-- page {r.task.label or r.task.unit} -->\n" if keep_page_markers else ""
        if r.task.route is Route.IMAGE:  # 没有占位符的独立图片（xlsx 内嵌图、单张图片）
            text = f"> [图片 {r.task.label}]\n\n{text}" if len(body) > 1 else text
        chunks.append(marker + text)

    md = "\n\n".join(c for c in chunks if c.strip())
    md = _IMG_REF.sub(lambda m: "\n" + images.get(m.group(1), "") + "\n", md)
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"
