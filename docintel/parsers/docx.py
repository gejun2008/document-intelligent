"""docx 原生解析：mammoth 转 HTML（保留标题样式、列表、表格），再转 markdown。

内嵌图片在正文里换成占位符 `docintel-img:N`，图片本身交给 VLM，拼装时回填。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import mammoth
from markdownify import markdownify

PLACEHOLDER = "docintel-img:{}"
# VLM 不收 emf/wmf/tiff 这些格式
VLM_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


_TABLE_SEP = re.compile(r"^\|?\s*:?-{3,}")
_BOLD_CELL = re.compile(r"\|\s*\*\*(.+?)\*\*\s*(?=\|)")


def _clean_table_headers(md: str) -> str:
    """表头行本身就是表头，去掉作者手动加的整格加粗。"""
    lines = md.splitlines()
    for i in range(len(lines) - 1):
        if lines[i].lstrip().startswith("|") and _TABLE_SEP.match(lines[i + 1].strip()):
            lines[i] = _BOLD_CELL.sub(lambda m: f"| {m.group(1)} ", lines[i])
    return "\n".join(lines)


class NeedsRender(Exception):
    """原生解析拿不到正文（比如整篇是扫描图片塞进 docx），应改走 LibreOffice→PDF。"""


@dataclass
class DocxImage:
    index: int
    content_type: str
    data: bytes


def parse_docx(path: Path, *, scanned_max_chars: int, min_image_bytes: int) -> tuple[str, str, list[DocxImage]]:
    images: list[DocxImage] = []

    def on_image(image: mammoth.documents.Image) -> dict[str, str]:
        with image.open() as f:
            data = f.read()
        if image.content_type not in VLM_IMAGE_TYPES or len(data) < min_image_bytes:
            return {"src": "", "alt": image.alt_text or ""}
        n = len(images)
        images.append(DocxImage(n, image.content_type, data))
        return {"src": PLACEHOLDER.format(n), "alt": image.alt_text or ""}

    with open(path, "rb") as f:
        result = mammoth.convert_to_html(f, convert_image=mammoth.images.img_element(on_image))
    # Word 表格没有表头语义；不推断的话 markdownify 会生成一行空表头，真表头变成数据行
    md = markdownify(result.value, heading_style="ATX", bullets="-", table_infer_header=True).strip()
    md = _clean_table_headers("\n".join(line.rstrip() for line in md.splitlines()))
    while "\n\n\n" in md:
        md = md.replace("\n\n\n", "\n\n")

    with open(path, "rb") as f:
        text = mammoth.extract_raw_text(f).value
    if len("".join(text.split())) <= scanned_max_chars and images:
        raise NeedsRender(str(path))
    return md, text, images
