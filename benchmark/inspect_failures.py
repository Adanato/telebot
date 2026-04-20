"""Inspect a bench run's failure modes.

Reads the cached trace (predictions + thinking + system prompt) and prints
the parser's reasoning for any sample where TP/FP/FN or category mismatch
indicates a failure.

Usage:
    uv run python benchmark/inspect_failures.py --fixture 1d
    uv run python benchmark/inspect_failures.py --fixture 1d --sample s0001
    uv run python benchmark/inspect_failures.py --fixture 1d --prompt   # dump system prompt
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml
from rapidfuzz import fuzz

BENCH_DIR = Path(__file__).parent
FIXTURES_DIR = BENCH_DIR / "fixtures"
LABELS_DIR = BENCH_DIR / "labels"
RESULTS_DIR = BENCH_DIR / "results"


def load_fixture(path: Path) -> list[dict]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def load_labels(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f) or {"samples": {}}


def load_trace(path: Path) -> dict:
    data = json.loads(path.read_text())
    # Back-compat: old caches stored lists
    first = next(iter(data.values()), None)
    if isinstance(first, list):
        data = {sid: {"items": items, "thinking": "", "system_prompt": ""}
                for sid, items in data.items()}
    return data


def align(pred, gold, threshold=60):
    from scipy.optimize import linear_sum_assignment
    if not pred or not gold:
        return []
    cost = [[100 - fuzz.token_set_ratio(p["title"], g["title"]) for g in gold] for p in pred]
    rows, cols = linear_sum_assignment(cost)
    return [(int(r), int(c)) for r, c in zip(rows, cols) if 100 - cost[r][c] >= threshold]


def print_sample(
    chunk: dict, pred: list, gold: list, thinking: str, show_msgs: bool = False
) -> None:
    sid = chunk["sample_id"]
    topic = chunk["topic_context"]
    print("\n" + "=" * 90)
    print(f"SAMPLE {sid}  ·  {topic}")
    print("-" * 90)

    pairs = align(pred, gold)
    matched_p = {p for p, _ in pairs}
    matched_g = {g for _, g in pairs}
    pair_map = dict(pairs)

    print("\nPRED vs GOLD:")
    for i, p in enumerate(pred):
        if i in matched_p:
            g = gold[pair_map[i]]
            if p.get("category") == g.get("category"):
                marker = "  "
            else:
                marker = "× "
            print(f"  {marker}[{p.get('category', '?'):<12}] → [{g.get('category', '?'):<12}]  {p.get('title', '')[:60]}")
        else:
            print(f"  FP [{p.get('category', '?'):<12}]              {p.get('title', '')[:60]}")
    for j, g in enumerate(gold):
        if j not in matched_g:
            print(f"  FN [{'MISSED':<12}] → [{g.get('category', '?'):<12}]  {g.get('title', '')[:60]}")

    if show_msgs:
        print("\nMESSAGES:")
        for m in chunk["messages"][:12]:
            c = (m.get("content") or "").replace("\n", " ")
            print(f"  #{m['id']}: {c[:120]}")
        if len(chunk["messages"]) > 12:
            print(f"  … {len(chunk['messages']) - 12} more")

    if thinking:
        print("\nTHINKING:")
        for line in thinking.splitlines():
            print(f"  {line}")
    else:
        print("\nTHINKING: (not captured — run bench with thinking=enabled)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    ap.add_argument("--sample", default=None, help="Inspect one sample ID (e.g. s0003)")
    ap.add_argument("--all", action="store_true", help="Show all samples, not just failures")
    ap.add_argument("--msgs", action="store_true", help="Also print the raw messages")
    ap.add_argument("--prompt", action="store_true",
                    help="Dump the parser's system prompt (sanity-check for prompt bugs)")
    args = ap.parse_args()

    cache_path = RESULTS_DIR / f"{args.fixture}_preds_{args.model}_{args.effort}.json"
    if not cache_path.exists():
        raise SystemExit(f"No cache at {cache_path}. Run bench_categorize.py first.")
    trace = load_trace(cache_path)

    # Dump prompt
    if args.prompt:
        first_trace = next((t for t in trace.values() if t.get("system_prompt")), None)
        if first_trace and first_trace["system_prompt"]:
            print("=== SYSTEM PROMPT ===")
            print(first_trace["system_prompt"])
            print("=" * 40)
        else:
            print("No system prompt captured in trace.")
        return

    fixture = {c["sample_id"]: c for c in load_fixture(FIXTURES_DIR / f"{args.fixture}.jsonl")}
    labels = load_labels(LABELS_DIR / f"{args.fixture}.yaml")

    sids = [args.sample] if args.sample else list(trace.keys())

    failure_count = 0
    for sid in sids:
        chunk = fixture.get(sid)
        gold_entry = labels["samples"].get(sid)
        if chunk is None or gold_entry is None:
            continue
        t = trace[sid]
        pred = t["items"]
        gold = gold_entry["expected"]

        pairs = align(pred, gold)
        has_fp = len(pred) > len(pairs)
        has_fn = len(gold) > len(pairs)
        has_cat_err = any(
            pred[p].get("category") != gold[g].get("category") for p, g in pairs
        )

        if not args.all and not (has_fp or has_fn or has_cat_err):
            continue

        failure_count += 1
        print_sample(chunk, pred, gold, t.get("thinking", ""), show_msgs=args.msgs)

    if failure_count == 0:
        print("No failures to inspect. Use --all to see all samples.")


if __name__ == "__main__":
    main()
