"""Preference bench: does the executive-summary LLM pick top-5 items that Adam
actually finds relevant?

Pipeline simulated end-to-end:
    1. Parser runs on each chunk in the fixture → items with (category, status)
    2. Python post-processing assigns priority
    3. Executive-summary LLM sees ALL items → picks top-5 by preference

Labels (labels/preference_{fixture}.yaml) record Adam's relevance tag per item:
    RELEVANT | IRRELEVANT | MAYBE

Metric: Precision@5 = (# top-5 items labeled RELEVANT) / 5.
Plus diagnostic: how many items the model picks that Adam labeled IRRELEVANT.

Usage:
    uv run python benchmark/bench_preference.py --fixture 1d
    uv run python benchmark/bench_preference.py --fixture 1d --label   # interactive labeling
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from rapidfuzz import fuzz
from tqdm.asyncio import tqdm as atqdm

# Reuse label.py's parser invocation
sys.path.insert(0, str(Path(__file__).parent))
from label import load_fixture, run_parser  # noqa: E402

BENCH_DIR = Path(__file__).parent
FIXTURES_DIR = BENCH_DIR / "fixtures"
LABELS_DIR = BENCH_DIR / "labels"
RESULTS_DIR = BENCH_DIR / "results"

RELEVANCE = ["RELEVANT", "MAYBE", "IRRELEVANT"]


# ── Executive-summary simulation ─────────────────────────────────────────────


TOP5_LINE = re.compile(r"^\s*(\d+)\.\s*\[(?P<tag>[A-Z]+)\]\s*\*\*(?P<title>[^*]+)\*\*")


async def generate_top5(all_items: list[dict], date_str: str) -> list[dict]:
    """Invoke the preference-ranking LLM like main._generate_executive_summary."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        TextBlock,
        query)

    def _fmt(item):
        cat = item["category"].upper()
        status = item.get("status") or ""
        priority = item.get("priority") or ""
        meta = " · ".join(filter(None, [priority, status]))
        meta_str = f" ({meta})" if meta else ""
        return (
            f"- [{cat}]{meta_str} {item['title']} | topic={item['topic']}: "
            f"{(item.get('description') or '')[:200]}"
        )

    items_block = "\n".join(_fmt(it) for it in all_items) if all_items else "(none)"

    prompt = f"""Today's ({date_str}) scan from art community Telegram channels.

Pick the top-5 items most relevant to Adam and write one-line rationales.

Adam's interests: 2D illustration, character design, anatomy, figure drawing,
color theory, lighting, rendering, Asian artists, anime/manga, Coloso/Schoolism
courses. De-prioritize: 3D, game dev, photography, UI/UX, motion graphics.

Priority hint: HIGH/MEDIUM/LOW is deterministic from category+status — use as
background signal, but preference-rank by Adam-relevance, not category.
[REQUEST] items can make top-5 if they match his interests.

=== ITEMS ===
{items_block}

FORMAT:

## Top 5 Finds

1. [TAG] **Exact item name** — why it matters. *Topic: {{src}}*
2. [TAG] **Exact item name** — why it matters. *Topic: {{src}}*
3. [TAG] **Exact item name** — why it matters. *Topic: {{src}}*
4. [TAG] **Exact item name** — why it matters. *Topic: {{src}}*
5. [TAG] **Exact item name** — why it matters. *Topic: {{src}}*"""

    options = ClaudeAgentOptions(
        model="claude-haiku-4-5",
        system_prompt="You pick items relevant to Adam and write tight rationales.",
        max_turns=1,
        permission_mode="bypassPermissions",
        effort="low",
        thinking={"type": "disabled"})

    last = None
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    last = block.text

    picks = []
    if last:
        for line in last.splitlines():
            m = TOP5_LINE.match(line)
            if m:
                picks.append({"tag": m.group("tag"), "title": m.group("title").strip()})
                if len(picks) == 5:
                    break
    return picks


# ── Build the item pool (parser + priority) ──────────────────────────────────


_PRIORITY_MAP = {
    ("file", None): "HIGH",
    ("course", "FULFILLED"): "HIGH",
    ("course", None): "MEDIUM",
    ("discussion", None): "MEDIUM",
    ("request", "FULFILLED"): "MEDIUM",
    ("request", None): "LOW",
    ("announcement", None): "LOW",
}


def _priority(cat: str, status: str | None) -> str | None:
    return _PRIORITY_MAP.get((cat, status)) or _PRIORITY_MAP.get((cat, None))


async def build_pool(fixture: list[dict], concurrency: int = 5) -> list[dict]:
    """Run parser + priority on every chunk; return a flat list of items."""
    sem = asyncio.Semaphore(concurrency)

    async def one(chunk):
        async with sem:
            try:
                items = await run_parser(chunk)
            except Exception as e:
                print(f"  [{chunk['sample_id']}] parser error: {e}")
                return []
            out = []
            for it in items:
                out.append({
                    "sample_id": chunk["sample_id"],
                    "topic": chunk["topic_context"],
                    "title": it.get("title", ""),
                    "category": it.get("category", ""),
                    "status": it.get("status"),
                    "priority": _priority(it.get("category", ""), it.get("status")),
                    "description": it.get("description", ""),
                })
            return out

    results = await atqdm.gather(
        *[one(c) for c in fixture],
        desc="build pool",
        unit="chunk")
    flat = [item for sub in results for item in sub]
    return flat


