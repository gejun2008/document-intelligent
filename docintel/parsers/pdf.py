"""PDF 逐页分析：判断文本层能不能用，能用就直接抽 markdown。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import pymupdf

try:
    import pymupdf4llm
except ImportError:  # 没装就退化成纯文本
    pymupdf4llm = None


@dataclass
class PageStats:
    chars: int
    printable_ratio: float
    image_coverage: float
    math_chars: int = 0             # 数学字体里的字符数：文本层只有 Unicode 符号，出不了 LaTeX
    invisible_ratio: float = 0.0    # 不可见文字占比：扫描件上叠的旧 OCR 层，质量未知
    tables: int = 0                 # 检测到的表格数：文本层只能猜表格结构，行列容易错位


# TeX / Office 常见数学字体
_MATH_FONT = re.compile(r"CMMI|CMSY|CMEX|CMBSY|MSAM|MSBM|EUFM|EUSM|RSFS|Math|STIX|txsy|txmi|txex|pxmi|pxsy|"
                        r"mtmi|MTSY|rtxmi|Symbol|Euler", re.I)
_MD_TABLE = re.compile(r"^\|.*\|[ \t]*\n\|[ \t]*:?-{3,}", re.M)
_PICTURE_TEXT = re.compile(r"^\s*<!-- (?:Start|End) of picture text -->\s*$", re.M)


def _is_readable(ch: str) -> bool:
    if ch.isspace():
        return True
    if ch == "�":
        return False
    cat = unicodedata.category(ch)
    # 私有区 (Co)、控制符 (Cc)、未分配 (Cn) 基本都是 CID 字体映射坏掉的乱码
    return cat[0] in "LNPS" and cat not in ("Co", "Cn")


def count_tables(page: pymupdf.Page) -> int:
    """按框线找表格（pymupdf 默认的 lines 策略），只算至少 2 行 2 列的，排除文字框。

    olmOCR 表格组里走文本层的页能找到 91%，其他组抽查命中的也都是真表格；每页约 0.05–0.1 秒。
    无框线的表格找不到，由 plan_pdf 用 pymupdf4llm 的输出兜底。
    """
    try:
        found = page.find_tables().tables
    except Exception:  # 个别畸形页面会让表格检测抛错，当作没有表格
        return 0
    return sum(1 for t in found if t.row_count >= 2 and t.col_count >= 2)


def has_md_table(md: str) -> bool:
    return bool(_MD_TABLE.search(md))


def analyze_page(page: pymupdf.Page, detect_tables: bool = True) -> PageStats:
    text = page.get_text("text")
    stripped = [c for c in text if not c.isspace()]
    chars = len(stripped)
    printable = sum(1 for c in stripped if _is_readable(c)) / chars if chars else 0.0

    page_area = abs(page.rect) or 1.0
    covered = 0.0
    for info in page.get_image_info():
        bbox = pymupdf.Rect(info["bbox"]) & page.rect
        if not bbox.is_empty:
            covered += abs(bbox)

    math_chars = 0
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if _MATH_FONT.search(span["font"]):
                    math_chars += len(span["text"].strip())

    traces = page.get_texttrace()
    total = sum(len(t["chars"]) for t in traces)
    invisible = sum(len(t["chars"]) for t in traces if t["type"] == 3)  # 渲染模式 3 = 不可见
    return PageStats(chars=chars, printable_ratio=printable, image_coverage=min(covered / page_area, 1.0),
                     math_chars=math_chars, invisible_ratio=invisible / total if total else 0.0,
                     tables=count_tables(page) if detect_tables else 0)


_CJK_BR = re.compile(r"(?<=[㐀-鿿＀-￯])<br>(?=[㐀-鿿＀-￯])")


def _join_cell_wraps(md: str) -> str:
    """PDF 表格里窄列的自动换行会被抽成 <br>（"苏<br>州"），这是排版换行不是内容，合回去。"""
    lines = []
    for line in md.splitlines():
        if line.lstrip().startswith("|") and "<br>" in line:
            line = _CJK_BR.sub("", line).replace("<br>", " ")
        lines.append(line)
    return "\n".join(lines)


def pages_markdown(doc: pymupdf.Document, indexes: list[int]) -> dict[int, str]:
    """一次处理整份文档的这些页：标题层级要按全文字号统计，逐页调用会每页各自定级。"""
    if not indexes:
        return {}
    if pymupdf4llm is None:
        return {i: doc[i].get_text("text").strip() for i in indexes}
    kwargs = dict(pages=indexes, page_chunks=True, show_progress=False)
    try:
        # 走到这里的页文本层是好的；OCR 交给 Azure/VLM 路由，不让 pymupdf4llm 自己做。
        # header/footer=False 用版面模型去页眉页脚，单页文档也有效（统计法至少要 4 页）
        chunks = pymupdf4llm.to_markdown(doc, use_ocr=False, header=False, footer=False, **kwargs)
    except TypeError:  # 旧版本没有这些参数
        chunks = pymupdf4llm.to_markdown(doc, **kwargs)
    return {i: _PICTURE_TEXT.sub("", _join_cell_wraps(c["text"])).strip() for i, c in zip(indexes, chunks)}
