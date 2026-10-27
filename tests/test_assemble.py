from docintel.assemble import assemble, strip_headers_footers
from docintel.models import PageResult, PageTask, QualityReport, Route

Q = QualityReport(score=1.0, verdict="pass")


def _res(unit, order, md, route=Route.PDF_TEXT, placeholder=None):
    task = PageTask(doc_id="d", unit=unit, order=order, route=route, placeholder=placeholder, label=unit)
    return PageResult(task=task, markdown=md, engine="x", quality=Q)


def test_strip_headers_footers():
    pages = [f"ACME 公司 内部资料\n\n正文 {i} 的内容\n\n第 {i} 页 共 4 页" for i in range(1, 5)]
    cleaned = strip_headers_footers(pages)
    assert all("ACME" not in p and "共 4 页" not in p for p in cleaned)
    assert cleaned[2] == "正文 3 的内容"


def test_cross_page_table_merge_and_image_fill():
    results = [
        _res("p1", 0, "前文\n\n| 名称 | 数量 |\n|---|---|\n| a | 1 |"),
        _res("p2", 1, "| 名称 | 数量 |\n|---|---|\n| b | 2 |\n\n后文 ![图](docintel-img:0)"),
        _res("img0", 2, "> [图：柱状图]", route=Route.IMAGE, placeholder="docintel-img:0"),
    ]
    md = assemble(results, keep_page_markers=True)
    assert md.count("| 名称 | 数量 |") == 1          # 续表的重复表头去掉了
    assert "| a | 1 |\n| b | 2 |" in md
    assert "> [图：柱状图]" in md and "docintel-img" not in md
    assert "<!-- page p1 -->" in md and "<!-- page p2 -->" not in md  # p2 并进了 p1 的表
