"""Azure Document Intelligence prebuilt-layout，直接输出 markdown。

一次提交多页（调用方先把待 OCR 的页切成小 PDF），再按每页的 spans 把整段 content 切回各页。
"""

from __future__ import annotations

import os
import re
from statistics import fmean

from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import AnalyzeDocumentRequest, DocumentContentFormat
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError, ServiceRequestError, ServiceResponseError

from docintel.config import AzureConfig
from docintel.infra.retry import RetryableError
from docintel.models import Recognition

_COMMENT = re.compile(r"<!--.*?-->", re.S)  # PageHeader / PageFooter / PageNumber / PageBreak 标记


class AzureLayout:
    def __init__(self, cfg: AzureConfig, client: DocumentIntelligenceClient | None = None):
        self.cfg = cfg
        self.client = client or DocumentIntelligenceClient(
            os.environ[cfg.endpoint_env], AzureKeyCredential(os.environ[cfg.key_env]))
        self.name = f"azure:{cfg.model_id}"
        self.version = f"{cfg.model_id}/markdown"

    async def analyze(self, pdf_bytes: bytes, page_count: int) -> list[Recognition]:
        try:
            poller = await self.client.begin_analyze_document(
                self.cfg.model_id,
                AnalyzeDocumentRequest(bytes_source=pdf_bytes),
                output_content_format=DocumentContentFormat.MARKDOWN,
                string_index_type="unicodeCodePoint",  # 让 span offset 和 Python str 下标一致
            )
            result = await poller.result()
        except HttpResponseError as e:
            if e.status_code == 429 or (e.status_code or 0) >= 500:
                ra = e.response.headers.get("retry-after") if e.response is not None else None
                raise RetryableError(f"Azure {e.status_code}: {e.message}", float(ra) if ra else None) from e
            raise
        except (ServiceRequestError, ServiceResponseError) as e:
            raise RetryableError(f"Azure 连接错误: {e}") from e

        content = result.content or ""
        pages = sorted(result.pages or [], key=lambda p: p.page_number)
        if pages and all(p.spans for p in pages):
            texts = ["".join(content[s.offset:s.offset + s.length] for s in p.spans) for p in pages]
        else:
            texts = content.split("<!-- PageBreak -->")

        out = []
        for i in range(page_count):
            md = _COMMENT.sub("", texts[i]).strip() if i < len(texts) else ""
            words = pages[i].words if i < len(pages) and pages[i].words else []
            conf = fmean(w.confidence for w in words) if words else None
            out.append(Recognition(markdown=md, engine=self.name, confidence=conf, usage={"pages": 1}))
        return out
