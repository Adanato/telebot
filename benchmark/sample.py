"""Extract parser-input fixtures from course-scout logs.

Each "input data" DEBUG log line is one chunk the parser saw. Two sampling modes:

1. --days N        : last N calendar days from latest log entry (1/7/30 legacy)
2. --per-channel N : walks back per-channel until each channel has N non-empty
                     days (robust to quiet channels — some might need 30+ days
                     of calendar to reach 10 non-empty days)

Usage:
    uv run python benchmark/sample.py --days 1
    uv run python benchmark/sample.py --days 7
    uv run python benchmark/sample.py --days 30
    uv run python benchmark/sample.py --per-channel 10       # canonical fixture
    uv run python benchmark/sample.py --per-channel 10 --full-topic
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

BENCH_DIR = Path(__file__).parent
FIXTURES_DIR = BENCH_DIR / "fixtures"
LOG_FILE = BENCH_DIR.parent / "logs" / "course_scout.log"

INPUT_LINE = re.compile(
    r"^(?P<ts>\d{2}:\d{2}:\d{2})\s-\s"
    r"course_scout\.infrastructure\.agents\s-\sDEBUG\s-\s"
    r"Agent\s(?P<model>\S+)\sinput data:\s(?P<payload>\{.*\})\s*$"
)


def iter_chunks(log_path: Path):
    """Yield (model, payload_json) tuples for every parser-input line in the log."""
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = INPUT_LINE.match(line.rstrip("\n"))
            if not m:
                continue
            try:
                payload = json.loads(m.group("payload"))
            except json.JSONDecodeError:
                continue
            yield m.group("model"), payload


def infer_scan_date(payload: dict) -> date | None:
    """Pull the first message's UTC date out of the payload."""
    msgs = payload.get("messages") or []
    if not msgs:
        return None
    ts = msgs[0].get("timestamp")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).date()
    except ValueError:
        return None


def build_fixture(days: int) -> list[dict]:
    """Return chunks whose first-message date is within `days` of the latest log entry.

    We anchor on the most recent message date in the log rather than today —
    this lets you build a "1-day fixture" from a scan that ran last week.
    """
    all_pairs = list(iter_chunks(LOG_FILE))
    dates = [d for _, p in all_pairs if (d := infer_scan_date(p)) is not None]
    if not dates:
        return []
    latest = max(dates)
    cutoff = latest - timedelta(days=days - 1)

    seen_ids: set[tuple] = set()
    chunks: list[dict] = []

    for model, payload in all_pairs:
        d = infer_scan_date(payload)
        if d is None or d < cutoff:
            continue
        # Dedup on (topic_context, first message id, last message id)
        msgs = payload.get("messages") or []
        if not msgs:
            continue
        key = (payload.get("topic_context"), msgs[0].get("id"), msgs[-1].get("id"))
        if key in seen_ids:
            continue
        seen_ids.add(key)
        chunks.append({
            "sample_id": f"s{len(chunks):04d}",
            "model": model,
            "topic_context": payload.get("topic_context", ""),
            "messages": msgs,
            "chat_message": payload.get("chat_message", ""),
            "scan_date": d.isoformat(),
        })
    return chunks


def build_per_channel_fixture(n_per_channel: int) -> list[dict]:
    """Return ~N non-empty days PER channel. Canonical bench fixture.

    For each topic_context, walks back through the log collecting distinct-date
    chunks until the channel has `n_per_channel` non-empty days. Each chunk
    represents one topic's full day (merges same-topic-same-day chunks, dedup
    on msg_id). Total samples ≈ n_per_channel × 13 (less for quiet channels).
    """
    from collections import defaultdict

    all_pairs = list(iter_chunks(LOG_FILE))
    by_topic_date: dict[tuple[str, date], dict] = {}
    for _model, payload in all_pairs:
        d = infer_scan_date(payload)
        if d is None:
            continue
        msgs = payload.get("messages") or []
        if not msgs:
            continue
        topic = payload.get("topic_context", "")
        key = (topic, d)
        existing = by_topic_date.get(key)
        if existing is None:
            by_topic_date[key] = {
                "topic_context": topic,
                "messages": list(msgs),
                "chat_message": payload.get("chat_message", ""),
                "scan_date": d.isoformat(),
            }
        else:
            seen_ids = {m.get("id") for m in existing["messages"]}
            for m in msgs:
                if m.get("id") not in seen_ids:
                    existing["messages"].append(m)
                    seen_ids.add(m.get("id"))

    by_topic: dict[str, list] = defaultdict(list)
    for (topic, d), entry in by_topic_date.items():
        by_topic[topic].append((d, entry))

    chunks: list[dict] = []
    for topic, dated_entries in sorted(by_topic.items()):
        dated_entries.sort(key=lambda x: x[0], reverse=True)
        kept = dated_entries[:n_per_channel]
        for _d, entry in kept:
            entry = dict(entry)
            entry["sample_id"] = f"s{len(chunks):04d}"
            chunks.append(entry)
    return chunks


def merge_to_full_topic_day(chunks: list[dict]) -> list[dict]:
    """Merge chunks with the same (topic, day) so each sample = one topic's full day.

    Lets us test whether giving the parser the full day's messages per topic at
    once (no within-topic chunking) improves accuracy vs the default 25-msg slices.
    """
    from collections import OrderedDict
    groups: dict[tuple[str, str], dict] = OrderedDict()
    for c in chunks:
        key = (c["topic_context"], c["scan_date"])
        if key not in groups:
            groups[key] = {**c, "messages": list(c["messages"])}
        else:
            groups[key]["messages"].extend(c["messages"])

    out = []
    for i, c in enumerate(groups.values()):
        # dedupe by message id within the merged bundle
        seen_ids = set()
        dedup_msgs = []
        for m in c["messages"]:
            mid = m.get("id")
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
            dedup_msgs.append(m)
        c["messages"] = dedup_msgs
        c["sample_id"] = f"s{i:04d}"
        out.append(c)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, choices=[1, 7, 30],
                    help="Calendar-day fixture: last N days from latest log entry")
    ap.add_argument("--per-channel", type=int, dest="per_channel",
                    help="Per-channel fixture: N non-empty days PER channel. Canonical bench.")
    ap.add_argument("--full-topic", dest="full_topic", action="store_true",
                    help="Merge same-topic-same-day chunks so each sample is one topic's full day. "
                         "Output: {N}d_full.jsonl or canon{N}.jsonl")
    args = ap.parse_args()
    if not args.days and not args.per_channel:
        ap.error("must specify --days OR --per-channel")

    if not LOG_FILE.exists():
        raise SystemExit(f"Log not found: {LOG_FILE}")

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    if args.per_channel:
        chunks = build_per_channel_fixture(args.per_channel)
        out = FIXTURES_DIR / f"canon{args.per_channel}.jsonl"
    else:
        chunks = build_fixture(args.days)
        if args.full_topic:
            chunks = merge_to_full_topic_day(chunks)
        suffix = "_full" if args.full_topic else ""
        out = FIXTURES_DIR / f"{args.days}d{suffix}.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    by_topic: dict[str, int] = {}
    msg_count = 0
    for c in chunks:
        by_topic[c["topic_context"]] = by_topic.get(c["topic_context"], 0) + 1
        msg_count += len(c["messages"])

    print(f"Wrote {len(chunks)} chunks ({msg_count} messages) to {out}")
    for topic, n in sorted(by_topic.items(), key=lambda x: -x[1]):
        print(f"  {n:4d}  {topic}")


if __name__ == "__main__":
    main()
