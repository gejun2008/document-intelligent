"""Claude 作为 VLM。"""

from __future__ import annotations

import base64

import anthropic

from docintel.adapters.base import strip_fences
from docintel.adapters.prompts import OCR_SYSTEM, PROMPT_VERSION, user_text
from docintel.config import VLMConfig
from docintel.infra.retry import RetryableError
from docintel.models import Recognition


def _retry_after(e: anthropic.APIStatusError) -> float | None:
    value = e.response.headers.get("retry-after")
    try:
        return float(value) if value else None
    except ValueError:
        return None


class ClaudeVLM:
    def __init__(self, cfg: VLMConfig, client: anthropic.AsyncAnthropic | None = None):
        self.cfg = cfg
        # 重试交给流水线统一处理（限流、熔断、退避在一处）
        self.client = client or anthropic.AsyncAnthropic(max_retries=0)
        self.name = f"claude:{cfg.model}"
        self.version = f"{cfg.model}/{cfg.effort or 'default'}/{PROMPT_VERSION}"

    async def recognize(self, image_png: bytes, hint: str | None = None) -> Recognition:
        kwargs: dict = {
            "model": self.cfg.model,
            "max_tokens": self.cfg.max_tokens,
            "system": OCR_SYSTEM,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.standard_b64encode(image_png).decode()}},
                {"type": "text", "text": user_text(hint)},
            ]}],
        }
        if self.cfg.effort:
            kwargs["output_config"] = {"effort": self.cfg.effort}
        if self.cfg.server_fallbacks:
            # 安全分类器误拒时，服务端按拒答类别自动换模型重跑（仅 Claude 一方 API）
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"

        try:
            resp = await self.client.beta.messages.create(**kwargs)
        except anthropic.RateLimitError as e:
            raise RetryableError(f"429 {e.message}", _retry_after(e)) from e
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                raise RetryableError(f"{e.status_code} {e.message}", _retry_after(e)) from e
            raise
        except anthropic.APIConnectionError as e:
            raise RetryableError(f"连接错误: {e}") from e

        text = "".join(b.text for b in resp.content if b.type == "text")
        return Recognition(
            markdown=strip_fences(text),
            engine=self.name,
            truncated=resp.stop_reason == "max_tokens",
            refused=resp.stop_reason == "refusal",
            usage={"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens},
        )
