"""Per-image vision pre-processor.

Runs a cheap Haiku vision call on each attached image and returns a short
text caption. The main parser stays text-only — it sees
  "[Media/File: <caption>]"
instead of raw bytes.

Captions are cached to `media_cache/captions.json` keyed by filename. Rescanning
the same day skips the vision pass entirely on cache hits.

This decouples image understanding (one cheap call per image, parallelizable)
from item extraction (one text call per topic, fast). Also sidesteps the
SDK hang observed with Sonnet + 10 base64 images in a single call.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from pathlib import Path

from collections.abc import AsyncIterator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    query,
)


async def _stream_user_turn(content: list[dict]) -> AsyncIterator[dict]:
    """Wrap content blocks in the stream-json user-turn envelope required by
    `claude_agent_sdk.query()`. A bare list of content blocks hangs the
    bundled CLI subprocess (stdin never closed). See claude_provider for
    the shared helper — duplicated here to keep vision.py standalone.
    """
    yield {
        "type": "user",
        "message": {"role": "user", "content": content},
        "parent_tool_use_id": None,
        "session_id": None,
    }

logger = logging.getLogger(__name__)

# Persistent caption cache — avoids re-captioning on repeated scans of the
# same day. Key: basename (e.g. "media_566744.jpg"), value: caption string.
_CACHE_PATH = Path("media_cache/captions.json")
_cache: dict[str, str] | None = None
_cache_lock = asyncio.Lock()


def _load_cache() -> dict[str, str]:
    global _cache
    if _cache is not None:
        return _cache
    if _CACHE_PATH.exists():
        try:
            _cache = json.loads(_CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            _cache = {}
    else:
        _cache = {}
    return _cache


async def _save_cache() -> None:
    """Atomically persist cache to disk. Call under lock."""
    assert _cache is not None
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_cache, ensure_ascii=False, indent=2))
    tmp.replace(_CACHE_PATH)

_VISION_SYSTEM_PROMPT = (
    "You caption image attachments from an art community. One short line. "
    "Identify: (1) book/course titles visible (any language — also transliterate), "
    "(2) artist/instructor names, (3) platform/logos (Coloso, Schoolism, etc.), "
    "(4) any readable text on covers or screenshots. "
    "If nothing identifiable, say 'art/figure/sketch (no titles visible)'. "
    "Never more than 2 lines. Never add commentary."
)

_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_SUPPORTED = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}


async def caption_image(path: str, model: str = "claude-haiku-4-5") -> str:
    """Return a short text caption for one image. Empty string on any failure.

    Cached in media_cache/captions.json — filename key, cache survives across
    scans so re-running a day doesn't re-caption the same images.
    """
    if not path or not os.path.exists(path):
        return ""
    ext = os.path.splitext(path)[1].lower()
    media_type = _SUPPORTED.get(ext)
    if media_type is None:
        return ""

    cache = _load_cache()
    key = os.path.basename(path)
    cached = cache.get(key)
    if cached is not None:
        return cached

    try:
        if os.path.getsize(path) > _MAX_IMAGE_BYTES:
            return ""
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode("ascii")
    except OSError:
        return ""

    content = [
        {"type": "text", "text": "Caption this image."},
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
    ]
    options = ClaudeAgentOptions(
        model=model,
        system_prompt=_VISION_SYSTEM_PROMPT,
        max_turns=1,
        setting_sources=[],
        allowed_tools=[],
        thinking={"type": "disabled"},
    )

    caption_parts: list[str] = []
    try:
        async for msg in query(prompt=_stream_user_turn(content), options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        caption_parts.append(block.text)
    except Exception as e:
        logger.warning(f"Vision caption failed for {path}: {e}")
        return ""

    caption = " ".join(caption_parts).strip().replace("\n", " ")
    if caption:
        async with _cache_lock:
            cache[key] = caption
            await _save_cache()
    return caption


async def caption_paths(paths: list[str], concurrency: int = 5) -> dict[str, str]:
    """Caption multiple images in parallel. Returns {path → caption}."""
    if not paths:
        return {}
    sem = asyncio.Semaphore(concurrency)

    async def one(p: str) -> tuple[str, str]:
        async with sem:
            c = await caption_image(p)
            return p, c

    results = await asyncio.gather(*[one(p) for p in paths])
    return {p: c for p, c in results if c}
