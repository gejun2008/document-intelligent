"""流水线配置：dataclass 默认值，可用 TOML 文件覆盖；密钥只从环境变量读。"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any


@dataclass
class EngineLimits:
    max_concurrency: int = 8   # 同时在途的请求数
    rpm: int = 60              # 每分钟请求数，0 = 不限
    tpm: int = 0               # 每分钟 token 数（估算值），0 = 不限
    est_tokens_per_call: int = 4000
    breaker_failures: int = 5  # 连续失败多少次熔断
    breaker_cooldown_s: float = 30.0


@dataclass
class VLMConfig:
    provider: str = "claude"        # claude | openai_compat（vLLM / 其他自部署 VLM）
    model: str = "claude-opus-5-5"
    base_url: str | None = None     # openai_compat 必填，例如 http://vllm:8000
    api_key_env: str | None = None  # openai_compat 的 key 所在环境变量
    max_tokens: int = 16000
    effort: str | None = None       # 仅 claude：low|medium|high|xhigh|max；None 用模型默认
    server_fallbacks: bool = True   # 仅 claude 一方 API：拒答时服务端自动换模型重跑
    limits: EngineLimits = field(default_factory=EngineLimits)


@dataclass
class AzureConfig:
    endpoint_env: str = "AZURE_DI_ENDPOINT"
    key_env: str = "AZURE_DI_KEY"
    model_id: str = "prebuilt-layout"  # 不要用 prebuilt-read：没有表格/标题结构
    max_pages_per_request: int = 50
    limits: EngineLimits = field(default_factory=lambda: EngineLimits(max_concurrency=4, rpm=60))


@dataclass
class RoutingConfig:
    min_chars_per_page: int = 50        # PDF 文本层少于这个字数 → 当扫描页
    min_printable_ratio: float = 0.9    # 文本层可读字符占比低于这个 → 乱码（CID 字体）
    image_coverage_for_vlm: float = 0.5  # 图片覆盖面积超过这个比例 → 图表页走 VLM
    min_math_chars: int = 20            # 数学字体字符数达到这个 → 走 VLM 出 LaTeX（olmOCR 上 arxiv 95% 命中，其他 ≤5%）
    invisible_text_ratio: float = 0.8   # 不可见文字占比达到这个 → 扫描件叠旧 OCR 层，走 Azure 重新识别
    table_route: str = "vlm"            # 有表格的 PDF 页：vlm（看图定结构、文字用文本层校对）| azure | text（不改道）
    scanned_docx_max_chars: int = 20    # docx 正文字数不超过这个且有图片 → 当扫描件，渲染后走 PDF 路由
    vlm_for_pptx: bool = True           # pptx 是否渲染幻灯片走 grounded VLM
    include_hidden_sheets: bool = False
    fill_merged_cells: bool = True      # 合并单元格的值填到每个格（检索更友好）
    max_rows_per_table: int = 500       # 大表按行分块，每块重复表头
    min_image_bytes: int = 10_000       # 小于这个的内嵌图片（图标、logo）跳过


@dataclass
class RenderConfig:
    dpi: int = 170
    max_side: int = 2000  # 渲染图长边上限，控制 VLM token 成本


@dataclass
class QualityConfig:
    pass_score: float = 0.8     # ≥ 通过
    review_score: float = 0.5   # < 进人工复核；中间档先升级引擎重跑
    judge_sample_rate: float = 0.0  # LLM-as-judge 抽样比例（未通过的页总会被评）
    judge_model: str = "claude-opus-5-5"


@dataclass
class Config:
    out_dir: Path = Path("out")
    cache_dir: Path = Path(".cache/docintel")
    max_docs_in_flight: int = 4
    cpu_workers: int = field(default_factory=lambda: os.cpu_count() or 4)  # 0 = 不用进程池
    libreoffice_bin: str = "soffice"  # macOS: /Applications/LibreOffice.app/Contents/MacOS/soffice
    libreoffice_instances: int = 2
    libreoffice_timeout_s: int = 180
    keep_page_markers: bool = True    # 输出里保留 <!-- page N -->，方便 RAG 引用溯源
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    azure: AzureConfig = field(default_factory=AzureConfig)
    vlm: VLMConfig = field(default_factory=VLMConfig)
    vlm_strong: VLMConfig | None = None  # 低分页升级用的更强模型；None = 不升级
    quality: QualityConfig = field(default_factory=QualityConfig)


# 默认值为 None 的嵌套字段，TOML 里出现时用哪个类构造
_OPTIONAL_NESTED = {"vlm_strong": VLMConfig}


def _merge(obj: Any, data: dict[str, Any]) -> None:
    names = {f.name for f in fields(obj)}
    for key, value in data.items():
        if key not in names:
            raise ValueError(f"未知配置项: {type(obj).__name__}.{key}")
        current = getattr(obj, key)
        if current is None and key in _OPTIONAL_NESTED:
            current = _OPTIONAL_NESTED[key]()
            setattr(obj, key, current)
        if is_dataclass(current) and isinstance(value, dict):
            _merge(current, value)
        elif isinstance(current, Path):
            setattr(obj, key, Path(value))
        else:
            setattr(obj, key, value)


def load_config(path: str | Path | None = None) -> Config:
    cfg = Config()
    if path:
        with open(path, "rb") as f:
            _merge(cfg, tomllib.load(f))
    return cfg
