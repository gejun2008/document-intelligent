import json
from pathlib import Path

from docintel.benchmarks import olmocr, omnidoc


def _t(**kw):
    return {"pdf": "s/x.pdf", "page": 1, "id": "t", "max_diffs": 0, **kw}


def test_olmocr_presence_absence_order():
    md = "Intro paragraph.\n\nThe **quick** brown fox — jumps.\n\nPage 3 footer"
    assert olmocr.run_test(_t(type="present", text="The quick brown fox - jumps"), md)
    assert olmocr.run_test(_t(type="present", text="the QUICK brown", case_sensitive=False), md)
    assert not olmocr.run_test(_t(type="present", text="lazy dog"), md)
    assert not olmocr.run_test(_t(type="absent", text="Page 3 footer"), md)
    assert olmocr.run_test(_t(type="absent", text="Page 3 footer", first_n=20), md)  # 只看开头 20 个字符
    assert olmocr.run_test(_t(type="order", before="Intro paragraph", after="brown fox"), md)
    assert not olmocr.run_test(_t(type="order", before="brown fox", after="Intro paragraph"), md)


def test_olmocr_page_markers_do_not_break_baseline():
    md = "<!-- page 第 1 页 -->\nSome English text here."
    assert olmocr.run_test(_t(type="baseline"), md)
    assert not olmocr.run_test(_t(type="baseline"), "中文内容")
    assert not olmocr.run_test(_t(type="baseline"), "loop " * 40)


def test_olmocr_tables_md_and_html():
    md = "| Name | Q1 | Q2 |\n|---|---|---|\n| Alpha | 10 | 12 |\n| Beta | 7 | 9 |"
    assert olmocr.run_test(_t(type="table", cell="12", left="10", top_heading="Q2", left_heading="Alpha"), md)
    assert not olmocr.run_test(_t(type="table", cell="12", top_heading="Q1"), md)
    html = "<table><tr><th>Name</th><th colspan=2>Score</th></tr><tr><td>Alpha</td><td>1</td><td>2</td></tr></table>"
    assert olmocr.run_test(_t(type="table", cell="2", top_heading="Score", left="1"), html)


def test_olmocr_math_and_scoring(tmp_path: Path):
    md = "Euler: $$ e^{i\\pi} + 1 = 0 $$ done"
    assert olmocr.run_test(_t(type="math", math="e^{i\\pi}+1=0"), md)
    assert not olmocr.run_test(_t(type="math", math="a^2+b^2=c^2"), md)

    bench = tmp_path / "bench_data"
    (bench / "pdfs" / "s").mkdir(parents=True)
    tests = [_t(type="present", text="hello world", id="a"), _t(type="present", text="missing", id="b")]
    (bench / "s.jsonl").write_text("\n".join(json.dumps(t) for t in tests), "utf-8")
    pred = tmp_path / "pred"
    pred.mkdir()
    (pred / "s__x.md").write_text("hello world", "utf-8")
    res = olmocr.score(tmp_path, pred)
    assert res.suites["s"]["pass_rate"] == 0.5 and res.overall == 0.5 and len(res.failures) == 1


def test_omnidoc_page_to_md():
    page = {
        "layout_dets": [
            {"category_type": "header", "ignore": False, "order": 0, "anno_id": 0, "text": "Journal X"},
            {"category_type": "title", "ignore": False, "order": 1, "anno_id": 1, "text": "引言"},
            {"category_type": "text_block", "ignore": False, "order": 2, "anno_id": 2, "text": "第一段的前半"},
            {"category_type": "text_block", "ignore": False, "order": 4, "anno_id": 3, "text": "后半。"},
            {"category_type": "table", "ignore": False, "order": 3, "anno_id": 4,
             "html": "<table><tr><td>a</td><td>b</td></tr><tr><td>1</td><td>2</td></tr></table>"},
            {"category_type": "equation_isolated", "ignore": False, "order": 5, "anno_id": 5, "latex": "$$x=1$$"},
            {"category_type": "text_block", "ignore": True, "order": 6, "anno_id": 6, "text": "ignored"},
            {"category_type": "page_number", "ignore": False, "order": None, "anno_id": 7, "text": "12"},
        ],
        "page_info": {"image_path": "p.png", "page_attribute": {"data_source": "book", "language": "simplified_chinese"}},
        "extra": {"relation": [{"source_anno_id": 2, "target_anno_id": 3, "relation_type": "truncated"}]},
    }
    md = omnidoc.page_to_md(page)
    assert md.startswith("## 引言\n\n第一段的前半后半。")
    assert "| a | b |" in md and "$$x=1$$" in md
    assert "Journal X" not in md and "ignored" not in md and "12" not in md
