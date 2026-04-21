import logging
import os
import time
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

from pydantic import BaseModel

from course_scout.domain.services import AIProvider

logger = logging.getLogger(__name__)


_PRICING: dict[str, dict[str, float]] = {
    "gpt-5": {"input": 1.25, "cached_input": 0.125, "output": 10.00},
    "gpt-5-mini": {"input": 0.25, "cached_input": 0.025, "output": 2.00},
    "gpt-5-nano": {"input": 0.05, "cached_input": 0.005, "output": 0.40},
    "gpt-5.1": {"input": 1.25, "cached_input": 0.125, "output": 10.00},
    "gpt-5.2": {"input": 1.75, "cached_input": 0.175, "output": 14.00},
    "gpt-5.4": {"input": 2.50, "cached_input": 0.25, "output": 15.00},
    "gpt-5.4-mini": {"input": 0.75, "cached_input": 0.075, "output": 4.50},
    "gpt-5.4-nano": {"input": 0.20, "cached_input": 0.02, "output": 1.25},
    "gpt-4.1": {"input": 2.00, "cached_input": 0.50, "output": 8.00},
    "gpt-4.1-mini": {"input": 0.40, "cached_input": 0.10, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "cached_input": 0.025, "output": 0.40},
}


def _normalize_model_id(model_id: str) -> str:
    if model_id.startswith("openai/"):
        return model_id.split("/", 1)[1]
    return model_id


def _estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> float:
    prices = _PRICING.get(model)
    if not prices:
        return 0.0

    cached = max(cached_input_tokens, 0)
    uncached = max(input_tokens - cached, 0)
    input_cost = (
        uncached * prices["input"] + cached * prices.get("cached_input", prices["input"])
    ) / 1_000_000
    output_cost = output_tokens * prices["output"] / 1_000_000
    return input_cost + output_cost


@dataclass
class OpenAIAgentsUsageStats:
    """Tracks cumulative usage across calls.

    Field names match ClaudeProvider.UsageStats for CLI merge compatibility.
    """

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0
    total_cost_usd: float = 0.0
    total_duration_ms: int = 0
    call_count: int = 0
    calls: list[dict] = field(default_factory=list)

    def record(self, usage: Any, model: str, duration_ms: int):
        self.call_count += 1
        self.total_duration_ms += duration_ms

        input_tok = getattr(usage, "input_tokens", 0) if usage else 0
        output_tok = getattr(usage, "output_tokens", 0) if usage else 0

        input_details = getattr(usage, "input_tokens_details", None) if usage else None
        cache_read = getattr(input_details, "cached_tokens", 0) or 0

        self.total_input_tokens += input_tok
        self.total_output_tokens += output_tok
        self.total_cache_read_tokens += cache_read

        request_entries = getattr(usage, "request_usage_entries", None) if usage else None
        if request_entries:
            cost = sum(
                _estimate_cost(
                    model=model,
                    input_tokens=getattr(entry, "input_tokens", 0),
                    output_tokens=getattr(entry, "output_tokens", 0),
                    cached_input_tokens=(
                        getattr(getattr(entry, "input_tokens_details", None), "cached_tokens", 0)
                        or 0
                    ),
                )
                for entry in request_entries
            )
        else:
            cost = _estimate_cost(model, input_tok, output_tok, cache_read)

        self.total_cost_usd += cost
        self.calls.append({
            "model": model,
            "input_tokens": input_tok,
            "output_tokens": output_tok,
            "cache_read": cache_read,
            "duration_ms": duration_ms,
            "cost_usd": cost,
        })
        logger.info(
            f"[{model}] {input_tok} in / {output_tok} out / "
            f"{cache_read} cache / {duration_ms}ms / ${cost:.4f}"
        )

    def summary(self) -> str:
        lines = [
            f"━━━ Usage Summary ({self.call_count} API calls) ━━━",
            f"  Input tokens:  {self.total_input_tokens:,}",
            f"  Output tokens: {self.total_output_tokens:,}",
            f"  Cache read:    {self.total_cache_read_tokens:,}",
            f"  Total time:    {self.total_duration_ms / 1000:.1f}s",
            f"  Est. cost:     ${self.total_cost_usd:.4f}",
        ]
        return "\n".join(lines)


class OpenAIAgentsProvider(AIProvider):
    """Benchmark-only provider backed by the OpenAI Agents SDK."""

    def __init__(self, api_key: str | None = None, effort: str = "medium"):
        """Initialize without importing or authenticating the SDK eagerly."""
        self.api_key = api_key
        self.effort = effort
        self.usage = OpenAIAgentsUsageStats()
        self.last_thinking: str = ""

    @staticmethod
    def _load_agents_sdk():
        try:
            return import_module("agents")
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "openai-agents is not installed. Install dev dependencies to benchmark GPT models."
            ) from exc

    def _resolve_api_key(self) -> str:
        api_key = self.api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is required to run benchmark models via the OpenAI Agents SDK."
            )
        return api_key

    def _build_model_settings(self, agents_sdk: Any, model: str):
        if not model.startswith("gpt-5"):
            return None

        from openai.types.shared import Reasoning

        return agents_sdk.ModelSettings(
            reasoning=Reasoning(effort=self.effort),
            verbosity="low",
        )

    async def generate_structured(
        self, model_id: str, system_prompt: str, input_data: str, output_schema: type
    ) -> BaseModel:
        """Generate structured output using the OpenAI Agents SDK."""
        self.last_thinking = ""

        agents_sdk = self._load_agents_sdk()
        api_key = self._resolve_api_key()
        normalized_model = _normalize_model_id(model_id)

        agents_sdk.set_default_openai_key(api_key, use_for_tracing=False)

        agent = agents_sdk.Agent(
            name="Course Scout Benchmark Summarizer",
            instructions=system_prompt,
            model=normalized_model,
            model_settings=self._build_model_settings(agents_sdk, normalized_model),
            output_type=output_schema,
        )
        run_config = agents_sdk.RunConfig(
            tracing_disabled=True,
            workflow_name="course-scout benchmark",
        )

        start = time.monotonic()
        result = await agents_sdk.Runner.run(
            agent,
            input=input_data,
            run_config=run_config,
        )
        duration_ms = int((time.monotonic() - start) * 1000)

        usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
        self.usage.record(usage, normalized_model, duration_ms)

        final_output = getattr(result, "final_output", None)
        if final_output is None:
            raise RuntimeError("No output received from OpenAI Agents SDK")

        if isinstance(final_output, str):
            return output_schema.model_validate_json(final_output)
        return output_schema.model_validate(final_output)
