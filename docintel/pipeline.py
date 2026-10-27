"""编排：文档级并发 + 页级并发，CPU 活进进程池，模型调用按引擎限流。

    文档 ──哈希──▶ 规划（LibreOffice 池 / 进程池里做路由和原生解析）
         ──▶ 页级识别（渲染进进程池；Azure / VLM 各自限流、重试、熔断；页级缓存）
         ──▶ 质检打分 ──低分──▶ 升级引擎重跑一次 ──仍低分──▶ 复核队列
         ──▶ 拼装输出 .md + .quality.json
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from docintel.adapters import LayoutEngine, VLMEngine, build_layout, build_vlm
from docintel.assemble import assemble
from docintel.config import Config, EngineLimits
from docintel.convert.libreoffice import ConversionError, LibreOfficePool
from docintel.convert.render import normalize_image, render_page, slice_pdf
from docintel.infra.cache import PageCache, file_sha256
from docintel.infra.ratelimit import RateLimiter
from docintel.infra.retry import CircuitBreaker, with_retry
from docintel.models import DocResult, PageResult, PageTask, QualityReport, Recognition, Route
from docintel.quality import assess
from docintel.router import (IMAGE_SUFFIXES, LEGACY_UPGRADE, NeedsRender, plan_docx, plan_image, plan_pdf,
                             plan_pptx, plan_xlsx)

log = logging.getLogger("docintel")
T = TypeVar("T")

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".xlsx", ".xlsm", ".pptx", *LEGACY_UPGRADE, *IMAGE_SUFFIXES}
_NO_MODEL = {Route.NATIVE, Route.PDF_TEXT}


class Pipeline:
    def __init__(self, cfg: Config, *, vlm: VLMEngine | None = None, vlm_strong: VLMEngine | None = None,
                 layout: LayoutEngine | None = None, judge: Any | None = None):
        self.cfg = cfg
        self._vlm, self._vlm_strong, self._layout, self._judge = vlm, vlm_strong, layout, judge
        self.cache = PageCache(cfg.cache_dir / "pages")
        self.lo = LibreOfficePool(cfg.libreoffice_bin, cfg.libreoffice_instances, cfg.libreoffice_timeout_s,
                                  cfg.cache_dir / "lo")
        self._guards: dict[str, tuple[RateLimiter, CircuitBreaker]] = {}
        self._pool: ProcessPoolExecutor | None = None

    # ---- 生命周期 ---------------------------------------------------------

    async def __aenter__(self) -> Pipeline:
        if self.cfg.cpu_workers > 0:
            self._pool = ProcessPoolExecutor(self.cfg.cpu_workers)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._pool:
            self._pool.shutdown(cancel_futures=True)
            self._pool = None

    # ---- 引擎（延迟构造：没用到就不需要凭据）------------------------------

    @property
    def vlm(self) -> VLMEngine:
        if self._vlm is None:
            self._vlm = build_vlm(self.cfg.vlm)
        return self._vlm

    @property
    def vlm_strong(self) -> VLMEngine | None:
        if self._vlm_strong is None and self.cfg.vlm_strong is not None:
            self._vlm_strong = build_vlm(self.cfg.vlm_strong)
        return self._vlm_strong

    @property
    def layout(self) -> LayoutEngine:
        if self._layout is None:
            self._layout = build_layout(self.cfg.azure)
        return self._layout

    @property
    def judge(self) -> Any | None:
        if self._judge is None and self.cfg.quality.judge_sample_rate > 0:
            from docintel.quality.judge import ClaudeJudge
            self._judge = ClaudeJudge(self.cfg.quality.judge_model)
        return self._judge

    def _limits_for(self, engine: VLMEngine | LayoutEngine) -> EngineLimits:
        if engine is self._layout:
            return self.cfg.azure.limits
        if engine is self._vlm_strong and self.cfg.vlm_strong:
            return self.cfg.vlm_strong.limits
        return self.cfg.vlm.limits

    # ---- 执行原语 ---------------------------------------------------------

    async def _cpu(self, fn: Callable[..., T], *args: Any) -> T:
        if self._pool is None:
            return await asyncio.to_thread(fn, *args)
        return await asyncio.get_running_loop().run_in_executor(self._pool, partial(fn, *args))

    async def _call(self, engine: VLMEngine | LayoutEngine, fn: Callable[[], Awaitable[T]]) -> T:
        limits = self._limits_for(engine)
        if engine.name not in self._guards:
            self._guards[engine.name] = (
                RateLimiter(limits.max_concurrency, limits.rpm, limits.tpm),
                CircuitBreaker(limits.breaker_failures, limits.breaker_cooldown_s),
            )
        limiter, breaker = self._guards[engine.name]

        async def once() -> T:
            async with limiter.slot(limits.est_tokens_per_call):  # 退避等待发生在 slot 之外，不占并发
                return await fn()

        return await with_retry(once, breaker=breaker)

    # ---- 入口 -------------------------------------------------------------

    async def run(self, paths: list[Path]) -> list[DocResult]:
        self.cfg.out_dir.mkdir(parents=True, exist_ok=True)
        sem = asyncio.Semaphore(self.cfg.max_docs_in_flight)  # 文档级背压：限制同时展开的页数

        async def one(p: Path) -> DocResult:
            async with sem:
                return await self.process(p)

        return await asyncio.gather(*(one(Path(p)) for p in paths))

    async def plan(self, path: Path, doc_id: str | None = None) -> list[PageTask]:
        doc_id = doc_id or (await asyncio.to_thread(file_sha256, path))[:16]
        suffix = path.suffix.lower()
        work = self.cfg.cache_dir / "work" / doc_id
        r = self.cfg.routing
        if suffix in LEGACY_UPGRADE:
            path = await self.lo.convert(path, LEGACY_UPGRADE[suffix], work)
            suffix = path.suffix.lower()
        if suffix == ".pdf":
            return await self._cpu(plan_pdf, path, doc_id, r)
        if suffix == ".docx":
            try:
                return await self._cpu(plan_docx, path, doc_id, r)
            except NeedsRender:
                log.info("%s 正文几乎全是图片，改走 PDF 渲染", path.name)
                return await self._cpu(plan_pdf, await self.lo.convert(path, "pdf", work), doc_id, r)
        if suffix in (".xlsx", ".xlsm"):
            return await self._cpu(plan_xlsx, path, doc_id, r)
        if suffix == ".pptx":
            pdf = None
            if r.vlm_for_pptx and self.lo.available:
                try:
                    pdf = await self.lo.convert(path, "pdf", work)
                except ConversionError as e:
                    log.warning("pptx 渲染失败，退化为纯原生解析: %s", e)
            return await self._cpu(plan_pptx, path, doc_id, pdf, r)
        if suffix in IMAGE_SUFFIXES:
            return await self._cpu(plan_image, path, doc_id)
        raise ValueError(f"不支持的文件类型: {path.suffix}")

    async def process(self, path: Path) -> DocResult:
        t0 = time.monotonic()
        try:
            file_hash = await asyncio.to_thread(file_sha256, path)
            tasks = await self.plan(path, file_hash[:16])
            results = await self._recognize(tasks, file_hash)
            md = assemble(results, self.cfg.keep_page_markers)

            out = self.cfg.out_dir / f"{path.stem}.md"
            out.write_text(md, "utf-8")
            self._write_report(path, out, results)

            usage: Counter[str] = Counter()
            for r in results:
                usage.update(r.usage)
            doc = DocResult(
                source=path, output=out, pages=len(results),
                score=min((r.quality.score for r in results), default=1.0),
                routes=dict(Counter(r.task.route.value for r in results)),
                review_units=[r.task.unit for r in results if r.quality.verdict != "pass"],
                elapsed_s=round(time.monotonic() - t0, 2), usage=dict(usage),
            )
            log.info("%s：%d 页，最低分 %.2f，待复核 %d 页，%.1fs", path.name, doc.pages, doc.score,
                     len(doc.review_units), doc.elapsed_s)
            return doc
        except Exception as e:
            log.exception("%s 处理失败", path)
            return DocResult(source=path, output=None, pages=0, score=0.0, routes={}, review_units=[],
                             elapsed_s=round(time.monotonic() - t0, 2), error=f"{type(e).__name__}: {e}")

    # ---- 页级识别 ---------------------------------------------------------

    async def _recognize(self, tasks: list[PageTask], file_hash: str) -> list[PageResult]:
        jobs: list[Awaitable[list[PageResult]]] = []
        azure_by_pdf: dict[Path, list[PageTask]] = defaultdict(list)
        for t in tasks:
            if t.route in _NO_MODEL:
                jobs.append(self._native(t, file_hash))
            elif t.route is Route.AZURE_DI:
                azure_by_pdf[t.pdf_path].append(t)
            else:
                jobs.append(self._single(self._vlm_page(t, file_hash, self.vlm), file_hash))
        for pdf, group in azure_by_pdf.items():
            jobs.append(self._azure_group(pdf, group, file_hash))
        nested = await asyncio.gather(*jobs)
        return [r for batch in nested for r in batch]

    async def _single(self, coro: Awaitable[PageResult], file_hash: str) -> list[PageResult]:
        return [await self._finalize(await coro, file_hash)]

    async def _native(self, task: PageTask, file_hash: str) -> list[PageResult]:
        rec = Recognition(markdown=task.native_md or "", engine=task.route.value)
        res = PageResult(task=task, markdown=rec.markdown, engine=rec.engine,
                         quality=assess(rec, self.cfg.quality, expect_content=False))
        return [await self._finalize(res, file_hash)]

    async def _vlm_page(self, task: PageTask, file_hash: str, engine: VLMEngine) -> PageResult:
        hint = task.native_text  # 只有文本层可信时才有值；Azure 表格页升级到 VLM 时也用得上
        key = PageCache.key(file_hash, task.unit, engine.version, bool(hint))
        t0 = time.monotonic()
        cached = self.cache.get(key)
        try:
            if cached:
                rec = Recognition(**cached)
            else:
                image = await self._image_for(task)
                rec = await self._call(engine, lambda: engine.recognize(image, hint))
                if rec.markdown and not rec.refused:
                    self.cache.put(key, asdict(rec))
        except Exception as e:
            return self._failed(task, engine.name, e)
        # grounded 时原生文本就是参考答案：可以直接量出漏字和幻觉
        quality = assess(rec, self.cfg.quality, ref_text=hint)
        return PageResult(task=task, markdown=rec.markdown, engine=rec.engine, quality=quality,
                          from_cache=cached is not None, latency_s=round(time.monotonic() - t0, 2),
                          usage=rec.usage)

    async def _azure_group(self, pdf: Path, tasks: list[PageTask], file_hash: str) -> list[PageResult]:
        engine = self.layout
        results: dict[str, PageResult] = {}
        todo: list[PageTask] = []
        for t in tasks:
            cached = self.cache.get(PageCache.key(file_hash, t.unit, engine.version))
            if cached:
                rec = Recognition(**cached)
                results[t.unit] = PageResult(task=t, markdown=rec.markdown, engine=rec.engine, from_cache=True,
                                             quality=assess(rec, self.cfg.quality, ref_text=t.native_text),
                                             usage=rec.usage)
            else:
                todo.append(t)

        size = self.cfg.azure.max_pages_per_request

        async def chunk(group: list[PageTask]) -> None:
            t0 = time.monotonic()
            try:
                data = await self._cpu(slice_pdf, pdf, [t.page_index for t in group])
                recs = await self._call(engine, lambda: engine.analyze(data, len(group)))
            except Exception as e:
                for t in group:
                    results[t.unit] = self._failed(t, engine.name, e)
                return
            per_page = round((time.monotonic() - t0) / len(group), 2)
            for t, rec in zip(group, recs):
                self.cache.put(PageCache.key(file_hash, t.unit, engine.version), asdict(rec))
                results[t.unit] = PageResult(task=t, markdown=rec.markdown, engine=rec.engine, usage=rec.usage,
                                             quality=assess(rec, self.cfg.quality, ref_text=t.native_text),
                                             latency_s=per_page)

        await asyncio.gather(*(chunk(todo[i:i + size]) for i in range(0, len(todo), size)))
        return list(await asyncio.gather(*(self._finalize(results[t.unit], file_hash) for t in tasks)))

    async def _image_for(self, task: PageTask) -> bytes:
        if task.image_bytes is not None:
            return await self._cpu(normalize_image, task.image_bytes, self.cfg.render.max_side)
        return await self._cpu(render_page, task.pdf_path, task.page_index, self.cfg.render.dpi,
                               self.cfg.render.max_side)

    @staticmethod
    def _failed(task: PageTask, engine: str, e: Exception) -> PageResult:
        log.warning("%s/%s 识别失败（%s）: %s", task.doc_id, task.unit, engine, e)
        return PageResult(task=task, markdown="", engine=engine, error=f"{type(e).__name__}: {e}",
                          quality=QualityReport(score=0.0, verdict="fail", issues=[f"引擎失败：{e}"]))

    # ---- 升级与抽检 -------------------------------------------------------

    def _escalation_engine(self, task: PageTask) -> VLMEngine | None:
        if task.pdf_path is None and task.image_bytes is None:
            return None  # docx/xlsx 原生结果没有可渲染的页面
        if self.vlm_strong is not None:
            return self.vlm_strong
        # 没配强模型时：OCR / 文本层失败的页还可以换 VLM；本来就是 VLM 的页没地方升级
        return self.vlm if task.route in (Route.AZURE_DI, Route.PDF_TEXT) else None

    async def _finalize(self, res: PageResult, file_hash: str) -> PageResult:
        if res.quality.verdict != "pass":
            engine = self._escalation_engine(res.task)
            if engine is not None and engine.name != res.engine:
                retry = await self._vlm_page(res.task, file_hash, engine)
                retry.escalated = True
                if retry.quality.score > res.quality.score:
                    res = retry
        return await self._maybe_judge(res)

    async def _maybe_judge(self, res: PageResult) -> PageResult:
        judge = self.judge
        if judge is None or res.task.route in _NO_MODEL or not res.markdown:
            return res
        if res.quality.verdict == "pass" and random.random() >= self.cfg.quality.judge_sample_rate:
            return res
        try:
            verdict = await judge.judge(await self._image_for(res.task), res.markdown)
        except Exception as e:
            log.warning("judge 失败 %s: %s", res.task.unit, e)
            return res
        if "score" in verdict:
            q = res.quality
            q.metrics["judge_score"] = verdict["score"]
            q.issues += [f"judge：{p}" for p in verdict.get("problems", [])]
            q.score = min(q.score, verdict["score"])
            q.verdict = ("pass" if q.score >= self.cfg.quality.pass_score
                         else "warn" if q.score >= self.cfg.quality.review_score else "fail")
        return res

    # ---- 输出 -------------------------------------------------------------

    def _write_report(self, src: Path, out: Path, results: list[PageResult]) -> None:
        pages = [{
            "unit": r.task.unit, "label": r.task.label, "route": r.task.route.value, "engine": r.engine,
            "score": r.quality.score, "verdict": r.quality.verdict, "issues": r.quality.issues,
            "metrics": r.quality.metrics, "escalated": r.escalated, "from_cache": r.from_cache,
            "latency_s": r.latency_s, "usage": r.usage, "error": r.error,
        } for r in sorted(results, key=lambda r: r.task.order)]
        report = {"source": str(src), "output": str(out),
                  "score": min((p["score"] for p in pages), default=1.0), "pages": pages}
        out.with_suffix(".quality.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")

        review = [p for p in pages if p["verdict"] != "pass"]
        if review:
            with open(self.cfg.out_dir / "review.jsonl", "a", encoding="utf-8") as f:
                for p in review:
                    f.write(json.dumps({"source": str(src), **p}, ensure_ascii=False) + "\n")
