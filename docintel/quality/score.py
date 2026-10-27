"""把各项检查合成页级质量分。

分数 = 各维度分数的最小值："最差的那一项"决定这页能不能用，issues 里写明是哪一项。
"""

from __future__ import annotations

from docintel.config import QualityConfig
from docintel.models import QualityReport, Recognition
from docintel.quality.checks import garbage_ratio, looks_like_refusal, repetition_ratio, table_consistency
from docintel.quality.reference import compare

MIN_REF_TOKENS = 20  # 参考文本太短时，词袋指标噪声太大，不用


def assess(rec: Recognition, cfg: QualityConfig, ref_text: str | None = None,
           expect_content: bool = True) -> QualityReport:
    md = rec.markdown
    issues: list[str] = []
    metrics: dict[str, float] = {"chars": float(len(md))}
    parts: dict[str, float] = {}

    if rec.refused or looks_like_refusal(md):
        issues.append("模型拒答")
        parts["refusal"] = 0.0
    if expect_content and len(md.strip()) < 5:
        issues.append("输出为空")
        parts["empty"] = 0.0
    if rec.truncated:
        issues.append("输出被截断（max_tokens）")
        parts["truncated"] = 0.4

    rep = repetition_ratio(md)
    metrics["repetition"] = rep
    parts["repetition"] = max(0.0, 1 - 2 * rep)
    if rep > 0.15:
        issues.append(f"重复内容 {rep:.0%}")

    tc, n_tables = table_consistency(md)
    metrics["table_consistency"] = tc
    metrics["tables"] = float(n_tables)
    parts["tables"] = tc
    if tc < 0.95:
        issues.append(f"表格列数不一致（{tc:.0%} 行合规）")

    garbage = garbage_ratio(md)
    metrics["garbage"] = garbage
    parts["garbage"] = max(0.0, 1 - 10 * garbage)
    if garbage > 0.02:
        issues.append(f"乱码字符 {garbage:.1%}")

    if rec.confidence is not None:
        metrics["ocr_confidence"] = rec.confidence
        parts["confidence"] = rec.confidence
        if rec.confidence < 0.85:
            issues.append(f"OCR 置信度低 {rec.confidence:.2f}")

    if ref_text and len(ref_text) >= MIN_REF_TOKENS:
        m = compare(md, ref_text)
        metrics.update(ref_recall=m.recall, ref_precision=m.precision, ref_f1=m.f1)
        parts["content"] = m.f1
        if m.recall < 0.9:
            issues.append(f"疑似漏内容（覆盖率 {m.recall:.0%}）")
        if m.precision < 0.9:
            issues.append(f"疑似幻觉（精确率 {m.precision:.0%}）")
        if m.ref_numbers >= 3:
            metrics.update(num_recall=m.number_recall, num_precision=m.number_precision)
            parts["numbers"] = min(m.number_recall, m.number_precision)
            if parts["numbers"] < 0.95:
                issues.append(f"数字不一致（召回 {m.number_recall:.0%} / 精确 {m.number_precision:.0%}）")

    score = min(parts.values()) if parts else 1.0
    verdict = "pass" if score >= cfg.pass_score else "warn" if score >= cfg.review_score else "fail"
    return QualityReport(score=round(score, 4), verdict=verdict, issues=issues, metrics=metrics)
