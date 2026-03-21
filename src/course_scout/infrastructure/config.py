import os
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class TaskConfig(BaseModel):
    """Per-topic agent configuration."""

    name: str
    channel_id: str | int
    topic_id: int | None = None
    actions: list[str] = ["summarize"]

    # Agent overrides (inherit from global defaults if not set)
    summarizer_model: str | None = None
    system_prompt: str | None = None
    chunk_size: int | None = None
    max_messages: int | None = None
    thinking: Literal["enabled", "disabled", "adaptive"] | None = None
    effort: Literal["low", "medium", "high", "max"] | None = None

    def resolve(self, defaults: "AgentDefaults") -> "ResolvedTaskConfig":
        """Merge task-level overrides with global defaults."""
        return ResolvedTaskConfig(
            name=self.name,
            channel_id=self.channel_id,
            topic_id=self.topic_id,
            actions=self.actions,
            summarizer_model=self.summarizer_model or defaults.summarizer_model,
            system_prompt=self.system_prompt,
            chunk_size=self.chunk_size or defaults.chunk_size,
            max_messages=self.max_messages or defaults.max_messages,
            thinking=self.thinking or defaults.thinking,
            effort=self.effort or defaults.effort,
        )


class ResolvedTaskConfig(BaseModel):
    """Fully resolved task config with no None values."""

    name: str
    channel_id: str | int
    topic_id: int | None = None
    actions: list[str] = ["summarize"]
    summarizer_model: str = "claude-sonnet-4-6"
    system_prompt: str | None = None
    chunk_size: int = 25
    max_messages: int = 100
    thinking: str = "adaptive"
    effort: str = "medium"


class AgentDefaults(BaseModel):
    """Global defaults for all topic agents."""

    summarizer_model: str = "claude-sonnet-4-6"
    chunk_size: int = 25
    max_messages: int = 100
    thinking: str = "adaptive"
    effort: str = "medium"


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
    settings = Settings()

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
                # Resolve prompt reference: "course_requests" → full prompt text
                if task.system_prompt and task.system_prompt in prompts:
                    task.system_prompt = prompts[task.system_prompt]
                resolved.append(task.resolve(settings.agent_defaults))
            settings.resolved_tasks = resolved

    return settings
