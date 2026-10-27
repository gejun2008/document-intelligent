import asyncio
import json
from pathlib import Path

import pytest

from docintel.infra.retry import CircuitBreaker, CircuitOpenError, RetryableError, with_retry
from docintel.pipeline import Pipeline

from conftest import FakeLayout, FakeVLM


def _run(cfg, files, **engines):
    async def go():
        async with Pipeline(cfg, **engines) as pipe:
            return await pipe.run(files)
    return asyncio.run(go())


def test_pdf_end_to_end_with_cache(cfg, sample_pdf: Path):
    layout, vlm = FakeLayout(), FakeVLM()
    [doc] = _run(cfg, [sample_pdf], layout=layout, vlm=vlm)
    assert doc.error is None
    assert doc.routes == {"pdf_text": 2, "azure_di": 1}
    assert layout.pages_seen == [1] and vlm.calls == 0  # 文本页没调模型，扫描页只传了 1 页

    md = doc.output.read_text("utf-8")
    assert md.index("Quarterly report page 1") < md.index("扫描页") < md.index("Quarterly report page 3")
    report = json.loads(doc.output.with_suffix(".quality.json").read_text("utf-8"))
    assert [p["route"] for p in report["pages"]] == ["pdf_text", "azure_di", "pdf_text"]

    # 第二次跑：Azure 页命中缓存
    [again] = _run(cfg, [sample_pdf], layout=layout, vlm=vlm)
    assert layout.calls == 1 and again.output.read_text("utf-8") == md


def test_bad_ocr_escalates_to_strong_vlm(cfg, sample_pdf: Path):
    layout = FakeLayout(markdown="")  # OCR 啥也没认出来
    strong = FakeVLM("strong", markdown="# 强模型重识别\n\n扫描页的完整内容。")
    [doc] = _run(cfg, [sample_pdf], layout=layout, vlm=FakeVLM(), vlm_strong=strong)
    report = json.loads(doc.output.with_suffix(".quality.json").read_text("utf-8"))
    scan = report["pages"][1]
    assert strong.calls == 1 and scan["engine"] == "strong" and scan["escalated"]
    assert "强模型重识别" in doc.output.read_text("utf-8")


def test_engine_failure_is_isolated(cfg, sample_pdf: Path):
    class Broken(FakeLayout):
        async def analyze(self, pdf_bytes, page_count):
            raise ValueError("boom")

    [doc] = _run(cfg, [sample_pdf], layout=Broken(), vlm=FakeVLM(markdown=""))
    assert doc.error is None and doc.review_units == ["p2"]  # 其他页照常产出
    assert "Quarterly report page 3" in doc.output.read_text("utf-8")
    review = (cfg.out_dir / "review.jsonl").read_text("utf-8").splitlines()
    assert json.loads(review[0])["unit"] == "p2"


def test_process_pool(cfg, sample_pdf: Path):
    cfg.cpu_workers = 2
    [doc] = _run(cfg, [sample_pdf, sample_pdf], layout=FakeLayout(), vlm=FakeVLM())[:1]
    assert doc.error is None and doc.pages == 3


def test_retry_and_breaker():
    attempts = 0

    async def flaky():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RetryableError("429")
        return "ok"

    assert asyncio.run(with_retry(flaky, base_s=0.01)) == "ok" and attempts == 3

    breaker = CircuitBreaker(failures=2, cooldown_s=60)

    async def always_fail():
        raise RetryableError("503")

    with pytest.raises(CircuitOpenError):
        asyncio.run(with_retry(always_fail, attempts=5, base_s=0.001, breaker=breaker))
