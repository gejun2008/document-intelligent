"""渲染：PDF 页 → PNG，图片归一化成 VLM 能收的 PNG。都在进程池里跑。"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
from PIL import Image


def render_page(pdf_path: Path, page_index: int, dpi: int, max_side: int) -> bytes:
    with pymupdf.open(pdf_path) as doc:
        page = doc[page_index]
        zoom = dpi / 72
        longest = max(page.rect.width, page.rect.height) * zoom
        if longest > max_side:
            zoom *= max_side / longest
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png")


def normalize_image(data: bytes, max_side: int) -> bytes:
    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


def slice_pdf(pdf_path: Path, page_indexes: list[int]) -> bytes:
    """抽出若干页组成新 PDF，只上传需要 OCR 的页，省带宽也避开大文件限制。"""
    with pymupdf.open(pdf_path) as src, pymupdf.open() as dst:
        for i in page_indexes:
            dst.insert_pdf(src, from_page=i, to_page=i)
        return dst.tobytes()
