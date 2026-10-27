"""按配置构造引擎；SDK 依赖延迟导入，没用到的引擎不需要凭据。"""

from __future__ import annotations

from docintel.adapters.base import LayoutEngine, VLMEngine
from docintel.config import AzureConfig, VLMConfig


def build_vlm(cfg: VLMConfig) -> VLMEngine:
    if cfg.provider == "claude":
        from docintel.adapters.claude_vlm import ClaudeVLM
        return ClaudeVLM(cfg)
    if cfg.provider == "openai_compat":
        from docintel.adapters.openai_compat_vlm import OpenAICompatVLM
        return OpenAICompatVLM(cfg)
    raise ValueError(f"未知 VLM provider: {cfg.provider}")


def build_layout(cfg: AzureConfig) -> LayoutEngine:
    from docintel.adapters.azure_di import AzureLayout
    return AzureLayout(cfg)


__all__ = ["LayoutEngine", "VLMEngine", "build_layout", "build_vlm"]
