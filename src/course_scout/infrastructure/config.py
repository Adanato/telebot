import os
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

# Shared base prepended to every per-channel system prompt.
# Documents output schema, gives 5 worked input/output examples spanning the
# main category boundaries, and reinforces output-shape constraints that have
# caused JSON validation failures (string-encoded lists, trailing data, etc.).
BASE_OUTPUT_GUIDANCE = """\
You extract structured digest items from art community Telegram messages.

OUTPUT SHAPE (CRITICAL — failure to follow these breaks the pipeline):
- Return items as a real JSON LIST, not a string-encoded list.
  WRONG: {"items": "[{...}, {...}]"}
  RIGHT: {"items": [{...}, {...}]}
- No trailing text, comments, or whitespace after the JSON.
- Every item MUST include msg_ids (list of int message IDs from the input).
- Translate non-English titles to English; keep originals in parentheses.
- Description: 1-3 short telegraphic lines. No narrative filler ("A user asked...").

═══════════════════════════════════════════════════════════════════
EXAMPLES — input messages → expected output
═══════════════════════════════════════════════════════════════════

EXAMPLE 1 — Fulfilled request (download link shared in reply)
Input:
  #1234: anyone got the new Krenz color course?
  #1235: yeah here https://mega.nz/folder/abc#xyz123 password: krenz2026
  #1236: thanks!
Output:
{"items": [{
  "title": "Krenz Color Course",
  "description": "Fulfilled — Mega link shared with password.",
  "category": "request",
  "status": "FULFILLED",
  "instructor": "Krenz",
  "platform": "Mega",
  "password": "krenz2026",
  "msg_ids": [1234, 1235]
}], "key_links": []}

EXAMPLE 2 — Unfulfilled request (only a storefront link, no real download)
Input:
  #2001: looking for Sangsoo Jeong character illustration coloso course
  #2002: it's here https://coloso.global/products/sangsoo-jeong
  #2003: no man, the actual files
Output:
{"items": [{
  "title": "Sangsoo Jeong Character Illustration",
  "description": "Storefront link only, no download shared.",
  "category": "request",
  "status": "UNFULFILLED",
  "instructor": "Sangsoo Jeong",
  "platform": "Coloso",
  "msg_ids": [2001, 2002, 2003]
}], "key_links": []}

EXAMPLE 3 — File share (someone drops a download with no prior request)
Input:
  #3010: dropping the WLOP painting tutorials 2025
  #3011: https://mega.nz/folder/wlop2025 pwd: wlop123
  #3012: subs in EN/ZH included, brushes in folder
Output:
{"items": [{
  "title": "WLOP Painting Tutorials 2025",
  "description": "EN+ZH subs, brushes included.",
  "category": "file",
  "status": "FULFILLED",
  "instructor": "WLOP",
  "platform": "Mega",
  "password": "wlop123",
  "msg_ids": [3010, 3011, 3012]
}], "key_links": []}

EXAMPLE 4 — Technique discussion (no link, just craft talk)
Input:
  #4050: how do you all keep grayscale values consistent?
  #4051: add an Ambient Occlusion layer at low opacity, if it reads as 3D the values work
  #4052: oh that's clever, gonna try
Output:
{"items": [{
  "title": "Grayscale Value Validation via AO Layer",
  "description": "Add Ambient Occlusion overlay at low opacity. If image reads as 3D, value structure is correct.",
  "category": "discussion",
  "msg_ids": [4050, 4051, 4052]
}], "key_links": []}

EXAMPLE 5 — Course review with verdict (recommendation, not a download)
Input:
  #5100: just finished the Hide anatomy course
  #5101: way better than Tenten for bone structure, slower pacing helps for beginners
  #5102: agreed, his ribcage section is gold
Output:
{"items": [{
  "title": "Hide Anatomy Course",
  "description": "Better than Tenten for bone structure. Slow pacing suits beginners. Standout: ribcage section.",
  "category": "course",
  "status": "DISCUSSING",
  "instructor": "Hide",
  "msg_ids": [5100, 5101, 5102]
}], "key_links": []}

EXAMPLE 6 — SKIP (no extractable item)
Input:
  #6001: gm everyone
  #6002: 🔥🔥🔥
  #6003: anyone else hyped for friday
Output:
{"items": [], "key_links": []}

EXAMPLE 7 — INTERLEAVED requests (two independent threads, replies arrive out of order)
Input:
  #7001: anyone got the krenz color course?
  #7002: looking for sangsoo jeong character course too
  #7003: krenz mega: https://mega.nz/krenz#abc
  #7004: thanks!
  #7005: bump for sangsoo, anyone?
  #7006: sangsoo not yet, still searching
Output:
{"items": [
  {
    "title": "Krenz Color Course",
    "description": "Mega link shared.",
    "category": "request",
    "status": "FULFILLED",
    "instructor": "Krenz",
    "platform": "Mega",
    "msg_ids": [7001, 7003, 7004]
  },
  {
    "title": "Sangsoo Jeong Character Course",
    "description": "Multiple users searching, no link found.",
    "category": "request",
    "status": "UNFULFILLED",
    "instructor": "Sangsoo Jeong",
    "msg_ids": [7002, 7005, 7006]
  }
], "key_links": []}

EXAMPLE 8 — MULTI-THREAD discussion (one channel, several distinct topics in flight)
Input:
  #8001: what brushes do you all use for skin?
  #8002: sai default round + soft eraser, no fancy brushes needed
  #8003: anyone got the new Yelomelomelo rendering course?
  #8004: groupbuy organizing — see the gb topic
  #8005: how about hair brushes
  #8006: csp with custom comb brushes is the move for hair
  #8007: 👀
Output:
{"items": [
  {
    "title": "Skin Painting — Brush Selection",
    "description": "Default Sai round brush + soft eraser. No fancy brushes needed.",
    "category": "discussion",
    "msg_ids": [8001, 8002]
  },
  {
    "title": "Yelomelomelo Rendering Course",
    "description": "Group buy organizing in GB topic.",
    "category": "request",
    "status": "DISCUSSING",
    "instructor": "Yelomelomelo",
    "msg_ids": [8003, 8004]
  },
  {
    "title": "Hair Painting — Brush Selection",
    "description": "Clip Studio Paint with custom comb brushes recommended.",
    "category": "discussion",
    "msg_ids": [8005, 8006]
  }
], "key_links": []}

EXAMPLE 9 — REPLY-TO across a gap (msg #200 replies to old msg #100; group as one item)
Input:
  #9100: #request need spine pro crack, can't find anywhere
  #9101: <unrelated msg about coloring>
  #9102: <unrelated msg about anatomy>
  #9105: any updates on spine pro?
  #9106: still looking, nobody has it
Output:
{"items": [
  {
    "title": "Spine Pro Crack",
    "description": "Multiple users searching, no resolution after several days.",
    "category": "request",
    "status": "UNFULFILLED",
    "platform": "Spine",
    "msg_ids": [9100, 9105, 9106]
  }
], "key_links": []}

EXAMPLE 10 — BROKEN thread (request stalls into discussion about why it's hard to find)
Input:
  #10001: anyone got the riggingpro Maya quickstart?
  #10002: that one's been requested for months, mods would have to buy it
  #10003: yeah it's a $200 course, no one's dropping the files
  #10004: maybe we can pool money for a groupbuy?
Output:
{"items": [
  {
    "title": "Rigging Pro Maya Quickstart",
    "description": "$200 course. Long-running unfulfilled request. Group buy proposed but not organized.",
    "category": "request",
    "status": "DISCUSSING",
    "platform": "RiggingPro",
    "msg_ids": [10001, 10002, 10003, 10004]
  }
], "key_links": []}

═══════════════════════════════════════════════════════════════════
CHANNEL-SPECIFIC RULES (override the examples above where they conflict):
═══════════════════════════════════════════════════════════════════
"""  # noqa: E501


