import datetime

from pydantic import BaseModel, Field


class TelegramMessage(BaseModel):
    id: int
    text: str | None  # Text can be empty if it's just media
    date: datetime.datetime
    author: str | None = None
    link: str
    reply_to_id: int | None = None
    forward_from_chat: str | None = None
    forward_from_author: str | None = None
    local_media_path: str | None = None


class ChannelDigest(BaseModel):
    channel_name: str
    date: datetime.date
    summaries: list[str]
    action_items: list[str] = Field(default_factory=list)
    key_links: list[str] = Field(default_factory=list)
