"""olmOCR-Bench（allenai/olmOCR-bench，ODC-BY-1.0）：断言式评测。

每份 PDF 配若干条单元测试（文字出现/不出现、先后顺序、表格邻居、公式、基础健全性），
按官方 olmocr/bench/tests.py 的规则复刻：同样的文本归一化和 max_diffs 模糊阈值。

和官方实现的差异：
- math：官方把 LaTeX 用 KaTeX 渲染后比较；这里只做归一化后的字符串相等，结果是下界。
- 表格解析是自己写的 markdown/HTML 解析，top_heading 取该列的表头行，left_heading 取该行第一列。
- 评测前去掉 HTML 注释（流水线的页标记），否则含中文的页标记会让 baseline 测试判失败。
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from fuzzysearch import find_near_matches
from rapidfuzz import fuzz

from docintel.quality.checks import split_row

# ---- 准备：把 PDF 平铺成唯一文件名，方便 docintel run 和回查 ------------------


def doc_name(pdf: str) -> str:
    """"arxiv_math/2503.0001_pg3.pdf" → "arxiv_math__2503.0001_pg3"。"""
    return pdf.removesuffix(".pdf").replace("/", "__")


def find_bench_dir(data_dir: Path) -> Path:
    for cand in (data_dir / "bench_data", data_dir):
        if list(cand.glob("*.jsonl")):
            return cand
    raise FileNotFoundError(f"{data_dir} 下找不到 olmOCR-Bench 的 *.jsonl")


def load_tests(data_dir: Path) -> list[dict]:
    bench = find_bench_dir(data_dir)
    tests = []
    for jsonl in sorted(bench.glob("*.jsonl")):
        for line in jsonl.read_text("utf-8").splitlines():
            if line.strip():
                t = json.loads(line)
                t["_suite"] = jsonl.stem
                tests.append(t)
    return tests


def prepare(data_dir: Path, out_docs: Path, suites: set[str] | None = None, limit: int | None = None) -> int:
    """在 out_docs 下给每份测试 PDF 建软链接（不复制数据）。返回 PDF 数。"""
    bench = find_bench_dir(data_dir)
    out_docs.mkdir(parents=True, exist_ok=True)
    pdfs = sorted({t["pdf"] for t in load_tests(data_dir) if not suites or t["_suite"] in suites})
    if limit:
        pdfs = pdfs[:limit]
    for pdf in pdfs:
        src = (bench / "pdfs" / pdf).resolve()
        link = out_docs / f"{doc_name(pdf)}.pdf"
        if not link.exists():
            link.symlink_to(src)
    return len(pdfs)


# ---- 归一化（照抄官方规则）-----------------------------------------------------

_TYPO = str.maketrans({"‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"',
                       "＿": "_", "–": "-", "—": "-", "‑": "-", "‒": "-", "−": "-",
                       "µ": "μ"})


def normalize_text(s: str) -> str:
    s = re.sub(r"<br\s*/?>", " ", s)
    s = re.sub(r"\*\*(.*?)\*\*|__(.*?)__", lambda m: m.group(1) or m.group(2) or "", s)
    s = re.sub(r"</?[bi]>", "", s)
    s = re.sub(r"([*_])(.*?)\1", r"\2", s)
    s = re.sub(r"\s+", " ", s)
    return unicodedata.normalize("NFC", s).translate(_TYPO)


def _threshold(max_diffs: int, ref: str) -> float:
    return 1.0 - max_diffs / max(len(ref), 1)


# ---- 表格解析 -------------------------------------------------------------------


@dataclass
class Grid:
    rows: list[list[str]]
    header_rows: set[int] = field(default_factory=set)


_SEP = re.compile(r"^\|?\s*:?-{3,}")


def parse_md_tables(md: str) -> list[Grid]:
    grids, block = [], []
    for line in md.splitlines() + [""]:
        if line.strip().startswith("|"):
            block.append(line.strip())
            continue
        if len(block) >= 2:
            header = {0} if _SEP.match(block[1]) else set()
            rows = [[normalize_text(c).strip() for c in split_row(l)] for l in block if not _SEP.match(l)]
            grids.append(Grid(rows, header))
        block = []
    return grids


class _HTMLTables(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.grids: list[Grid] = []
        self._stack: list[tuple[list[list[str]], set[int], dict]] = []
        self._cell: list[str] | None = None
        self._cell_is_th = False
        self._row_all_th = True
        self._in_thead = False
        self._span = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._stack.append(([], set(), {}))
        elif not self._stack:
            return
        elif tag == "thead":
            self._in_thead = True
        elif tag == "tr":
            self._stack[-1][0].append([])
            self._row_all_th = True
        elif tag in ("td", "th"):
            self._cell, self._cell_is_th = [], tag == "th"
            span = dict(attrs).get("colspan") or "1"
            self._span = int(span) if span.isdigit() else 1

    def handle_endtag(self, tag: str) -> None:
        if not self._stack:
            return
        rows, header, _ = self._stack[-1]
        if tag in ("td", "th") and self._cell is not None and rows:
            text = normalize_text("".join(self._cell)).strip()
            rows[-1].extend([text] * self._span)  # colspan 展开，邻居关系按网格算
            self._row_all_th &= self._cell_is_th
            self._cell = None
        elif tag == "tr" and rows:
            if self._in_thead or (self._row_all_th and rows[-1]):
                header.add(len(rows) - 1)
        elif tag == "thead":
            self._in_thead = False
        elif tag == "table":
            rows, header, _ = self._stack.pop()
            if rows:
                self.grids.append(Grid(rows, header))

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def parse_html_tables(md: str) -> list[Grid]:
    p = _HTMLTables()
    p.feed(md)
    return p.grids


# ---- 各类测试 -------------------------------------------------------------------


def _presence(t: dict, md: str) -> bool:
    ref = normalize_text(t["text"])
    content = md
    first_n, last_n = t.get("first_n"), t.get("last_n")
    if first_n and last_n:
        content = content[:first_n] + content[-last_n:]
    elif first_n:
        content = content[:first_n]
    elif last_n:
        content = content[-last_n:]
    if not t.get("case_sensitive", True):
        ref, content = ref.lower(), content.lower()
    found = fuzz.partial_ratio(ref, content) / 100 >= _threshold(t.get("max_diffs", 0), ref)
    return found if t["type"] == "present" else not found


def _order(t: dict, md: str) -> bool:
    before, after, d = normalize_text(t["before"]), normalize_text(t["after"]), t.get("max_diffs", 0)
    bm = find_near_matches(before, md, max_l_dist=d)
    am = find_near_matches(after, md, max_l_dist=d)
    return any(b.start < a.start for b in bm for a in am)


def _close(a: str, b: str, max_diffs: int) -> bool:
    return fuzz.ratio(a, b) / 100 >= max(0.5, 1.0 - max_diffs / max(len(a), 1))


def _table(t: dict, md: str) -> bool:
    d = t.get("max_diffs", 0)
    cell = normalize_text(t["cell"]).strip()
    grids = parse_html_tables(md) + ([] if t.get("ignore_markdown_tables") else parse_md_tables(md))
    for g in grids:
        for r, row in enumerate(g.rows):
            for c, value in enumerate(row):
                if not _close(cell, value, d):
                    continue
                checks = {
                    "up": [g.rows[r - 1][c]] if r > 0 and c < len(g.rows[r - 1]) else [],
                    "down": [g.rows[r + 1][c]] if r + 1 < len(g.rows) and c < len(g.rows[r + 1]) else [],
                    "left": [row[c - 1]] if c > 0 else [],
                    "right": [row[c + 1]] if c + 1 < len(row) else [],
                    "top_heading": [g.rows[h][c] for h in sorted(g.header_rows) if h < r and c < len(g.rows[h])]
                                   or ([g.rows[0][c]] if r > 0 and c < len(g.rows[0]) else []),
                    "left_heading": [row[0]] if c > 0 else [],
                }
                ok = True
                for rel, candidates in checks.items():
                    want = normalize_text(t.get(rel) or "").strip()
                    if want and not any(_close(want, x, d) for x in candidates):
                        ok = False
                        break
                if ok:
                    return True
    return False


_EQ = [re.compile(r"\\\((.+?)\\\)", re.S), re.compile(r"\\\[(.+?)\\\]", re.S),
       re.compile(r"\$\$(.+?)\$\$", re.S), re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", re.S)]


def _latex_norm(s: str) -> str:
    s = re.sub(r"\\(left|right|displaystyle|,|;|!|quad|qquad)", "", s)
    return re.sub(r"\s+", "", s)


def _math(t: dict, md: str) -> bool:
    ref = _latex_norm(t["math"])
    content = md
    for i, pat in enumerate(_EQ):
        if t.get("ignore_dollar_delimited") and i >= 2:
            break
        for m in pat.finditer(content):
            if _latex_norm(m.group(1)) == ref:
                return True
        content = pat.sub(" ", content)
    return False


_DISALLOWED = re.compile("[一-鿿぀-ゟ゠-ヿ\U0001F300-\U0001FAFF\U0001F1E6-\U0001F1FF]")


def _baseline(t: dict, md: str) -> bool:
    if t.get("max_length") is not None:
        text = re.sub(r"!\[.*?\]\(.*?\)", "", md) if t.get("max_length_skips_image_alt_tags") else md
        return sum(ch.isalnum() for ch in text) <= t["max_length"]
    if not any(ch.isalnum() for ch in md):
        return False
    words = md.split()
    max_repeats = t.get("max_repeats", 30)
    for n in range(1, 6):
        run = 1
        for i in range(n, len(words) - n + 1, n):
            run = run + 1 if words[i:i + n] == words[i - n:i] else 1
            if run > max_repeats:
                return False
    return not (t.get("check_disallowed_characters", True) and _DISALLOWED.search(md))


def _format(t: dict, md: str) -> bool:
    pats = {"heading": [r"^#{1,6}\s+(.+?)$", r"<h[1-6][^>]*>(.*?)</h[1-6]>"],
            "bold": [r"\*\*(.+?)\*\*", r"__(.+?)__", r"<b>(.*?)</b>", r"<strong>(.*?)</strong>"],
            "italic": [r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"<i>(.*?)</i>",
                       r"<em>(.*?)</em>"]}[t["format"]]
    ref = normalize_text(t["text"])
    spans = [normalize_text(m) for p in pats for m in re.findall(p, md, re.M | re.S)]
    if not t.get("case_sensitive", True):
        ref, spans = ref.lower(), [s.lower() for s in spans]
    thr = _threshold(t.get("max_diffs", 0), ref)
    return any(fuzz.partial_ratio(ref, s) / 100 >= thr for s in spans)


_RUNNERS = {"present": _presence, "absent": _presence, "order": _order, "table": _table, "math": _math,
            "baseline": _baseline, "format": _format}
APPROX = {"math"}  # 和官方实现有差异的测试类型


def run_test(t: dict, raw_md: str) -> bool | None:
    """返回通过与否；不支持的测试类型返回 None。"""
    runner = _RUNNERS.get(t["type"])
    if runner is None:
        return None
    md = re.sub(r"<!--.*?-->", "", raw_md, flags=re.S)
    if t["type"] not in ("table", "format", "math", "baseline"):
        md = normalize_text(md)
    return runner(t, md)


# ---- 汇总 -----------------------------------------------------------------------


@dataclass
class OlmResult:
    suites: dict[str, dict[str, float]]   # suite → {pass_rate, n, missing}
    by_type: dict[str, dict[str, float]]
    overall: float                         # 各 suite 通过率的平均（官方口径）
    failures: list[dict]


def score(data_dir: Path, pred_dir: Path, only_present: bool = False) -> OlmResult:
    """only_present=True 时只评有预测输出的 PDF（部分跑的时候用）。"""
    stats: dict[str, Counter] = defaultdict(Counter)
    types: dict[str, Counter] = defaultdict(Counter)
    failures = []
    cache: dict[str, str | None] = {}
    for t in load_tests(data_dir):
        name = doc_name(t["pdf"])
        if name not in cache:
            p = pred_dir / f"{name}.md"
            cache[name] = p.read_text("utf-8") if p.exists() else None
        md = cache[name]
        if md is None and only_present:
            continue
        s = stats[t["_suite"]]
        if md is None:
            s["missing"] += 1
            passed = False
        else:
            result = run_test(t, md)
            if result is None:
                s["unsupported"] += 1
                continue
            passed = result
        s["n"] += 1
        s["pass"] += passed
        types[t["type"]]["n"] += 1
        types[t["type"]]["pass"] += passed
        if not passed and md is not None:
            failures.append({"suite": t["_suite"], "pdf": t["pdf"], "id": t.get("id"), "type": t["type"],
                             **{k: t[k] for k in ("text", "before", "after", "cell", "math") if k in t}})

    def rate(c: Counter) -> dict[str, float]:
        return {"pass_rate": round(c["pass"] / c["n"], 4) if c["n"] else 0.0, "n": c["n"],
                "missing": c["missing"], "unsupported": c["unsupported"]}

    suites = {k: rate(v) for k, v in sorted(stats.items())}
    overall = round(sum(v["pass_rate"] for v in suites.values()) / len(suites), 4) if suites else 0.0
    return OlmResult(suites, {k: rate(v) for k, v in sorted(types.items())}, overall, failures)
