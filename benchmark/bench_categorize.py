"""Run the current parser on a fixture and score against hand-labeled ground truth.

Alignment: we match each predicted item to at most one gold item using Hungarian
assignment on a title-similarity matrix (RapidFuzz token_set_ratio). Matched
pairs are scored per-field; unmatched predictions count as false positives;
unmatched gold items count as false negatives.

Outputs:
    - Per-field accuracy (category / priority / status) over matched pairs
    - Macro and micro F1 for the set-alignment task
    - Per-category confusion matrix (category field only, over matched pairs)
    - A JSON report saved to benchmark/results/{fixture}_{timestamp}.json

Usage:
    uv run python benchmark/eval.py --fixture 1d
    uv run python benchmark/eval.py --fixture 7d --model claude-sonnet-4-6
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml
from rapidfuzz import fuzz
from scipy.optimize import linear_sum_assignment
from tqdm.asyncio import tqdm as atqdm

from course_scout.infrastructure.agents import (
    AgentOrchestrator,
    StructuredMessage,
    SummarizerInputSchema)

BENCH_DIR = Path(__file__).parent
FIXTURES_DIR = BENCH_DIR / "fixtures"
LABELS_DIR = BENCH_DIR / "labels"
RESULTS_DIR = BENCH_DIR / "results"

MATCH_THRESHOLD = 60  # rapidfuzz token_set_ratio; below this = no match


# ── parser invocation ────────────────────────────────────────────────────────


async def run_parser(chunk: dict, model: str, effort: str) -> dict:
    """Run parser; return items + thinking + system_prompt for analysis."""
    orch = AgentOrchestrator(summarizer_model=model, effort=effort, thinking="adaptive")
    agent = orch.get_summarizer_agent()
    msgs = [StructuredMessage(**m) for m in chunk["messages"]]
    inp = SummarizerInputSchema(
        messages=msgs,
        topic_context=chunk["topic_context"],
        chat_message=chunk.get("chat_message", ""))
    result = await agent.run(inp)
    provider = list(orch._providers.values())[0] if orch._providers else None
    thinking_text = getattr(provider, "last_thinking", "") if provider else ""
    items = [
        {
            "title": getattr(it, "title", ""),
            "category": getattr(it, "category", ""),
            "priority": getattr(it, "priority", None),
            "status": getattr(it, "status", None),
        }
        for it in result.items
    ]
    return {
        "items": items,
        "thinking": thinking_text,
        "system_prompt": agent.system_prompt,
    }


# ── alignment + scoring ──────────────────────────────────────────────────────


def align(
    pred: list[dict], gold: list[dict], threshold: int = MATCH_THRESHOLD
) -> list[tuple[int, int]]:
    """Hungarian-match predicted items to gold items by title similarity.

    Returns list of (pred_idx, gold_idx) pairs where similarity ≥ threshold.
    """
    if not pred or not gold:
        return []
    cost = [[100 - fuzz.token_set_ratio(p["title"], g["title"]) for g in gold] for p in pred]
    rows, cols = linear_sum_assignment(cost)
    pairs = []
    for r, c in zip(rows, cols):
        sim = 100 - cost[r][c]
        if sim >= threshold:
            pairs.append((int(r), int(c)))
    return pairs


def _set_scores_at(
    fixture: list[dict], labels: dict, preds_by_sid: dict[str, list[dict]], threshold: int
) -> dict:
    """Compute (tp, fp, fn) using Hungarian match at a given threshold."""
    tp = fp = fn = 0
    for chunk in fixture:
        sid = chunk["sample_id"]
        gold_entry = labels["samples"].get(sid)
        if gold_entry is None:
            continue
        gold = gold_entry["expected"]
        pred = preds_by_sid.get(sid, [])
        pairs = align(pred, gold, threshold=threshold)
        tp += len(pairs)
        fp += len(pred) - len(pairs)
        fn += len(gold) - len(pairs)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": p, "recall": r, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def score_fixture(
    fixture: list[dict], labels: dict, preds_by_sid: dict[str, list[dict]]
) -> dict:
    """Compute scores at multiple fidelity levels:

    - set_scores: loose match (threshold 60) — kept for historical comparison
    - set_scores_strict: strict title match (threshold 90) — catches near-duplicates
    - strict_match_f1: title ≥90 AND category correct — the "really right" metric
    - category_accuracy_on_matched: old metric (biased — hides recall drops)
    - category_accuracy_over_gold: new metric — correct / total_gold
                                   (drops when recall drops; harder to game)
    """
    # Set-level TP/FP/FN at multiple thresholds (Hungarian curve)
    set_60 = _set_scores_at(fixture, labels, preds_by_sid, 60)
    set_75 = _set_scores_at(fixture, labels, preds_by_sid, 75)
    set_90 = _set_scores_at(fixture, labels, preds_by_sid, 90)

    # Now score per-field accuracy at the default threshold (60)
    field_hits_matched: dict[str, int] = defaultdict(int)
    field_total_matched: dict[str, int] = defaultdict(int)
    field_hits_overgold: dict[str, int] = defaultdict(int)
    field_total_overgold: dict[str, int] = defaultdict(int)
    confusion: Counter = Counter()
    strict_tp = strict_fp = strict_fn = 0
    sample_rows: list[dict] = []

    for chunk in fixture:
        sid = chunk["sample_id"]
        gold_entry = labels["samples"].get(sid)
        if gold_entry is None:
            continue
        gold = gold_entry["expected"]
        pred = preds_by_sid.get(sid, [])

        pairs_60 = align(pred, gold, threshold=60)
        pairs_90 = align(pred, gold, threshold=90)

        # All-gold field accuracy: every gold item counts, unmatched = miss
        matched_gold_60 = {g for _, g in pairs_60}
        for g_idx, g in enumerate(gold):
            gv = g.get("category") or None
            field_total_overgold["category"] += 1
            if g_idx in matched_gold_60:
                # find the pred it matched to
                p_idx = next(p for p, gg in pairs_60 if gg == g_idx)
                pv = pred[p_idx].get("category") or None
                if pv == gv:
                    field_hits_overgold["category"] += 1

        # On-matched accuracy (legacy — kept for comparison)
        for p_idx, g_idx in pairs_60:
            p = pred[p_idx]
            g = gold[g_idx]
            pv = p.get("category") or None
            gv = g.get("category") or None
            field_total_matched["category"] += 1
            if pv == gv:
                field_hits_matched["category"] += 1
            confusion[(gv, pv)] += 1

        # Strict-match F1: title ≥ 90 AND category matches
        strict_pairs = [
            (p, g) for p, g in pairs_90
            if (pred[p].get("category") or None) == (gold[g].get("category") or None)
        ]
        strict_tp += len(strict_pairs)
        strict_fp += len(pred) - len(strict_pairs)
        strict_fn += len(gold) - len(strict_pairs)

        sample_rows.append({
            "sample_id": sid,
            "topic": chunk["topic_context"],
            "n_pred": len(pred),
            "n_gold": len(gold),
            "matched_60": len(pairs_60),
            "matched_90": len(pairs_90),
        })

    strict_p = strict_tp / (strict_tp + strict_fp) if (strict_tp + strict_fp) else 0.0
    strict_r = strict_tp / (strict_tp + strict_fn) if (strict_tp + strict_fn) else 0.0
    strict_f1 = 2 * strict_p * strict_r / (strict_p + strict_r) if (strict_p + strict_r) else 0.0

    return {
        "set_scores": set_60,  # keeps existing downstream consumers working
        "set_scores_at_60": set_60,
        "set_scores_at_75": set_75,
        "set_scores_at_90": set_90,
        "strict_match": {
            "precision": strict_p,
            "recall": strict_r,
            "f1": strict_f1,
            "tp": strict_tp, "fp": strict_fp, "fn": strict_fn,
        },
        "field_accuracy_on_matched": {
            "category": (field_hits_matched["category"] / field_total_matched["category"])
            if field_total_matched.get("category") else None,
        },
        "field_accuracy_over_gold": {
            "category": (field_hits_overgold["category"] / field_total_overgold["category"])
            if field_total_overgold.get("category") else None,
        },
        "category_confusion": {
            f"{gold}→{pred}": n for (gold, pred), n in confusion.most_common()
        },
        "per_sample": sample_rows,
    }


# ── reporting ────────────────────────────────────────────────────────────────


def render_summary(report: dict) -> str:
    s = report["strict_match"]
    n_total = s["tp"] + s["fn"]
    lines = [
        "━" * 60,
        "HEADLINE — Strict match (title + category both correct)",
        "━" * 60,
        f"  F1: {s['f1']:.3f}    ({s['tp']}/{n_total} items fully correct)",
        f"  Precision: {s['precision']:.3f}   Recall: {s['recall']:.3f}",
        "",
        "Where the errors are (gold → predicted):",
    ]
    errors = [(k, v) for k, v in report["category_confusion"].items()
              if k.split("→")[0] != k.split("→")[1]]
    if errors:
        for k, v in errors:
            lines.append(f"  × {k:<32s} {v}")
    else:
        lines.append("  (none — all matched items have correct category)")

    # Per-prompt slice (from the report's per_sample rows, if present)
    # Top-level: if the bench was run by group_by, slices are available.
    # Otherwise this section is a reminder to run: uv run python benchmark/group_by.py --fixture <N>d
    lines.append("")
    lines.append("For per-channel breakdown:")
    lines.append("  uv run python benchmark/group_by.py --fixture 1d")
    return "\n".join(lines)


# ── main ─────────────────────────────────────────────────────────────────────


def load_fixture(path: Path) -> list[dict]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def load_labels(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f) or {"samples": {}}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    ap.add_argument("--concurrency", type=int, default=5)  # Anthropic Max guidance: 3–5 parallel
    ap.add_argument("--dry-run", action="store_true",
                    help="Score cached predictions only; do not call the model")
    args = ap.parse_args()

    fixture_path = FIXTURES_DIR / f"{args.fixture}.jsonl"
    labels_path = LABELS_DIR / f"{args.fixture}.yaml"
    cache_path = RESULTS_DIR / f"{args.fixture}_preds_{args.model}_{args.effort}.json"

    fixture = load_fixture(fixture_path)
    labels = load_labels(labels_path)
    labeled_sids = set(labels["samples"].keys())

    to_run = [c for c in fixture if c["sample_id"] in labeled_sids]
    if not to_run:
        raise SystemExit(f"No labeled samples in {labels_path}. Run label.py first.")

    print(f"Scoring {len(to_run)} labeled samples from {args.fixture}.jsonl")
    print(f"Model: {args.model}, effort: {args.effort}")

    # Run parser (or load cache). Cache stores the full trace (items + thinking
    # + system_prompt) for failure-mode analysis; scoring uses just items.
    trace_by_sid: dict[str, dict]
    if args.dry_run and cache_path.exists():
        trace_by_sid = json.loads(cache_path.read_text())
        # Back-compat: old caches stored plain list of items; wrap them.
        first = next(iter(trace_by_sid.values()), None)
        if isinstance(first, list):
            trace_by_sid = {sid: {"items": items, "thinking": "", "system_prompt": ""}
                            for sid, items in trace_by_sid.items()}
        print(f"Loaded cached predictions from {cache_path}")
    else:
        sem = asyncio.Semaphore(args.concurrency)

        async def one(chunk):
            async with sem:
                try:
                    trace = await run_parser(chunk, args.model, args.effort)
                except Exception as e:
                    print(f"  [{chunk['sample_id']}] parser error: {e}")
                    trace = {"items": [], "thinking": "", "system_prompt": ""}
                return chunk["sample_id"], trace

        results = await atqdm.gather(
            *[one(c) for c in to_run],
            desc=f"parse {args.fixture}",
            unit="chunk")
        trace_by_sid = dict(results)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(trace_by_sid, ensure_ascii=False, indent=2))
        print(f"Cached predictions + traces to {cache_path}")

    preds_by_sid = {sid: t["items"] for sid, t in trace_by_sid.items()}
    report = score_fixture(fixture, labels, preds_by_sid)
    report["meta"] = {
        "fixture": args.fixture,
        "model": args.model,
        "effort": args.effort,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "n_samples": len(to_run),
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{args.fixture}_{args.model}_{args.effort}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    print()
    print(render_summary(report))
    print()
    print(f"Full report: {out}")


if __name__ == "__main__":
    asyncio.run(main())
