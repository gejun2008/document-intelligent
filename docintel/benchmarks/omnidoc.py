"""OmniDocBench（opendatalab/OmniDocBench，仅限研究用途）：把版面标注转成金标 md，走 docintel bench。

转换规则（尽量贴近官方端到端评测的口径）：
- 按 order 排序，跳过 ignore 的框；页眉、页脚、页码、页脚注、abandon、图片和各类 mask 不进金标
- title → "## 标题"（数据集不标层级，所以标题 F1 只看文本，层级统一按二级）
- table 的 HTML 转成 md 表格（合并单元格会展开，结构细节以官方 TEDS 为准）
- equation_isolated 保留原 LaTeX（$$...$$）
- extra.relation 里 truncated 关系的文本块（跨栏/跨块截断）拼回一段

这是近似口径：要和论文、榜单数字对齐，请用官方评测脚本（github.com/opendatalab/OmniDocBench）。
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

from markdownify import markdownify

SKIP = {"header", "footer", "page_number", "page_footnote", "abandon", "figure", "text_mask", "chart_mask",
        "table_mask", "unknown_mask", "need_mask", "organic_chemical_formula_mask", "algorithm_mask",
        "equation_semantic", "equation_explanation",
        "list_group"}  # list_group 是空容器，成员已经通过 merge_list 并进 text_block
LANG = {"simplified_chinese": "zh", "traditional_chinese": "zh_hant", "english": "en", "en_ch_mixed": "mixed"}


def _truthy(v: object) -> bool:
    return v is True or str(v).lower() == "true"


def _order(v: object) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("inf")


def _table_md(html: str) -> str:
    md = markdownify(html, table_infer_header=True).strip()
    return "\n".join(line for line in md.splitlines() if line.strip())


def page_to_md(page: dict) -> str:
    dets = [d for d in page["layout_dets"] if not _truthy(d.get("ignore")) and d["category_type"] not in SKIP]
    by_id = {d.get("anno_id"): d for d in dets}

    # truncated：后一块接到前一块上
    absorbed: set[str] = set()
    tails: dict[str, list[str]] = defaultdict(list)
    for rel in (page.get("extra") or {}).get("relation", []) or []:
        if rel.get("relation_type") != "truncated":
            continue
        src, dst = rel.get("source_anno_id"), rel.get("target_anno_id")
        if src in by_id and dst in by_id and dst not in absorbed:
            head, tail = sorted((by_id[src], by_id[dst]), key=lambda d: _order(d.get("order")))
            tails[head["anno_id"]].append(tail["anno_id"])
            absorbed.add(tail["anno_id"])

    def clean(text: str | None) -> str:
        # 合并后的列表块里，缩进是字面的 "\t"
        return (text or "").replace("\\t", "  ").strip()

    def text_of(d: dict) -> str:
        parts = [clean(d.get("text"))]
        for tid in tails.get(d.get("anno_id"), []):
            parts.append(clean(by_id[tid].get("text")))
        sep = "" if any("一" <= ch <= "鿿" for ch in parts[0][-1:]) else " "
        return sep.join(p for p in parts if p)

    blocks = []
    for d in sorted(dets, key=lambda d: _order(d.get("order"))):
        if d.get("anno_id") in absorbed:
            continue
        cat = d["category_type"]
        if cat == "table":
            if d.get("html"):
                blocks.append(_table_md(d["html"]))
        elif cat == "equation_isolated":
            latex = (d.get("latex") or "").strip()
            if latex:
                blocks.append(latex if latex.startswith("$$") else f"$$\n{latex}\n$$")
        elif cat == "title":
            t = text_of(d)
            if t:
                blocks.append(f"## {t}")
        elif cat == "code_txt":
            blocks.append(f"```\n{text_of(d)}\n```")
        else:
            t = text_of(d)
            if t:
                blocks.append(t)
    return "\n\n".join(blocks) + "\n"


def prepare(data_dir: Path, out: Path, per_source: int | None = None, langs: set[str] | None = None,
            seed: int = 0) -> int:
    """生成 out/docs（页面图软链接）、out/gold（金标 md）、out/manifest.jsonl。

    per_source：每个 data_source 最多抽几页（分层抽样，控制模型调用成本）。
    langs：只保留这些语言（zh / zh_hant / en / mixed）。
    """
    pages = json.loads((data_dir / "OmniDocBench.json").read_text("utf-8"))
    docs, gold = out / "docs", out / "gold"
    docs.mkdir(parents=True, exist_ok=True)
    gold.mkdir(parents=True, exist_ok=True)

    by_source: dict[str, list[dict]] = defaultdict(list)
    for p in pages:
        attr = p["page_info"].get("page_attribute", {})
        lang = LANG.get(attr.get("language"), "other")
        if langs and lang not in langs:
            continue
        by_source[attr.get("data_source", "unknown")].append(p)

    rng = random.Random(seed)
    chosen = []
    for source in sorted(by_source):
        group = sorted(by_source[source], key=lambda p: p["page_info"]["image_path"])
        if per_source and len(group) > per_source:
            group = rng.sample(group, per_source)
        chosen += group

    with open(out / "manifest.jsonl", "w", encoding="utf-8") as mf:
        for p in chosen:
            info = p["page_info"]
            attr = info.get("page_attribute", {})
            image = (data_dir / "images" / info["image_path"]).resolve()
            name = "omnidoc__" + Path(info["image_path"]).stem
            link = docs / f"{name}{image.suffix}"
            if not link.exists():
                link.symlink_to(image)
            (gold / f"{name}.md").write_text(page_to_md(p), "utf-8")
            lang = LANG.get(attr.get("language"), "other")
            mf.write(json.dumps({"name": name, "kind": f"omnidoc_{lang}", "lang": lang,
                                 "difficulty": attr.get("data_source", "unknown"),
                                 "features": {"layout": attr.get("layout"), "subset": attr.get("subset"),
                                              "special_issue": attr.get("special_issue")}},
                                ensure_ascii=False) + "\n")
    return len(chosen)
