import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    query,
)

from course_scout.domain.services import AIProvider

logger = logging.getLogger(__name__)


async def _stream_user_turn(content: str | list[dict]) -> AsyncIterator[dict]:
    """Wrap content in the stream-json user-turn envelope expected by
    `claude_agent_sdk.query()`.

    The SDK's `query(prompt=...)` only accepts `str | AsyncIterable[dict]`.
    Passing a bare list of content blocks (e.g. text + image) causes the
    bundled `claude` CLI subprocess to hang forever — stdin is never closed.
    This helper yields exactly one user turn, then the generator ends, which
    signals the SDK to close stdin and let the CLI respond.
    """  # noqa: D205
    yield {
        "type": "user",
        "message": {"role": "user", "content": content},
        "parent_tool_use_id": None,
        "session_id": None,
    }


@dataclass
class UsageStats:
    """Tracks cumulative usage across calls."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0
    total_cost_usd: float = 0.0
    total_duration_ms: int = 0
    call_count: int = 0
    calls: list[dict] = field(default_factory=list)

    def record(self, result: ResultMessage, model: str):
        """Record usage from a ResultMessage."""
        self.call_count += 1
        self.total_duration_ms += result.duration_ms or 0
        self.total_cost_usd += result.total_cost_usd or 0.0

        usage = result.usage or {}
        input_tok = usage.get("input_tokens", 0)
        output_tok = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_create = usage.get("cache_creation_input_tokens", 0)

        self.total_input_tokens += input_tok
        self.total_output_tokens += output_tok
        self.total_cache_read_tokens += cache_read
        self.total_cache_creation_tokens += cache_create

        self.calls.append(
            {
                "model": model,
                "input_tokens": input_tok,
                "output_tokens": output_tok,
                "cache_read": cache_read,
                "duration_ms": result.duration_ms or 0,
                "cost_usd": result.total_cost_usd or 0.0,
            }
        )

    def summary(self) -> str:
        """Return a formatted usage summary with Max plan budget estimate."""
        daily_budget = 5_000_000
        five_hour_budget = daily_budget * (5 / 24)
        daily_pct = (self.total_output_tokens / daily_budget * 100) if daily_budget else 0
        window_pct = (self.total_output_tokens / five_hour_budget * 100) if five_hour_budget else 0

        lines = [
            f"━━━ Usage Summary ({self.call_count} API calls) ━━━",
            f"  Input tokens:  {self.total_input_tokens:,}",
            f"  Output tokens: {self.total_output_tokens:,}",
            f"  Cache read:    {self.total_cache_read_tokens:,}",
            f"  Total time:    {self.total_duration_ms / 1000:.1f}s",
            f"  Est. cost:     ${self.total_cost_usd:.4f}",
            "  ── Max Plan Budget (approx) ──",
            f"  5h window:     ~{window_pct:.1f}% used",
            f"  Daily:         ~{daily_pct:.1f}% used",
        ]
        return "\n".join(lines)


class ClaudeProvider(AIProvider):
    def __init__(self, thinking: str = "adaptive", effort: str = "medium"):
        """Initialize with thinking/effort config. Auth handled by Claude Agent SDK."""
        self.usage = UsageStats()
        self.thinking = thinking
        self.effort = effort
        # Side-channel: thinking text from the most recent call. Bench reads this
        # immediately after agent.run() to capture failure-mode reasoning.
        self.last_thinking: str = ""

    def _thinking_config(self) -> dict:
        """Build thinking config dict for ClaudeAgentOptions."""
        if self.thinking == "enabled":
            return {"type": "enabled", "budget_tokens": 10000}
        if self.thinking == "disabled":
            return {"type": "disabled"}
        return {"type": "adaptive"}

    async def generate_structured(
        self,
        model_id: str,
        system_prompt: str,
        input_data: str,
        output_schema: type,
        media_paths: list[str] | None = None,
    ) -> Any:
        """Generate structured output using Claude Agent SDK.

        If `media_paths` is provided, each path is attached to the user message
        as a base64-encoded image block (native Claude vision). Files >5MB or
        non-image paths are skipped silently.
        """
        schema = output_schema.model_json_schema()

        from course_scout.infrastructure.runtime import get_runtime

        options = ClaudeAgentOptions(
            model=model_id,
            system_prompt=system_prompt,
            # max_turns is read from runtime config (default 5). With structured
            # output, Haiku occasionally needs 3-4 turns before emitting the
            # payload. Unused turns don't cost tokens — generous is fine.
            max_turns=get_runtime().max_turns,
            setting_sources=[],
            allowed_tools=[],
            thinking=cast(Any, self._thinking_config()),
            effort=cast(Literal["low", "medium", "high", "max"], self.effort),
            output_format={"type": "json_schema", "schema": schema},
        )

        # Build prompt: plain string for text-only, AsyncIterable envelope for
        # multi-modal. The SDK treats a bare list as neither str nor
        # AsyncIterable and hangs stdin — always wrap multi-block content.
        prompt: str | AsyncIterator[dict] = input_data
        if media_paths:
            content_blocks = self._build_image_blocks(media_paths)
            if content_blocks:
                combined = [{"type": "text", "text": input_data}, *content_blocks]
                prompt = _stream_user_turn(combined)
                total_bytes = sum(len(b["source"]["data"]) * 3 // 4 for b in content_blocks)
                logger.info(
                    f"[{model_id}] {len(content_blocks)} image(s) attached "
                    f"(~{total_bytes // 1024} KB)"
                )

        structured, tool_output, last_text = await self._collect_messages(prompt, options, model_id)
        return self._parse_output(output_schema, structured, tool_output, last_text)

    @staticmethod
    def _build_image_blocks(media_paths: list[str]) -> list[dict]:
        """Convert local image paths to base64 content blocks. Skips missing,
        oversized, or non-image files.
        """  # noqa: D205
        import base64
        import os

        MAX_BYTES = 5 * 1024 * 1024
        SUPPORTED = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }
        blocks: list[dict] = []
        for path in media_paths:
            if not path or not os.path.exists(path):
                continue
            ext = os.path.splitext(path)[1].lower()
            media_type = SUPPORTED.get(ext)
            if media_type is None:
                continue
            try:
                if os.path.getsize(path) > MAX_BYTES:
                    logger.warning(f"Skipping {path}: >5MB")
                    continue
                with open(path, "rb") as f:
                    data = base64.standard_b64encode(f.read()).decode("ascii")
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": data,
                        },
                    }
                )
            except OSError as e:
                logger.warning(f"Skipping {path}: {e}")
        return blocks

    async def _collect_messages(self, input_data, options, model_id):
        """Iterate SDK messages and extract structured output, tool output, text, thinking."""
        structured = None
        tool_output = None
        last_text = None
        thinking_chunks: list[str] = []

        async for message in query(prompt=input_data, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock) and block.name == "StructuredOutput":
                        tool_output = block.input
                    elif isinstance(block, TextBlock):
                        last_text = block.text
                    elif isinstance(block, ThinkingBlock):
                        thinking_chunks.append(block.thinking)
            elif isinstance(message, ResultMessage):
                if message.is_error:
                    logger.warning(f"ResultMessage error: {message.subtype}")
                self.usage.record(message, model_id)
                self._log_usage(message, model_id)
                if message.structured_output is not None:
                    structured = message.structured_output

        self.last_thinking = "\n\n".join(thinking_chunks)
        return structured, tool_output, last_text

    @staticmethod
    def _log_usage(message, model_id):
        """Log per-call usage stats."""
        u = message.usage or {}
        logger.info(
            f"[{model_id}] {u.get('input_tokens', 0)} in / "
            f"{u.get('output_tokens', 0)} out / "
            f"{u.get('cache_read_input_tokens', 0)} cache / "
            f"{message.duration_ms or 0}ms / "
            f"${message.total_cost_usd or 0:.4f}"
        )

    @staticmethod
    def _parse_output(output_schema, structured, tool_output, last_text):
        """Parse output in priority order: structured > tool > text.

        Defends against the model returning string-encoded JSON for nested fields
        (e.g. `{"items": "[...]"}` instead of `{"items": [...]}`) and against
        trailing garbage / whitespace in JSON outputs.
        """
        for candidate in (structured, tool_output):
            if candidate is None:
                continue
            try:
                return output_schema.model_validate(candidate)
            except Exception as primary_err:
                # Attempt repair: parse any string-encoded JSON fields manually
                if isinstance(candidate, dict):
                    repaired = ClaudeProvider._repair_string_json_fields(candidate)
                    try:
                        return output_schema.model_validate(repaired)
                    except Exception:
                        logger.warning(
                            f"Validation failed even after JSON-string repair: {primary_err}"
                        )
                        continue

        if last_text:
            text = last_text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                text = text.rsplit("```", 1)[0]
            return output_schema.model_validate_json(text.strip())

        raise RuntimeError("No output received from Claude Agent SDK")

    @staticmethod
    def _repair_string_json_fields(data: dict) -> dict:
        r"""If any field value is a JSON-string (list/object), parse it.

        Trims trailing whitespace/junk before parsing — the model occasionally
        emits `"[...]\n  "` which fails strict JSON parse.
        """
        import json

        repaired = {}
        for k, v in data.items():
            if isinstance(v, str):
                stripped = v.strip()
                if stripped.startswith(("[", "{")):
                    # Try strict parse; if fails, try truncating to last balanced bracket
                    try:
                        v = json.loads(stripped)
                    except json.JSONDecodeError:
                        # Find last matching bracket and try again
                        for end in range(len(stripped), 0, -1):
                            try:
                                v = json.loads(stripped[:end])
                                break
                            except json.JSONDecodeError:
                                continue
            repaired[k] = v
        return repaired
