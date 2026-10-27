"""LLM-as-judge：把页面图和转写结果一起给强模型按评分标准打分。

成本高，只用于抽样页和规则检查没通过的页。
"""

from __future__ import annotations

import base64
import json

import anthropic

JUDGE_SYSTEM = """你是文档转写质检员。对照页面图片，评估给出的 markdown 转写结果。
每项 1-5 分（5 = 完全正确）：
- completeness：图片里的内容有没有遗漏（段落、表格行、图中文字）
- faithfulness：有没有图片里不存在的内容、错字、错数字
- structure：标题层级、列表、表格结构、阅读顺序是否正确
problems 列出具体问题（最多 5 条，每条一句话），没有就给空数组。"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "completeness": {"type": "integer"},
        "faithfulness": {"type": "integer"},
        "structure": {"type": "integer"},
        "problems": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["completeness", "faithfulness", "structure", "problems"],
    "additionalProperties": False,
}


class ClaudeJudge:
    def __init__(self, model: str, client: anthropic.AsyncAnthropic | None = None):
        self.model = model
        self.client = client or anthropic.AsyncAnthropic()

    async def judge(self, image_png: bytes, markdown: str) -> dict:
        resp = await self.client.beta.messages.create(
            model=self.model,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=JUDGE_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.standard_b64encode(image_png).decode()}},
                {"type": "text", "text": f"<transcription>\n{markdown[:60000]}\n</transcription>"},
            ]}],
        )
        if resp.stop_reason == "refusal":
            return {}
        data = json.loads("".join(b.text for b in resp.content if b.type == "text"))
        # 1-5 映射到 0-1，取最差项
        data["score"] = (min(data["completeness"], data["faithfulness"], data["structure"]) - 1) / 4
        return data
