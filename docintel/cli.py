"""命令行：

    docintel run  文件或目录... [-c config.toml] [-o out/]   转换并打分
    docintel route 文件...                                   只看路由，不调模型（dry run）
    docintel bench --pred out/ --gold gold/                  离线评测
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter
from pathlib import Path

from docintel.config import load_config
from docintel.pipeline import SUPPORTED_SUFFIXES, Pipeline
from docintel.quality.benchmark import run_benchmark


def _expand(inputs: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in inputs:
        p = Path(raw)
        if p.is_dir():
            files += sorted(f for f in p.rglob("*") if f.suffix.lower() in SUPPORTED_SUFFIXES
                            and not f.name.startswith("~$"))
        else:
            files.append(p)
    return files


async def _run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    if args.out:
        cfg.out_dir = Path(args.out)
    files = _expand(args.inputs)
    async with Pipeline(cfg) as pipe:
        docs = await pipe.run(files)
    failed = 0
    for d in docs:
        if d.error:
            failed += 1
            print(f"✗ {d.source}  {d.error}")
        else:
            flag = "✓" if not d.review_units else "!"
            print(f"{flag} {d.source} → {d.output}  页数 {d.pages}  最低分 {d.score:.2f}  "
                  f"路由 {d.routes}  待复核 {len(d.review_units)}  {d.elapsed_s}s")
    return 1 if failed else 0


async def _route(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    async with Pipeline(cfg) as pipe:
        for f in _expand(args.inputs):
            tasks = await pipe.plan(f)
            print(f"{f}: {dict(Counter(t.route.value for t in tasks))}")
            if args.verbose:
                for t in tasks:
                    print(f"    {t.unit:<16} {t.route.value:<13} {t.label}")
    return 0


def _bench(args: argparse.Namespace) -> int:
    names = {Path(p).stem for p in args.only} if args.only else None
    rows, summary = run_benchmark(Path(args.pred), Path(args.gold), names)
    if args.verbose:
        for r in rows:
            print(f"{r.name:<28} NED {r.text_ned:.3f}  表格F1 {r.table_cell_f1:.3f}  "
                  f"标题F1 {r.heading_f1:.3f}  数字F1 {r.number_f1:.3f}")
    print(f"{'分组':<22}{'n':>4}{'NED↓':>9}{'表格F1':>9}{'标题F1':>9}{'数字F1':>9}")
    for g, m in summary.items():
        print(f"{g:<22}{m['n']:>4}{m['text_ned']:>9.3f}{m['table_cell_f1']:>9.3f}"
              f"{m['heading_f1']:>9.3f}{m['number_f1']:>9.3f}")
    if args.json:
        Path(args.json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")
    return 0


def _synth(args: argparse.Namespace) -> int:
    from docintel.synth import generate

    specs = generate(Path(args.out), per_kind=args.per_kind, seed=args.seed)
    print(f"生成 {len(specs)} 份文档 → {args.out}/docs，金标 → {args.out}/gold，清单 → {args.out}/manifest.jsonl")
    print(dict(Counter(s.kind for s in specs)))
    return 0


def _olmocr(args: argparse.Namespace) -> int:
    from docintel.benchmarks import olmocr

    if args.action == "prepare":
        n = olmocr.prepare(Path(args.data), Path(args.out) / "docs", set(args.suites or []) or None, args.limit)
        print(f"{n} 份 PDF → {args.out}/docs（软链接）。接着：docintel run {args.out}/docs -o <输出目录>")
        return 0
    res = olmocr.score(Path(args.data), Path(args.pred), only_present=args.only_present)
    print(f"{'suite':<20}{'n':>6}{'通过率':>9}{'缺输出':>8}")
    for name, s in res.suites.items():
        print(f"{name:<20}{s['n']:>6}{s['pass_rate']:>9.1%}{s['missing']:>8}")
    print(f"{'按测试类型':<20}")
    for name, s in res.by_type.items():
        note = "  （近似：只做字符串比较，是下界）" if name in olmocr.APPROX else ""
        print(f"  {name:<18}{s['n']:>6}{s['pass_rate']:>9.1%}{note}")
    print(f"overall（各 suite 平均，官方口径）: {res.overall:.1%}")
    if args.failures:
        with open(args.failures, "w", encoding="utf-8") as f:
            for item in res.failures:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"失败用例 {len(res.failures)} 条 → {args.failures}")
    return 0


def _omnidoc(args: argparse.Namespace) -> int:
    from docintel.benchmarks import omnidoc

    n = omnidoc.prepare(Path(args.data), Path(args.out), args.per_source, set(args.lang or []) or None, args.seed)
    print(f"{n} 页 → {args.out}/docs，金标 → {args.out}/gold。接着：\n"
          f"  docintel run {args.out}/docs -o <输出目录> -c config.toml\n"
          f"  docintel bench --pred <输出目录> --gold {args.out}/gold")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docintel")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="转换文档并打分")
    p_run.add_argument("inputs", nargs="+")
    p_run.add_argument("-c", "--config")
    p_run.add_argument("-o", "--out")

    p_route = sub.add_parser("route", help="只看每页的路由")
    p_route.add_argument("inputs", nargs="+")
    p_route.add_argument("-c", "--config")

    p_bench = sub.add_parser("bench", help="对比预测和金标")
    p_bench.add_argument("--pred", required=True)
    p_bench.add_argument("--gold", required=True)
    p_bench.add_argument("--only", nargs="*", help="只评这些文件（名字或路径）")
    p_bench.add_argument("--json", help="汇总结果另存为 JSON")

    p_synth = sub.add_parser("synth", help="生成带金标的合成测试集")
    p_synth.add_argument("-o", "--out", default="testset")
    p_synth.add_argument("-n", "--per-kind", type=int, default=9, help="每种类型生成几份（难度轮换）")
    p_synth.add_argument("--seed", type=int, default=0)

    p_olm = sub.add_parser("olmocr", help="olmOCR-Bench：准备输入 / 打分")
    olm_sub = p_olm.add_subparsers(dest="action", required=True)
    p_olm_prep = olm_sub.add_parser("prepare")
    p_olm_prep.add_argument("--data", default="datasets/olmocr-bench")
    p_olm_prep.add_argument("-o", "--out", default="eval/olmocr")
    p_olm_prep.add_argument("--suites", nargs="*", help="只要这些 suite，如 multi_column old_scans")
    p_olm_prep.add_argument("--limit", type=int)
    p_olm_score = olm_sub.add_parser("score")
    p_olm_score.add_argument("--data", default="datasets/olmocr-bench")
    p_olm_score.add_argument("--pred", required=True)
    p_olm_score.add_argument("--only-present", action="store_true", help="只评有输出的 PDF（部分跑时用）")
    p_olm_score.add_argument("--failures", help="失败用例写到这个 jsonl")

    p_omni = sub.add_parser("omnidoc", help="OmniDocBench：生成输入和金标")
    omni_sub = p_omni.add_subparsers(dest="action", required=True)
    p_omni_prep = omni_sub.add_parser("prepare")
    p_omni_prep.add_argument("--data", default="datasets/omnidocbench")
    p_omni_prep.add_argument("-o", "--out", default="eval/omnidoc")
    p_omni_prep.add_argument("--per-source", type=int, help="每类文档最多抽几页")
    p_omni_prep.add_argument("--lang", nargs="*", help="zh / zh_hant / en / mixed")
    p_omni_prep.add_argument("--seed", type=int, default=0)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd == "bench":
        return _bench(args)
    if args.cmd == "synth":
        return _synth(args)
    if args.cmd == "olmocr":
        return _olmocr(args)
    if args.cmd == "omnidoc":
        return _omnidoc(args)
    return asyncio.run(_run(args) if args.cmd == "run" else _route(args))


if __name__ == "__main__":
    sys.exit(main())
