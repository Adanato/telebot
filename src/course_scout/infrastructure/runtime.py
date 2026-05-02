"""Runtime configuration singleton.

Single source of truth for tunable knobs (timeouts, retry counts, rate limits,
log paths). Reads the `runtime:` block from `config.yaml` once on first access
and caches the result. Importable from anywhere — does not require Settings
or env vars to be loaded.

Usage:
    from course_scout.infrastructure.runtime import get_runtime

    rt = get_runtime()
    timeout = rt.provider_call_timeout
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class RuntimeConfig(BaseModel):
    """Tunable knobs loaded from `config.yaml` at startup.

    All fields have sensible defaults; the `runtime:` block in `config.yaml` is
    optional. Values shown here are what's used when the block is missing.
    """

    # ── API call layer (Anthropic / Claude Agent SDK) ──
    provider_call_timeout: float = 600.0
    """Outer wrapper timeout on `provider.generate_structured()` (seconds).
    Observed: SDK calls can hang indefinitely with no error. 10 minutes covers
    worst-case Sonnet+multi-modal on dense topics."""

    max_retries: int = 3
    """Per-model retry attempts before falling back to next model in the chain."""

    rate_limit_retry_sleep: float = 65.0
    """Sleep duration when a 429 / RATE error is detected (seconds). 65s exceeds
    the typical 60s window for Anthropic rate-limit windows."""

    max_turns: int = 5
    """Max turns per `claude_agent_sdk.query()`. With structured output,
    Haiku occasionally needs 3-4 turns; 5 is a generous ceiling."""

    rate_limit_rpm: int = 50
    """Local rate limiter requests-per-minute. Spaces out the SDK calls so we
    don't hit Anthropic's per-minute limits ourselves."""

    # ── Telegram fetch layer ──
    topic_fetch_timeout: float = 180.0
    """Per-topic Telethon fetch timeout (seconds). Telethon retries connection
    drops indefinitely; we cap to skip-and-continue."""

    # ── Vision layer ──
    max_images_per_call: int = 20
    """Max image attachments included in a single LLM call. Above this we drop
    extras to keep the call within the SDK's multipart limits."""

    # ── Logging ──
    log_path: str = "/tmp/course-scout-runtime.log"
    """Append-only JSON-line log of each run (start, end, duration, status, error).
    Lives in /tmp by default — lost on reboot, which is fine for failure visibility."""


@lru_cache(maxsize=1)
def get_runtime(config_path: str = "config.yaml") -> RuntimeConfig:
    """Load and cache runtime config from `config.yaml`.

    Reads only the `runtime:` block. Returns defaults if the file is missing,
    the block is absent, or parsing fails. Cached via `lru_cache` so repeat
    calls are free.
    """
    path = Path(config_path)
    if not path.exists():
        logger.debug(f"Runtime config: {config_path} not found, using defaults")
        return RuntimeConfig()

    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        block = data.get("runtime") or {}
        return RuntimeConfig(**block)
    except Exception as e:
        logger.warning(f"Runtime config: failed to load {config_path}: {e}; using defaults")
        return RuntimeConfig()
