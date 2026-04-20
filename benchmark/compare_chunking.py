"""Compare parser output on chunked vs coalesced fixtures.

Runs the parser on both {N}d.jsonl and {N}d_full.jsonl and prints a structural
comparison: item counts, category distribution, items per chunk. No labels
needed — this measures whether chunking changes what the parser extracts.

Usage:
    uv run python benchmark/compare_chunking.py --days 7
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

from tqdm.asyncio import tqdm as atqdm

sys.path.insert(0, str(Path(__file__).parent))
from label import load_fixture, run_parser  # noqa: E402

BENCH_DIR = Path(__file__).parent
FIXTURES_DIR = BENCH_DIR / "fixtures"
RESULTS_DIR = BENCH_DIR / "results"


async def parse_all(fixture: list[dict], concurrency: int = 5) -> dict[str, list[dict]]:
    sem = asyncio.Semaphore(concurrency)

    async def one(chunk):
        async with sem:
            try:
                items = await run_parser(chunk)
            except Exception as e:
                print(f"  [{chunk['sample_id']}] error: {e}")
                items = []
            return chunk["sample_id"], items

    results = await atqdm.gather(
        *[one(c) for c in fixture],
        desc=f"parse ({len(fixture)} chunks)",
        unit="chunk",
    )
    return dict(results)


def summarize(name: str, fixture: list[dict], preds: dict[str, list[dict]]) -> dict:
    n_chunks = len(fixture)
    n_items = sum(len(p) for p in preds.values())
    cat_counts: Counter = Counter()
    for items in preds.values():
        for it in items:
            cat_counts[it.get("category", "?")] += 1
    total_msgs = sum(len(c["messages"]) for c in fixture)
    return {
        "name": name,
        "chunks": n_chunks,
        "total_messages": total_msgs,
        "items_total": n_items,
        "items_per_chunk": n_items / n_chunks if n_chunks else 0,
        "items_per_1000_msgs": n_items / total_msgs * 1000 if total_msgs else 0,
        "category_distribution": dict(cat_counts),
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, required=True, choices=[1, 7, 30])
    args = ap.parse_args()

    fix_a = load_fixture(FIXTURES_DIR / f"{args.days}d.jsonl")
    fix_b = load_fixture(FIXTURES_DIR / f"{args.days}d_full.jsonl")
    if len(fix_a) == len(fix_b):
        print(f"Note: {args.days}d and {args.days}d_full have same chunk count "
              f"({len(fix_a)}) — no merging possible. Comparison will be identical.")

    print(f"\nRunning parser on {args.days}d (chunked, {len(fix_a)} chunks)...")
    preds_a = await parse_all(fix_a)

    print(f"\nRunning parser on {args.days}d_full (full-topic, {len(fix_b)} chunks)...")
    preds_b = await parse_all(fix_b)

    sum_a = summarize(f"{args.days}d (chunked)", fix_a, preds_a)
    sum_b = summarize(f"{args.days}d_full (full-topic)", fix_b, preds_b)

    print("\n" + "=" * 80)
    print(f"{'metric':<30s}  {'chunked':>15s}  {'full-topic':>15s}  {'Δ':>10s}")
    print("-" * 80)
    def row(label, a, b, fmt="{:.1f}"):
        diff = b - a
        sign = "+" if diff >= 0 else ""
        print(f"{label:<30s}  {fmt.format(a):>15s}  {fmt.format(b):>15s}  {sign}{fmt.format(diff):>9s}")

    row("chunks", sum_a["chunks"], sum_b["chunks"], "{:d}")
    row("total messages", sum_a["total_messages"], sum_b["total_messages"], "{:d}")
    row("items extracted", sum_a["items_total"], sum_b["items_total"], "{:d}")
    row("items / chunk", sum_a["items_per_chunk"], sum_b["items_per_chunk"], "{:.2f}")
    row("items / 1000 msgs", sum_a["items_per_1000_msgs"], sum_b["items_per_1000_msgs"], "{:.2f}")

    print("\nCategory distribution:")
    all_cats = sorted(set(sum_a["category_distribution"]) | set(sum_b["category_distribution"]))
    for cat in all_cats:
        a = sum_a["category_distribution"].get(cat, 0)
        b = sum_b["category_distribution"].get(cat, 0)
        diff = b - a
        sign = "+" if diff >= 0 else ""
        print(f"  {cat:<16s}  {a:>4d}  {b:>4d}  ({sign}{diff})")

    out = RESULTS_DIR / f"compare_chunking_{args.days}d.json"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"chunked": sum_a, "full_topic": sum_b},
                              ensure_ascii=False, indent=2))
    print(f"\nFull comparison: {out}")


if __name__ == "__main__":
    asyncio.run(main())