class TaskConfig(BaseModel):
    """Per-topic agent configuration."""

    name: str
    channel_id: str | int
    topic_id: int | None = None
    actions: list[str] = ["summarize"]

    # Agent overrides (inherit from global defaults if not set)
    summarizer_model: str | None = None
    system_prompt: str | None = None
    system_prompt_name: str | None = None  # populated during config load
    chunk_size: int | None = None
    max_messages: int | None = None
    thinking: Literal["enabled", "disabled", "adaptive"] | None = None
    effort: Literal["low", "medium", "high", "max"] | None = None
    include_media: bool | None = None  # None = inherit default (False for request channels)

    def resolve(self, defaults: "AgentDefaults") -> "ResolvedTaskConfig":
        """Merge task-level overrides with global defaults."""
        return ResolvedTaskConfig(
            name=self.name,
            channel_id=self.channel_id,
            topic_id=self.topic_id,
            actions=self.actions,
            summarizer_model=self.summarizer_model or defaults.summarizer_model,
            system_prompt=self.system_prompt,
            system_prompt_name=self.system_prompt_name,
            chunk_size=self.chunk_size or defaults.chunk_size,
            max_messages=self.max_messages or defaults.max_messages,
            thinking=self.thinking or defaults.thinking,
            effort=self.effort or defaults.effort,
            include_media=(
                self.include_media if self.include_media is not None else defaults.include_media
            ),
        )


