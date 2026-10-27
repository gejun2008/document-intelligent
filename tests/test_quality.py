from docintel.config import QualityConfig
from docintel.models import Recognition
from docintel.quality.benchmark import score_pair
from docintel.quality.checks import looks_like_refusal, repetition_ratio, table_consistency
from docintel.quality.reference import compare
from docintel.quality.score import assess

REF = "2024 年营业收入 3,400 万元，同比增长 12.5%，毛利率 41%，研发投入 980 万元。北区贡献最大。"


def test_repetition_detects_loop():
    assert repetition_ratio("正常的一段话。" * 1 + "重复重复重复重复的片段" * 8) == 1.0
    assert repetition_ratio("第一行内容足够长\n第二行内容足够长\n第三行内容足够长") == 0.0


def test_table_consistency():
    good = "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
    bad = "| a | b |\n|---|---|\n| 1 | 2 | 3 |\n| 4 |"
    assert table_consistency(good) == (1.0, 1)
    assert table_consistency(bad)[0] < 0.6


def test_reference_catches_missing_and_wrong_numbers():
    full = compare(REF, REF)
    assert full.recall == 1.0 and full.precision == 1.0
    partial = compare("2024 年营业收入 3,400 万元。", REF)
    assert partial.recall < 0.5 and partial.precision == 1.0
    wrong = compare(REF.replace("12.5", "15.2").replace("980", "890"), REF)
    assert wrong.number_recall < 1.0


def test_assess_verdicts():
    cfg = QualityConfig()
    assert assess(Recognition(REF, "x"), cfg, ref_text=REF).verdict == "pass"

    truncated = assess(Recognition(REF, "x", truncated=True), cfg, ref_text=REF)
    assert truncated.verdict == "fail" and any("截断" in i for i in truncated.issues)

    missing = assess(Recognition(REF[:20], "x"), cfg, ref_text=REF)
    assert missing.verdict != "pass" and any("漏内容" in i for i in missing.issues)

    refusal = assess(Recognition("I'm sorry, I can't transcribe this image.", "x"), cfg)
    assert refusal.score == 0.0 and looks_like_refusal(refusal_text := "抱歉，我无法识别") and refusal_text


def test_benchmark_scores():
    gold = "# 标题\n\n正文一段。\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    same = score_pair(gold, gold)
    assert same.text_ned == 0 and same.table_cell_f1 == 1 and same.heading_f1 == 1
    worse = score_pair("## 标题\n\n正文。", gold)
    assert worse.text_ned > 0 and worse.heading_f1 == 0 and worse.table_cell_f1 == 0
