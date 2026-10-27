"""流水线里流转的数据结构。PageTask 要能跨进程 pickle，所以只放普通字段。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Route(str, Enum):
    NATIVE = "native"              # 原生解析（docx/xlsx/pptx），不调模型
    PDF_TEXT = "pdf_text"          # PDF 文本层直接抽取
    AZURE_DI = "azure_di"          # 扫描页 / 乱码页 → Azure DI prebuilt-layout
    VLM = "vlm"                    # 渲染页 → VLM
    VLM_GROUNDED = "vlm_grounded"  # 渲染页 + 原生文本提示 → VLM（pptx、图表页）
    IMAGE = "image"                # 文档内嵌图片 → VLM


@dataclass
class PageTask:
    doc_id: str
    unit: str                        # 文档内唯一：p3 / sheet2.1 / slide4 / img5
    order: int                       # 拼装顺序
    route: Route
    pdf_path: Path | None = None     # 需要渲染时的 PDF
    page_index: int | None = None    # pdf_path 里的 0-based 页号
    native_text: str | None = None   # 原生文本：给 VLM 当提示，也是质检的参考答案
    native_md: str | None = None     # NATIVE / PDF_TEXT 路由已产出的 markdown
    image_bytes: bytes | None = None  # IMAGE 路由
    placeholder: str | None = None   # IMAGE 路由：在正文里要替换掉的占位符
    label: str = ""                  # 人可读的位置，如 "Sheet1" / "第 3 页"


@dataclass
class Recognition:
    markdown: str
    engine: str
    truncated: bool = False
    refused: bool = False
    confidence: float | None = None  # OCR 引擎给的平均置信度
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class QualityReport:
    score: float
    verdict: str                     # pass | warn | fail
    issues: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass
class PageResult:
    task: PageTask
    markdown: str
    engine: str
    quality: QualityReport
    escalated: bool = False
    from_cache: bool = False
    error: str | None = None
    latency_s: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class DocResult:
    source: Path
    output: Path | None
    pages: int
    score: float                     # 页级分数的最小值：一份文档的质量取决于最差的页
    routes: dict[str, int]
    review_units: list[str]
    elapsed_s: float
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None