# ── Labeling ─────────────────────────────────────────────────────────────────


def load_labels(path: Path) -> dict:
    if not path.exists():
        return {"items": {}}
    with path.open() as f:
        return yaml.safe_load(f) or {"items": {}}


def save_labels(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, width=100)


def _item_key(item: dict) -> str:
    return f"{item['topic']}::{item['title'][:80]}"


def label_interactive(pool: list[dict], labels: dict, labels_path: Path) -> None:
    print(f"\nLabeling {len(pool)} items. Type R (relevant), M (maybe), I (irrelevant).")
    print("Enter to skip; Ctrl+C to save & exit.\n")
    for i, item in enumerate(pool, 1):
        key = _item_key(item)
        if key in labels["items"]:
            continue
        print(f"[{i}/{len(pool)}] [{item['category'].upper()}] ({item['priority']}) {item['title']}")
        print(f"    topic={item['topic']}")
        if item["description"]:
            print(f"    {item['description'][:200]}")
        while True:
            try:
                choice = input("  R/M/I? > ").strip().upper()
            except (EOFError, KeyboardInterrupt):
                save_labels(labels_path, labels)
                print(f"\nSaved to {labels_path}")
                return
            if choice == "R":
                labels["items"][key] = "RELEVANT"
                break
            if choice == "M":
                labels["items"][key] = "MAYBE"
                break
            if choice == "I":
                labels["items"][key] = "IRRELEVANT"
                break
            if choice == "":
                break  # skip
            print("  invalid; R/M/I")
        save_labels(labels_path, labels)


# ── Scoring ──────────────────────────────────────────────────────────────────


def match_pick_to_pool(pick_title: str, pool: list[dict]) -> dict | None:
    """Fuzzy-match a top-5 pick title back to the pool item."""
    best, best_score = None, 0
    for item in pool:
        score = fuzz.token_set_ratio(pick_title, item["title"])
        if score > best_score:
            best_score = score
            best = item
    return best if best_score >= 60 else None


def score_preference(
    picks: list[dict], pool: list[dict], labels: dict
) -> dict:
    """Precision@5 = frac of picks tagged RELEVANT. Also counts IRRELEVANT / MAYBE / unknown."""
    counts = {"RELEVANT": 0, "MAYBE": 0, "IRRELEVANT": 0, "UNKNOWN": 0}
    details = []
    for p in picks:
        matched = match_pick_to_pool(p["title"], pool)
        if matched is None:
            counts["UNKNOWN"] += 1
            details.append({"pick": p["title"], "label": "UNMATCHED"})
            continue
        key = _item_key(matched)
        label = labels["items"].get(key, "UNKNOWN")
        counts[label] += 1
        details.append({"pick": p["title"], "label": label, "category": matched["category"]})

    p_at_5 = counts["RELEVANT"] / 5 if picks else 0.0
    return {
        "precision_at_5": p_at_5,
        "counts": counts,
        "picks": details,
        "n_picks": len(picks),
        "n_pool": len(pool),
    }


# ── Main ─────────────────────────────────────────────────────────────────────


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--label", action="store_true",
                    help="Build pool and interactively label items; skip scoring")
    ap.add_argument("--rebuild-pool", action="store_true",
                    help="Regenerate pool even if cached")
    args = ap.parse_args()

    fixture_path = FIXTURES_DIR / f"{args.fixture}.jsonl"
    labels_path = LABELS_DIR / f"preference_{args.fixture}.yaml"
    pool_path = RESULTS_DIR / f"pool_{args.fixture}.json"

    fixture = load_fixture(fixture_path)

    # Step 1: build item pool (cached)
    if pool_path.exists() and not args.rebuild_pool:
        pool = json.loads(pool_path.read_text())
        print(f"Loaded cached pool ({len(pool)} items) from {pool_path.name}")
    else:
        pool = await build_pool(fixture)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        pool_path.write_text(json.dumps(pool, ensure_ascii=False, indent=2))
        print(f"Built pool of {len(pool)} items → {pool_path.name}")

    labels = load_labels(labels_path)

    # Label mode: skip scoring
    if args.label:
        label_interactive(pool, labels, labels_path)
        return

    if not labels["items"]:
        raise SystemExit(f"No labels at {labels_path}. Run with --label first.")

    # Step 2: run preference LLM on pool
    date_str = datetime.now().strftime("%Y-%m-%d")
    picks = await generate_top5(pool, date_str)
    if not picks:
        raise SystemExit("Preference LLM produced no picks — check prompt / model output.")

    # Step 3: score
    report = score_preference(picks, pool, labels)
    report["meta"] = {
        "fixture": args.fixture,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    out = RESULTS_DIR / f"preference_{args.fixture}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    print()
    print(f"Precision@5: {report['precision_at_5']:.3f}")
    print(f"Breakdown: {report['counts']}")
    print()
    print("Picks:")
    for d in report["picks"]:
        marker = {"RELEVANT": "✓", "MAYBE": "~", "IRRELEVANT": "×", "UNKNOWN": "?", "UNMATCHED": "?"}[d["label"]]
        print(f"  {marker} [{d.get('category', '—'):<12}] {d['pick'][:70]}")
    print(f"\nReport: {out}")


if __name__ == "__main__":
    asyncio.run(main())
