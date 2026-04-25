"""Per-topic pinned-message cache + diff.

Telegram forum topics pin important posts (mega-link rotations, rules, index
messages). Pin state changes carry operational signal — a replaced pin often
means a new password or a dead link being swapped out. This module:

  1. Snapshots the current pinned-message set per (channel_id, topic_id).
  2. Diffs against a persisted cache at `media_cache/pins.json`.
  3. Produces a markdown block for the daily digest when anything changed.

No Telegram notifications — per user decision, pin changes surface in the
daily digest summary where they get read alongside everything else.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from course_scout.domain.models import TelegramMessage

logger = logging.getLogger(__name__)

_CACHE_PATH = Path("media_cache/pins.json")
_PREVIEW_LEN = 200


def _cache_key(channel_id: str | int, topic_id: int | None) -> str:
    return f"{channel_id}:{topic_id if topic_id is not None else 'root'}"


def _snapshot_of(m: TelegramMessage) -> dict:
    """Minimal snapshot used for diffing (avoids storing full message body)."""
    text = (m.text or "")[:_PREVIEW_LEN]
    return {
        "text_preview": text,
        "date": m.date.isoformat() if m.date else None,
        "link": m.link,
    }


def load_pin_cache() -> dict[str, dict]:
    if not _CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"pins.json unreadable ({e}); starting fresh")
        return {}


def save_pin_cache(cache: dict[str, dict]) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    tmp.replace(_CACHE_PATH)


@dataclass
class PinDiff:
    added: list[TelegramMessage] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)  # snapshots, not live msgs
    changed: list[tuple[dict, TelegramMessage]] = field(default_factory=list)  # (old, new)
    first_run: bool = False

    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


def diff_pins(cached_entry: dict | None, current: list[TelegramMessage]) -> PinDiff:
    """Diff current pinned set against the cached snapshot.

    `cached_entry` shape: {"pinned_ids": [int], "snapshots": {id_str: {...}}}
    Returns first_run=True when no cache exists yet — callers should save
    the snapshot and skip emitting markdown (no "all pins are new" spam).
    """
    if not cached_entry:
        return PinDiff(first_run=True)

    cached_ids: set[int] = set(cached_entry.get("pinned_ids", []))
    cached_snaps: dict[str, dict] = cached_entry.get("snapshots", {})

    current_by_id = {m.id: m for m in current}
    current_ids = set(current_by_id.keys())

    added_ids = current_ids - cached_ids
    removed_ids = cached_ids - current_ids
    common_ids = current_ids & cached_ids

    added = [current_by_id[i] for i in added_ids]
    removed = [cached_snaps[str(i)] for i in removed_ids if str(i) in cached_snaps]
    changed: list[tuple[dict, TelegramMessage]] = []
    for i in common_ids:
        old_snap = cached_snaps.get(str(i))
        new_msg = current_by_id[i]
        if old_snap and old_snap.get("text_preview") != (new_msg.text or "")[:_PREVIEW_LEN]:
            changed.append((old_snap, new_msg))

    return PinDiff(added=added, removed=removed, changed=changed, first_run=False)


def build_snapshot_entry(current: list[TelegramMessage]) -> dict:
    """Serialize the current pinned set for caching."""
    return {
        "pinned_ids": [m.id for m in current],
        "snapshots": {str(m.id): _snapshot_of(m) for m in current},
    }


def _truncate(text: str | None, n: int = 120) -> str:
    if not text:
        return "(no text)"
    text = text.replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def format_pin_diff_markdown(diff: PinDiff) -> str | None:
    """Render pin changes as a markdown block for injection into the digest.

    Returns None when nothing changed (or on first run) so the caller can
    skip adding an empty section.
    """
    if diff.is_empty() or diff.first_run:
        return None

    lines = ["### 📌 Pin Changes"]
    for m in diff.added:
        lines.append(f"- **Added**: {_truncate(m.text)} — [msg]({m.link})")
    for snap in diff.removed:
        lines.append(f"- **Removed**: {_truncate(snap.get('text_preview'))}")
    for old, new in diff.changed:
        lines.append(
            f"- **Edited**: {_truncate(old.get('text_preview'), 80)} "
            f"→ {_truncate(new.text, 80)} — [msg]({new.link})"
        )
    return "\n".join(lines)


async def diff_and_record(scraper, channel_id: str | int, topic_id: int | None) -> str | None:
    """Orchestration helper: fetch pins, diff, persist, return markdown block.

    Designed to be called from the per-topic scan loop alongside summarization.
    Errors swallowed (returns None) — pinning must never break the main scan.
    """
    try:
        current = await scraper.get_pinned_messages(channel_id, topic_id)
    except Exception as e:
        logger.warning(f"Pin fetch failed for {channel_id}:{topic_id}: {e}")
        return None

    cache = load_pin_cache()
    key = _cache_key(channel_id, topic_id)
    diff = diff_pins(cache.get(key), current)

    cache[key] = build_snapshot_entry(current)
    try:
        save_pin_cache(cache)
    except OSError as e:
        logger.warning(f"Failed to save pin cache: {e}")

    return format_pin_diff_markdown(diff)
