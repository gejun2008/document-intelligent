"""路由：决定每一页走哪条路径，并把原生解析能产出的内容当场产出。

这里的函数都是纯 CPU 计算，由 pipeline 放进进程池执行，所以参数和返回值都要能 pickle。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from docintel.config import RoutingConfig
from docintel.models import PageTask, Route
from docintel.parsers.docx import PLACEHOLDER, NeedsRender, parse_docx
from docintel.parsers.pdf import PageStats, analyze_page, has_md_table, pages_markdown
from docintel.parsers.pptx import parse_pptx
from docintel.parsers.xlsx import parse_xlsx

# 旧格式先用 LibreOffice 升级成 OOXML，再走原生解析
LEGACY_UPGRADE = {".doc": "docx", ".xls": "xlsx", ".ppt": "pptx", ".rtf": "docx", ".odt": "docx",
                  ".ods": "xlsx", ".odp": "pptx"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".bmp"}

__all__ = ["IMAGE_SUFFIXES", "LEGACY_UPGRADE", "NeedsRender", "plan_docx", "plan_image",
           "plan_pdf", "plan_pptx", "plan_xlsx", "route_pdf_page", "text_layer_ok"]


TABLE_ROUTES = {"vlm": Route.VLM_GROUNDED, "azure": Route.AZURE_DI}


def text_layer_ok(stats: PageStats, cfg: RoutingConfig) -> bool:
    """文本层可信：字够多、不是乱码、不是扫描件上叠的旧 OCR 层。"""
    return (stats.chars >= cfg.min_chars_per_page and stats.printable_ratio >= cfg.min_printable_ratio
            and stats.invisible_ratio < cfg.invisible_text_ratio)


def route_pdf_page(stats: PageStats, cfg: RoutingConfig) -> Route:
    if not text_layer_ok(stats, cfg):
        return Route.AZURE_DI
    if stats.math_chars >= cfg.min_math_chars or stats.image_coverage >= cfg.image_coverage_for_vlm:
        return Route.VLM_GROUNDED
    if stats.tables and cfg.table_route in TABLE_ROUTES:
        return TABLE_ROUTES[cfg.table_route]
    return Route.PDF_TEXT


def plan_pdf(pdf_path: Path, doc_id: str, cfg: RoutingConfig) -> list[PageTask]:
    tasks: list[PageTask] = []
    if cfg.table_route not in (*TABLE_ROUTES, "text"):
        raise ValueError(f"routing.table_route 只能是 vlm / azure / text，收到 {cfg.table_route!r}")
    table_route = TABLE_ROUTES.get(cfg.table_route)
    with pymupdf.open(pdf_path) as doc:
        for i, page in enumerate(doc):
            stats = analyze_page(page, detect_tables=table_route is not None)
            route = route_pdf_page(stats, cfg)
            task = PageTask(doc_id=doc_id, unit=f"p{i + 1}", order=i, route=route, pdf_path=pdf_path,
                            page_index=i, label=f"第 {i + 1} 页")
            if text_layer_ok(stats, cfg):
                # VLM 拿它当提示；走 Azure 的表格页拿它当质检参考
                task.native_text = page.get_text("text")
            tasks.append(task)
        text_pages = [t for t in tasks if t.route is Route.PDF_TEXT]
        mds = pages_markdown(doc, [t.page_index for t in text_pages])
        for t in text_pages:
            md = mds[t.page_index]
            if table_route is not None and has_md_table(md):
                # 框线检测漏掉、pymupdf4llm 却抽出了表格（多半是无框线表格）：同样改道
                t.route = table_route
                continue
            t.native_md = md
    return tasks


def plan_docx(path: Path, doc_id: str, cfg: RoutingConfig) -> list[PageTask]:
    md, text, images = parse_docx(path, scanned_max_chars=cfg.scanned_docx_max_chars,
                                  min_image_bytes=cfg.min_image_bytes)
    tasks = [PageTask(doc_id=doc_id, unit="body", order=0, route=Route.NATIVE, native_md=md,
                      native_text=text, label="正文")]
    for img in images:
        tasks.append(PageTask(doc_id=doc_id, unit=f"img{img.index}", order=1 + img.index, route=Route.IMAGE,
                              image_bytes=img.data, placeholder=PLACEHOLDER.format(img.index),
                              label=f"图片 {img.index + 1}"))
    return tasks


def plan_xlsx(path: Path, doc_id: str, cfg: RoutingConfig) -> list[PageTask]:
    chunks, images = parse_xlsx(path, include_hidden=cfg.include_hidden_sheets, fill_merged=cfg.fill_merged_cells,
                                max_rows=cfg.max_rows_per_table, min_image_bytes=cfg.min_image_bytes)
    tasks = [PageTask(doc_id=doc_id, unit=f"{c.sheet}#{c.chunk_index}", order=n, route=Route.NATIVE,
                      native_md=c.markdown, native_text=c.text, label=c.sheet)
             for n, c in enumerate(chunks)]
    for img in images:
        tasks.append(PageTask(doc_id=doc_id, unit=f"{img.sheet}#img{img.index}", order=len(tasks),
                              route=Route.IMAGE, image_bytes=img.data, label=f"{img.sheet} 图片 {img.index + 1}"))
    return tasks


def plan_pptx(path: Path, doc_id: str, rendered_pdf: Path | None, cfg: RoutingConfig) -> list[PageTask]:
    tasks = []
    for s in parse_pptx(path):
        task = PageTask(doc_id=doc_id, unit=f"slide{s.index + 1}", order=s.visible_index, route=Route.NATIVE,
                        native_md=s.markdown, native_text=s.text, label=f"幻灯片 {s.index + 1}")
        if rendered_pdf is not None:
            task.route = Route.VLM_GROUNDED
            task.pdf_path = rendered_pdf
            task.page_index = s.visible_index
        tasks.append(task)
    return tasks


def plan_image(path: Path, doc_id: str) -> list[PageTask]:
    return [PageTask(doc_id=doc_id, unit="img0", order=0, route=Route.IMAGE, image_bytes=path.read_bytes(),
                     label=path.name)]
