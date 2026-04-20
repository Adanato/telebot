"""Interactive labeler for benchmark fixtures.

For each chunk in a fixture, runs the parser ONCE to get candidate items, then
prompts you to accept/edit the (category, priority, status) for each item and
flag missing/hallucinated items.

Labels are saved as YAML (one file per fixture) so you can edit them in vim
between sessions. Re-running the labeler resumes where you left off.

Usage:
    uv run python benchmark/label.py --fixture 1d
    uv run python benchmark/label.py --fixture 1d --from s0005   # resume
    uv run python benchmark/label.py --fixture 1d --reparse      # overwrite candidates
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import yaml

from course_scout.infrastructure.agents import (
    AgentOrchestrator,
    StructuredMessage,
    SummarizerInputSchema)

BENCH_DIR = Path(__file__).parent
FIXTURES_DIR = BENCH_DIR / "fixtures"
LABELS_DIR = BENCH_DIR / "labels"

CATEGORIES = ["file", "course", "discussion", "request", "announcement"]
PRIORITIES = ["HIGH", "MEDIUM", "LOW"]
STATUSES = ["FULFILLED", "UNFULFILLED", "DISCUSSING"]


def load_fixture(path: Path) -> list[dict]:
    chunks = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def load_labels(path: Path) -> dict:
    if not path.exists():
        return {"samples": {}}
    with path.open() as f:
        return yaml.safe_load(f) or {"samples": {}}


def save_labels(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, width=100)


async def run_parser_with_trace(
    chunk: dict,
    model: str = "claude-haiku-4-5",
    effort: str = "low",
    thinking: str = "adaptive") -> dict:
    """Invoke the parser, returning items + thinking + system_prompt for analysis.

    Returns {items, thinking, system_prompt, model, effort}. The thinking field
    is empty unless the model emitted ThinkingBlocks (requires thinking=enabled).
    """
    orch = AgentOrchestrator(
        summarizer_model=model,
        effort=effort,
        thinking=thinking)
    agent = orch.get_summarizer_agent()
    msgs = [StructuredMessage(**m) for m in chunk["messages"]]
    input_data = SummarizerInputSchema(
        messages=msgs,
        topic_context=chunk["topic_context"],
        chat_message=chunk.get("chat_message", ""))
    result = await agent.run(input_data)

    # Pull thinking off the provider (side-channel, most recent call)
    provider = list(orch._providers.values())[0] if orch._providers else None
    thinking_text = getattr(provider, "last_thinking", "") if provider else ""

    items = []
    for it in result.items:
        d = it.model_dump() if hasattr(it, "model_dump") else dict(it)
        items.append({
            "title": d.get("title", ""),
            "category": d.get("category", ""),
            "priority": d.get("priority"),
            "status": d.get("status"),
            "description": (d.get("description") or "")[:180],
        })

    return {
        "items": items,
        "thinking": thinking_text,
        "system_prompt": agent.system_prompt,
        "model": model,
        "effort": effort,
    }


async def run_parser(chunk: dict) -> list[dict]:
    """Invoke the parser; return just the items. Thin wrapper over run_parser_with_trace."""
    trace = await run_parser_with_trace(chunk)
    return trace["items"]


def print_chunk(chunk: dict) -> None:
    print("=" * 80)
    print(f"SAMPLE {chunk['sample_id']}  ·  {chunk['topic_context']}")
    print("-" * 80)
    for m in chunk["messages"][:15]:
        content = (m.get("content") or "").replace("\n", " ")
        print(f"  #{m['id']}: {content[:140]}")
    if len(chunk["messages"]) > 15:
        print(f"  … {len(chunk['messages']) - 15} more messages")
    print()


def edit_item(it: dict) -> dict | None:
    """Prompt for corrections on a single item."""
    print(f"  title:    {it['title']}")
    print(f"  category: {it['category']}  (valid: {'/'.join(CATEGORIES)})")
    print(f"  priority: {it['priority']}  (valid: {'/'.join(PRIORITIES)}/None)")
    print(f"  status:   {it['status']}  (valid: {'/'.join(STATUSES)}/None)")
    choice = input("  [y]=ok  [c]at  [p]ri  [s]tat  [d]elete  > ").strip().lower()
    if choice in ("", "y"):
        return it
    if choice == "d":
        return None
    if choice == "c":
        new = input(f"  new category [{'/'.join(CATEGORIES)}]: ").strip()
        if new in CATEGORIES:
            it["category"] = new
    if choice == "p":
        new = input(f"  new priority [{'/'.join(PRIORITIES)}/none]: ").strip().upper()
        it["priority"] = new if new in PRIORITIES else None
    if choice == "s":
        new = input(f"  new status [{'/'.join(STATUSES)}/none]: ").strip().upper()
        it["status"] = new if new in STATUSES else None
    return it


def label_chunk(chunk: dict, candidates: list[dict]) -> dict:
    print_chunk(chunk)
    print(f"Parser produced {len(candidates)} candidate items:\n")
    labeled = []
    for i, cand in enumerate(candidates, 1):
        print(f"[{i}/{len(candidates)}]")
        result = edit_item(dict(cand))
        if result is not None:
            labeled.append(result)
    # Missed items
    while True:
        extra = input("Add a MISSED item? (title or Enter to skip): ").strip()
        if not extra:
            break
        cat = input(f"  category [{'/'.join(CATEGORIES)}]: ").strip()
        if cat not in CATEGORIES:
            print("  invalid, skipping")
            continue
        pri = input(f"  priority [{'/'.join(PRIORITIES)}/none]: ").strip().upper()
        stat = input(f"  status [{'/'.join(STATUSES)}/none]: ").strip().upper()
        labeled.append({
            "title": extra,
            "category": cat,
            "priority": pri if pri in PRIORITIES else None,
            "status": stat if stat in STATUSES else None,
            "missed": True,
        })
    return {
        "expected": labeled,
        "candidate_count": len(candidates),
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--from", dest="start_from", default=None,
                    help="Resume from sample_id (e.g. s0010)")
    ap.add_argument("--reparse", action="store_true",
                    help="Re-run parser even if cached candidate exists")
    args = ap.parse_args()

    fixture_path = FIXTURES_DIR / f"{args.fixture}.jsonl"
    labels_path = LABELS_DIR / f"{args.fixture}.yaml"
    if not fixture_path.exists():
        raise SystemExit(f"Fixture not found: {fixture_path}. Run sample.py first.")

    chunks = load_fixture(fixture_path)
    labels = load_labels(labels_path)

    started = args.start_from is None
    for chunk in chunks:
        sid = chunk["sample_id"]
        if not started:
            if sid == args.start_from:
                started = True
            else:
                continue
        if sid in labels["samples"] and not args.reparse:
            continue

        try:
            candidates = await run_parser(chunk)
        except Exception as e:
            print(f"[{sid}] parser error: {e}", file=sys.stderr)
            continue

        try:
            result = label_chunk(chunk, candidates)
        except (KeyboardInterrupt, EOFError):
            save_labels(labels_path, labels)
            print(f"\nSaved progress to {labels_path}")
            return

        labels["samples"][sid] = result
        save_labels(labels_path, labels)

    print(f"Done. Labels saved to {labels_path}")


if __name__ == "__main__":
    asyncio.run(main())
