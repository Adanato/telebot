"""Silver labels using Opus 4.7 (stronger model than the parser).

This is NOT ground truth. It's a stronger-model-as-judge approach:
- Opus 4.7 reads each chunk and produces the "should have been" item list
- Flags items it's uncertain about (for manual review)
- Applies stricter category rules than the parser gets

Reviewer 1's warning still applies: if Opus and Haiku share a blind spot,
this bench looks healthy while being wrong. Use this as a dev-loop metric
with a caveat, not as production-grade ground truth.

Usage:
    uv run python benchmark/silver_label.py --fixture canon10
    uv run python benchmark/silver_label.py --fixture canon10 --uncertain-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import yaml
from tqdm.asyncio import tqdm as atqdm

sys.path.insert(0, str(Path(__file__).parent))
from label import load_fixture  # noqa: E402


BENCH_DIR = Path(__file__).parent
FIXTURES_DIR = BENCH_DIR / "fixtures"
LABELS_DIR = BENCH_DIR / "labels"

SILVER_SYSTEM_PROMPT = """You are a strict ground-truth labeler for a Telegram message classification benchmark.
Your job: read a chunk of messages from an art community channel and produce the
EXACT set of digest items a careful human would extract.

Be STRICTER than a production parser:
- If a course name is mentioned without a verdict or link, SKIP it (not a `course`).
- If a request has no download link in reply, status=UNFULFILLED (storefront links don't count).
- If a message is pure chat/greeting/emoji, produce NOTHING for it.
- Group reply chains into one item; do not emit duplicates.
- If a topic name contains "Request" or "Download", EVERY non-discussion item MUST be category=request.

CATEGORIES (strict definitions):
- file: an actual download link (mega, drive, pan.baidu, t.me/c/... internal, .zip, magnet)
- course: a named course WITH a verdict/review (not a bare name)
- discussion: technique / tool / workflow conversation, no download
- request: someone asking for a resource (status tells you if fulfilled)
- announcement: community news, event, release date, moderation notice

UNCERTAINTY FLAGGING:
For each item you emit, add "uncertainty": a score 0.0-1.0.
  0.0 = obviously correct, no ambiguity
  0.5 = plausible alternative classification exists
  0.9 = genuinely unsure, flag for human review

OUTPUT: JSON object with "items" (list) and "key_links" (list, usually empty).
Each item has: title, description (1 line), category, status (if applicable),
instructor, platform, password, msg_ids, uncertainty, uncertainty_reason.
"""


async def silver_label_chunk(chunk: dict) -> dict:
    """Run Opus 4.7 on one chunk, return silver labels with uncertainty scores."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        TextBlock,
        ToolUseBlock,
        query,
    )

    # Build a compact representation of the chunk
    msgs_block = "\n".join(
        f"  #{m['id']}: {(m.get('content') or '')[:300]}"
        for m in chunk["messages"]
    )
    prompt = f"""Topic: {chunk["topic_context"]}

Messages ({len(chunk["messages"])}):
{msgs_block}

Produce the strict ground-truth item list for this chunk."""

    # JSON schema for the silver output (looser than parser — no discriminated union)
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "category": {"type": "string", "enum": [
                            "file", "course", "discussion", "request", "announcement"
                        ]},
                        "status": {"type": ["string", "null"], "enum": [
                            "FULFILLED", "UNFULFILLED", "DISCUSSING", None
                        ]},
                        "instructor": {"type": ["string", "null"]},
                        "platform": {"type": ["string", "null"]},
                        "password": {"type": ["string", "null"]},
                        "msg_ids": {"type": "array", "items": {"type": "integer"}},
                        "uncertainty": {"type": "number"},
                        "uncertainty_reason": {"type": "string"},
                    },
                    "required": ["title", "category", "msg_ids", "uncertainty"],
                },
            },
        },
        "required": ["items"],
    }

    options = ClaudeAgentOptions(
        model="claude-opus-4-7",
        system_prompt=SILVER_SYSTEM_PROMPT,
        max_turns=1,
        setting_sources=[],
        disallowed_tools=[
            "Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit",
            "Glob", "Grep", "WebFetch", "WebSearch", "Agent",
        ],
        thinking={"type": "enabled", "budget_tokens": 10000},
        effort="high",
        output_format={"type": "json_schema", "schema": schema},
    )

    structured = None
    tool_output = None
    last_text = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock) and block.name == "StructuredOutput":
                    tool_output = block.input
                elif isinstance(block, TextBlock):
                    last_text = block.text
    # Prefer structured, then tool_output, then text
    if tool_output is not None:
        return tool_output
    if structured is not None:
        return structured
    if last_text:
        return json.loads(last_text.strip().lstrip("```json").rstrip("```"))
    return {"items": []}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--concurrency", type=int, default=3,
                    help="Opus is slower and more expensive; keep low")
    ap.add_argument("--uncertain-threshold", type=float, default=0.6,
                    help="Flag items with uncertainty ≥ this for manual review")
    args = ap.parse_args()

    fixture_path = FIXTURES_DIR / f"{args.fixture}.jsonl"
    labels_path = LABELS_DIR / f"{args.fixture}.yaml"
    uncertain_path = LABELS_DIR / f"{args.fixture}_uncertain.md"

    chunks = load_fixture(fixture_path)
    print(f"Silver-labeling {len(chunks)} chunks with Opus 4.7 (concurrency={args.concurrency})")
    print(f"Expected wall-clock: ~{len(chunks) * 15 / args.concurrency / 60:.1f} min")

    sem = asyncio.Semaphore(args.concurrency)

    async def one(chunk):
        async with sem:
            try:
                result = await silver_label_chunk(chunk)
                return chunk["sample_id"], result
            except Exception as e:
                print(f"  [{chunk['sample_id']}] error: {e}")
                return chunk["sample_id"], {"items": [], "error": str(e)}

    results = await atqdm.gather(
        *[one(c) for c in chunks],
        desc=f"silver-label {args.fixture}",
        unit="chunk",
    )

    # Save labels in the standard format (`expected` key for bench_categorize)
    data = {"samples": {}}
    uncertain_items = []
    for sid, result in results:
        items = result.get("items", [])
        data["samples"][sid] = {
            "expected": items,
            "candidate_count": len(items),
            "source": "silver_opus_4_7",
        }
        # Collect uncertain items for spot-check queue
        for item in items:
            if item.get("uncertainty", 0) >= args.uncertain_threshold:
                uncertain_items.append((sid, item))

    labels_path.parent.mkdir(parents=True, exist_ok=True)
    with labels_path.open("w") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, width=100)
    print(f"\nSilver labels: {labels_path}")
    print(f"  {sum(len(v['expected']) for v in data['samples'].values())} total items")

    # Uncertain queue (markdown for easy manual review)
    with uncertain_path.open("w") as f:
        f.write(f"# Uncertain items to review ({len(uncertain_items)} total)\n\n")
        f.write(f"These items had Opus uncertainty ≥ {args.uncertain_threshold}.\n")
        f.write("Review each and correct `labels/{fixture}.yaml` if needed.\n\n")
        for sid, item in uncertain_items:
            f.write(f"## {sid} — [{item['category']}] {item['title']}\n\n")
            f.write(f"- **Uncertainty**: {item['uncertainty']:.2f}\n")
            if item.get("uncertainty_reason"):
                f.write(f"- **Reason**: {item['uncertainty_reason']}\n")
            f.write(f"- **Status**: {item.get('status', '—')}\n")
            f.write(f"- **Description**: {item.get('description', '')}\n")
            f.write(f"- **msg_ids**: {item.get('msg_ids', [])}\n\n")
    print(f"Uncertain queue ({len(uncertain_items)} items): {uncertain_path}")


if __name__ == "__main__":
    asyncio.run(main())
