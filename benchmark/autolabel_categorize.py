"""Smoke-test helper: run the parser once on a fixture and save its own output as
"gold" labels. Then `eval.py` re-runs the parser and compares, producing a
self-consistency baseline.

If eval reports <1.0 F1 after this, the delta is pure parser non-determinism.

Usage:
    uv run python benchmark/autolabel.py --fixture 1d
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import yaml
from tqdm.asyncio import tqdm as atqdm

from label import load_fixture, run_parser

BENCH_DIR = Path(__file__).parent
FIXTURES_DIR = BENCH_DIR / "fixtures"
LABELS_DIR = BENCH_DIR / "labels"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--concurrency", type=int, default=5)  # Anthropic Max guidance: 3–5 parallel
    args = ap.parse_args()

    fixture_path = FIXTURES_DIR / f"{args.fixture}.jsonl"
    labels_path = LABELS_DIR / f"{args.fixture}.yaml"
    chunks = load_fixture(fixture_path)
    print(f"Auto-labeling {len(chunks)} chunks from {fixture_path.name}")

    sem = asyncio.Semaphore(args.concurrency)

    async def one(chunk):
        async with sem:
            try:
                items = await run_parser(chunk)
                return chunk["sample_id"], items
            except Exception as e:
                print(f"  {chunk['sample_id']}: ERROR {e}")
                return chunk["sample_id"], []

    results = await atqdm.gather(
        *[one(c) for c in chunks],
        desc=f"autolabel {args.fixture}",
        unit="chunk")

    data = {"samples": {}}
    for sid, items in results:
        data["samples"][sid] = {
            "expected": items,
            "candidate_count": len(items),
            "source": "autolabel",
        }

    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    with labels_path.open("w") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, width=100)
    print(f"\nWrote {labels_path}")


if __name__ == "__main__":
    asyncio.run(main())