class ResolvedTaskConfig(BaseModel):
    """Fully resolved task config with no None values."""

    name: str
    channel_id: str | int
    topic_id: int | None = None
    actions: list[str] = ["summarize"]
    summarizer_model: str = "claude-sonnet-4-6"
    system_prompt: str | None = None
    system_prompt_name: str | None = None  # original YAML prompt key (for category allowlist)
    chunk_size: int = 25
    max_messages: int = 100
    thinking: str = "adaptive"
    effort: str = "medium"
    include_media: bool = False  # set True to pass image attachments into the parser call


class AgentDefaults(BaseModel):
    """Global defaults for all topic agents."""

    summarizer_model: str = "claude-sonnet-4-6"
    chunk_size: int = 25
    max_messages: int = 100
    thinking: str = "adaptive"
    effort: str = "medium"
    include_media: bool = False


class Settings(BaseSettings):
    tg_api_id: int = Field(alias="TG_API_ID")
    tg_api_hash: str = Field(alias="TG_API_HASH")
    telegram_bot_token: str | None = Field(None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(None, alias="TELEGRAM_CHAT_ID")
    tg_notify_target: str | None = Field(None, alias="TG_NOTIFY_TARGET")
    phone_number: str | None = Field(None, alias="PHONE_NUMBER")
    login_code: str | None = None
    session_path: str = "course_scout.session"

    # Global YAML overrides
    lookback_days: int = 1
    report_format: str = "pdf"
    timezone: str = "America/New_York"
    window_mode: str = "rolling"

    # Agent defaults (overridable per task)
    agent_defaults: AgentDefaults = AgentDefaults()

    # Raw tasks from YAML (parsed into TaskConfig objects by load_settings)
    tasks: list[dict[str, Any]] = []
    resolved_tasks: list[ResolvedTaskConfig] = []

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "populate_by_name": True,
    }


def load_settings(config_path: str = "config.yaml") -> Settings:
    # pydantic-settings reads required fields from env; pyright can't infer this.
    settings = Settings()  # type: ignore[call-arg]

    if os.path.exists(config_path):
        with open(config_path) as f:
            config_data = yaml.safe_load(f) or {}

            # Load global overrides
            global_cfg = config_data.get("global", {})
            for key, value in global_cfg.items():
                if hasattr(settings, key):
                    setattr(settings, key, value)

            # Load agent defaults
            defaults_cfg = config_data.get("agent_defaults", {})
            if defaults_cfg:
                settings.agent_defaults = AgentDefaults(**defaults_cfg)

            # Load prompt templates
            prompts = config_data.get("prompts", {})

            # Load and resolve tasks (resolve prompt references)
            raw_tasks = config_data.get("tasks", [])
            settings.tasks = raw_tasks
            resolved = []
            for t in raw_tasks:
                task = TaskConfig(**t)
                # Preserve the original prompt key (e.g. "course_requests") for
                # downstream category-allowlist enforcement.
                if task.system_prompt and task.system_prompt in prompts:
                    task.system_prompt_name = task.system_prompt
                    task.system_prompt = prompts[task.system_prompt]
                # Auto-enable media for non-request channels. Request channels
                # are text-only (asks rarely carry useful images). Explicit
                # per-task include_media still wins.
                if task.include_media is None:
                    task.include_media = task.system_prompt_name != "course_requests"
                # Prepend shared base (output schema + few-shot examples)
                if task.system_prompt:
                    task.system_prompt = BASE_OUTPUT_GUIDANCE + task.system_prompt
                resolved.append(task.resolve(settings.agent_defaults))
            settings.resolved_tasks = resolved

    return settings
