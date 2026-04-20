"""Group-by analysis on bench results: metrics sliced by topic / prompt-family.

Reads the cached trace + fixture + labels, joins with config.yaml to get the
system_prompt assigned to each topic, and prints per-group F1 + category
confusion.

Usage:
    uv run python benchmark/group_by.py --fixture 1d
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import yaml
from rapidfuzz import fuzz
from scipy.optimize import linear_sum_assignment

BENCH_DIR = Path(__file__).parent
FIXTURES_DIR = BENCH_DIR / "fixtures"
LABELS_DIR = BENCH_DIR / "labels"
RESULTS_DIR = BENCH_DIR / "results"
CONFIG_PATH = BENCH_DIR.parent / "config.yaml"


def align(pred, gold, threshold=60):
    if not pred or not gold:
        return []
    cost = [[100 - fuzz.token_set_ratio(p["title"], g["title"]) for g in gold] for p in pred]
    rows, cols = linear_sum_assignment(cost)
    return [(int(r), int(c)) for r, c in zip(rows, cols) if 100 - cost[r][c] >= threshold]


def load_topic_prompt_map() -> tuple[dict[int, tuple[str, str]], tuple[str, str] | None]:
    """Map topic_id → (task_name, system_prompt). Also return the General Channel fallback."""
    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    by_topic = {}
    general = None
    for task in cfg.get("tasks", []):
        tid = task.get("topic_id")
        if tid is not None:
            by_topic[tid] = (task["name"], task.get("system_prompt", "default"))
        else:
            # Tasks without topic_id are whole-channel tasks (e.g. GBUYB)
            general = (task["name"], task.get("system_prompt", "default"))
    return by_topic, general


def extract_topic_id(topic_context: str) -> int | None:
    """Parse 'Topic: Topic 3028, Date: ...' → 3028. Returns None for General Channel."""
    import re
    m = re.search(r"Topic: Topic (\d+)", topic_context)
    return int(m.group(1)) if m else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    args = ap.parse_args()

    fixture_path = FIXTURES_DIR / f"{args.fixture}.jsonl"
    labels_path = LABELS_DIR / f"{args.fixture}.yaml"
    cache_path = RESULTS_DIR / f"{args.fixture}_preds_{args.model}_{args.effort}.json"

    if not cache_path.exists():
        raise SystemExit(f"No cache at {cache_path}")

    fixture = {c["sample_id"]: json.loads(line)
               for line in fixture_path.read_text().splitlines() if line.strip()
               for c in [json.loads(line)]}
    labels = yaml.safe_load(labels_path.read_text())
    trace = json.loads(cache_path.read_text())
    topic_prompt, general_fallback = load_topic_prompt_map()

    # first-value back-compat
    first = next(iter(trace.values()), None)
    if isinstance(first, list):
        trace = {sid: {"items": items} for sid, items in trace.items()}

    # Group samples by system_prompt
    by_prompt: dict[str, dict] = defaultdict(lambda: {
        "tp": 0, "fp": 0, "fn": 0, "confusion": Counter(), "samples": [], "topics": set()
    })

    for sid, chunk in fixture.items():
        gold_entry = labels["samples"].get(sid)
        if gold_entry is None:
            continue
        tid = extract_topic_id(chunk["topic_context"])
        if tid is not None:
            task_name, prompt_name = topic_prompt.get(tid, (f"Topic {tid}", "default"))
        elif general_fallback is not None:
            task_name, prompt_name = general_fallback
        else:
            task_name, prompt_name = "General Channel", "default"

        pred = trace[sid]["items"]
        gold = gold_entry["expected"]
        # Strict match: title≥90 AND category correct (same as bench headline)
        pairs = align(pred, gold, threshold=90)
        strict_pairs = [
            (p, g) for p, g in pairs
            if (pred[p].get("category") or None) == (gold[g].get("category") or None)
        ]
        tp = len(strict_pairs)
        fp = len(pred) - tp
        fn = len(gold) - tp

        bucket = by_prompt[prompt_name]
        bucket["tp"] += tp
        bucket["fp"] += fp
        bucket["fn"] += fn
        bucket["topics"].add(task_name)
        bucket["samples"].append(sid)

        # Track category errors (title matched at 90, category disagreed)
        for p_idx, g_idx in pairs:
            pv = pred[p_idx].get("category") or None
            gv = gold[g_idx].get("category") or None
            if pv != gv:
                bucket["confusion"][(gv, pv)] += 1

    print("\nStrict-match F1 per channel type (title≥90 AND category correct):\n")
    print(f"{'channel type':<20s}  {'F1':>6s}  {'tp':>4s}  {'fp':>4s}  {'fn':>4s}  {'n':>3s}  topics")
    print("-" * 100)
    rows = []
    for prompt_name, b in by_prompt.items():
        p = b["tp"] / (b["tp"] + b["fp"]) if (b["tp"] + b["fp"]) else 0.0
        r = b["tp"] / (b["tp"] + b["fn"]) if (b["tp"] + b["fn"]) else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        rows.append((f1, prompt_name, p, r, b))
    rows.sort(reverse=True)
    for f1, prompt_name, p, r, b in rows:
        n = len(b["samples"])
        n_warn = " *" if n < 3 else "  "
        topics_str = ", ".join(sorted(b["topics"]))[:55]
        print(
            f"{prompt_name:<20s}  {f1:>6.3f}  "
            f"{b['tp']:>4d}  {b['fp']:>4d}  {b['fn']:>4d}  {n:>3d}{n_warn}{topics_str}"
        )

    print("\n* n<3 — too small for reliable signal")
    print("\nCategory errors per channel type:")
    for prompt_name, b in by_prompt.items():
        errs = [(k, v) for k, v in b["confusion"].items() if k[0] != k[1]]
        if not errs:
            continue
        print(f"  [{prompt_name}]")
        for (gold, pred), n in sorted(errs, key=lambda x: -x[1]):
            print(f"    × {gold or 'None':>14s} → {pred or 'None':<14s}  {n}")


if __name__ == "__main__":
    main()
