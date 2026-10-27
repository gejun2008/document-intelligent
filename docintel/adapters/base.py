"""引擎接口。换模型、加模型只需要实现这两个协议之一。"""

from __future__ import annotations

import re
from typing import Protocol

from docintel.models import Recognition


class VLMEngine(Protocol):
    name: str
    version: str  # 进缓存 key：模型或 prompt 变了，旧缓存自动失效

    async def recognize(self, image_png: bytes, hint: str | None = None) -> Recognition: ...


class LayoutEngine(Protocol):
    name: str
    version: str

    async def analyze(self, pdf_bytes: bytes, page_count: int) -> list[Recognition]:
        """pdf_bytes 是只含待识别页的小 PDF；按页序返回结果。"""
        ...


_FENCE = re.compile(r"^```(?:markdown|md)?\s*\n(.*)\n```\s*$", re.S)


def strip_fences(text: str) -> str:
    """模型偶尔会把整页包进 ```markdown ... ```，去掉。"""
    text = text.strip()
    m = _FENCE.match(text)
    return m.group(1).strip() if m else text
