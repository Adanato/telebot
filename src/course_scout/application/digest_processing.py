"""Per-topic digest post-processing: category allowlist, reclassification, priority.

Pure application-layer logic that runs after the LLM summarizer returns a
ChannelDigest. These transforms are deterministic guardrails — the parser
emits some category/priority hints we OVERWRITE here so the routing signal
is consistent with the topic's role.

Ordering matters when called by BatchScanUseCase:
  1. enforce_category_allowlist — drop/remap items that violate the prompt's
     category contract (file_sharing emits a `course` → coerce to `file`).
  2. reclassify_by_topic_name   — if the topic title contains "Request" /
     "Download", every actionable item becomes a request.
  3. assign_priority            — deterministic (category, status) → HIGH/MED/LOW.

All three rewrites log to logs/overrides.jsonl for offline audit.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

# Override audit log. Every time a Python post-processor rewrites the LLM's output,
# we record (stage, topic, before_category, after_category, title). Used for
# offline accuracy review of the deterministic guardrails.
_OVERRIDE_LOG_PATH = "logs/overrides.jsonl"


def override_log(
    stage: str,
    topic: str,
    before: str,
    after: str | None,
    title: str,
    reason: str = "",
) -> None:
    """Append a single override event to the JSONL audit log."""
    path = Path(_OVERRIDE_LOG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "ts": datetime.now().isoformat(),
                    "stage": stage,
                    "topic": topic,
                    "before": before,
                    "after": after,
                    "title": title,
                    "reason": reason,
                }
            )
            + "\n"
        )


# Category allowlist per prompt family. Maps YAML prompt name → allowed categories.
# If the parser emits a category outside this set (e.g. file_sharing emits
# `course`), the item is remapped to the nearest allowed category — or dropped
# if nothing fits. Enforces the prompt restriction in Python, since prompt-only
# enforcement leaks at semantic boundaries (e.g. Gumroad + media attachments).
_PROMPT_ALLOWED_CATEGORIES: dict[str, set[str]] = {
    "course_requests": {"request"},
    "file_sharing": {"file", "discussion"},
    "discussion_lounge": {"discussion", "course", "file", "request"},
    "course_review": {"course", "discussion"},
    "language_chat": {"file", "course", "discussion", "request", "announcement"},
}

# Remap rules for each allowlist violation. If the parser emits X in a channel
# that allows Y, convert X → Y (preserving all other fields). `None` = drop.
_CATEGORY_REMAP: dict[tuple[str, str], str | None] = {
    ("file_sharing", "course"): "file",
    ("file_sharing", "request"): None,
    ("file_sharing", "announcement"): "discussion",
    ("discussion_lounge", "announcement"): "discussion",
    ("course_review", "file"): "course",
    ("course_review", "request"): None,
    ("course_review", "announcement"): "discussion",
}


def enforce_category_allowlist(
    digest: Any, system_prompt_name: str | None, topic_name: str = ""
) -> None:
    """Drop/remap items whose category is outside the prompt's allowlist."""
    if system_prompt_name is None:
        return
    allowed = _PROMPT_ALLOWED_CATEGORIES.get(system_prompt_name)
    if allowed is None:
        return

    from course_scout.domain.models import (
        AnnouncementItem,
        CourseItem,
        DiscussionItem,
        FileItem,
        RequestItem,
    )

    category_to_cls = {
        "course": CourseItem,
        "file": FileItem,
        "discussion": DiscussionItem,
        "request": RequestItem,
        "announcement": AnnouncementItem,
    }

    new_items = []
    for item in digest.items:
        if item.category in allowed:
            new_items.append(item)
            continue
        remap_target = _CATEGORY_REMAP.get((system_prompt_name, item.category))
        if remap_target is None:
            override_log(
                "allowlist",
                topic_name,
                item.category,
                None,
                item.title,
                f"{system_prompt_name} drops {item.category}",
            )
            continue
        target_cls = category_to_cls[remap_target]
        try:
            data = item.model_dump(exclude={"category"})
            new_items.append(target_cls(**data))
            override_log(
                "allowlist",
                topic_name,
                item.category,
                remap_target,
                item.title,
                f"{system_prompt_name} remaps {item.category}→{remap_target}",
            )
        except Exception as e:
            override_log(
                "allowlist",
                topic_name,
                item.category,
                None,
                item.title,
                f"remap failed: {e}",
            )
            continue
    digest.items = new_items


def reclassify_by_topic_name(digest: Any, topic_name: str) -> None:
    """Force actionable items into RequestItem when topic is a Requests feed.

    Parser LLMs often promote a fulfilled request (with download link) to
    category=file, even when the source topic is "Coloso Requests" /
    "Domestika Requests". Topic framing is ground truth: a request topic
    contains requests, not files. Discussions are left alone — those can
    legitimately be meta-discussions in a request topic.
    """
    from course_scout.domain.models import RequestItem

    if not any(k in topic_name.lower() for k in ("request", "download")):
        return

    new_items = []
    seen_titles: set[str] = set()
    for item in digest.items:
        if item.category == "discussion":
            new_items.append(item)
            continue
        key = item.title.strip().lower()
        if key in seen_titles:
            override_log(
                "reclassify", topic_name, item.category, None, item.title, "duplicate title"
            )
            continue
        seen_titles.add(key)
        if item.category == "request":
            new_items.append(item)
        else:
            override_log(
                "reclassify",
                topic_name,
                item.category,
                "request",
                item.title,
                "request-topic → request",
            )
            data = item.model_dump(exclude={"category"})
            new_items.append(RequestItem(**data))
    digest.items = new_items


# Deterministic priority from (category, status). The parser emits priority too,
# but we OVERWRITE it here — priority is a pipeline routing signal, not LLM
# judgment.
_PRIORITY_MAP: dict[tuple[str, str | None], str] = {
    ("file", None): "HIGH",
    ("course", "FULFILLED"): "HIGH",
    ("course", None): "MEDIUM",
    ("discussion", None): "MEDIUM",
    ("request", "FULFILLED"): "MEDIUM",
    ("request", None): "LOW",
    ("announcement", None): "LOW",
}


def assign_priority(digest: Any) -> None:
    """Overwrite each item's priority based on (category, status)."""
    for item in digest.items:
        cat = item.category
        status = getattr(item, "status", None)
        priority = _PRIORITY_MAP.get((cat, status)) or _PRIORITY_MAP.get((cat, None))
        if priority is not None:
            item.priority = priority
