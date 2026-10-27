"""自部署 VLM（vLLM / SGLang / LMDeploy 等）走 OpenAI 兼容的 /v1/chat/completions。"""

from __future__ import annotations

import base64
import os

import httpx

from docintel.adapters.base import strip_fences
from docintel.adapters.prompts import OCR_SYSTEM, PROMPT_VERSION, user_text
from docintel.config import VLMConfig
from docintel.infra.retry import RetryableError
from docintel.models import Recognition


class OpenAICompatVLM:
    def __init__(self, cfg: VLMConfig, client: httpx.AsyncClient | None = None):
        if not cfg.base_url:
            raise ValueError("openai_compat 需要配置 base_url")
        self.cfg = cfg
        headers = {}
        if cfg.api_key_env and os.environ.get(cfg.api_key_env):
            headers["Authorization"] = f"Bearer {os.environ[cfg.api_key_env]}"
        self.client = client or httpx.AsyncClient(base_url=cfg.base_url, headers=headers, timeout=300)
        self.name = f"vlm:{cfg.model}"
        self.version = f"{cfg.model}/{PROMPT_VERSION}"

    async def recognize(self, image_png: bytes, hint: str | None = None) -> Recognition:
        data_uri = "data:image/png;base64," + base64.b64encode(image_png).decode()
        body = {
            "model": self.cfg.model,
            "max_tokens": self.cfg.max_tokens,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": OCR_SYSTEM},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": data_uri}},
                    {"type": "text", "text": user_text(hint)},
                ]},
            ],
        }
        try:
            r = await self.client.post("/v1/chat/completions", json=body)
        except httpx.TransportError as e:
            raise RetryableError(f"连接错误: {e}") from e
        if r.status_code == 429 or r.status_code >= 500:
            ra = r.headers.get("retry-after")
            raise RetryableError(f"{r.status_code} {r.text[:200]}", float(ra) if ra and ra.isdigit() else None)
        r.raise_for_status()

        data = r.json()
        choice = data["choices"][0]
        usage = data.get("usage") or {}
        return Recognition(
            markdown=strip_fences(choice["message"].get("content") or ""),
            engine=self.name,
            truncated=choice.get("finish_reason") == "length",
            usage={"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)},
        )
