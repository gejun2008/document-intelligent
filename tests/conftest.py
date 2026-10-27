from __future__ import annotations

import io
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw

from docintel.config import Config
from docintel.models import Recognition


def png_bytes(text: str = "scan", size=(600, 300)) -> bytes:
    img = Image.new("RGB", size, "white")
    ImageDraw.Draw(img).text((20, 20), text, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """第 1 页有文本层；第 2 页只有一张图（模拟扫描页）；第 3 页又是文本。"""
    path = tmp_path / "sample.pdf"
    doc = pymupdf.open()
    bodies = {1: "Revenue grew 12.5 percent to 3,400 units in the north region.",
              3: "Operating costs stayed flat at 980 while margin reached 41 percent."}
    for i in (1, 3):
        page = doc.new_page(pno=-1)
        page.insert_text((72, 72), f"Quarterly report page {i}", fontsize=14)
        page.insert_text((72, 100), bodies[i], fontsize=10)
        if i == 1:
            scan = doc.new_page(pno=-1)
            scan.insert_image(scan.rect, stream=png_bytes())
    doc.save(path)
    return path


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    c = Config(out_dir=tmp_path / "out", cache_dir=tmp_path / "cache", cpu_workers=0)
    c.libreoffice_bin = "definitely-not-installed-soffice"
    c.routing.min_image_bytes = 100
    return c


class FakeVLM:
    def __init__(self, name: str = "fake-vlm", markdown: str | None = None):
        self.name = name
        self.version = name + "/v1"
        self.markdown = markdown
        self.calls = 0

    async def recognize(self, image_png: bytes, hint: str | None = None) -> Recognition:
        self.calls += 1
        md = self.markdown if self.markdown is not None else f"# 识别结果\n\n{(hint or '图片内容').strip()}"
        return Recognition(markdown=md, engine=self.name, usage={"input_tokens": 10, "output_tokens": 5})


class FakeLayout:
    def __init__(self, markdown: str = "# 扫描页\n\n这是一段 OCR 出来的文字，内容完整。"):
        self.name = "fake-azure"
        self.version = "fake-azure/v1"
        self.markdown = markdown
        self.calls = 0
        self.pages_seen: list[int] = []

    async def analyze(self, pdf_bytes: bytes, page_count: int) -> list[Recognition]:
        self.calls += 1
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as d:
            assert len(d) == page_count  # 只上传了需要 OCR 的页
        self.pages_seen.append(page_count)
        return [Recognition(markdown=self.markdown, engine=self.name, confidence=0.98) for _ in range(page_count)]
