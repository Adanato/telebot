from abc import ABC, abstractmethod
from datetime import datetime

from telebot.domain.models import ChannelDigest, TelegramMessage


class ScraperInterface(ABC):
    @abstractmethod
    async def get_messages(
        self, channel_id: str, start_date: datetime, topic_id: int | None = None
    ) -> list[TelegramMessage]:
        pass


class SummarizerInterface(ABC):
    @abstractmethod
    async def summarize(
        self, messages: list[TelegramMessage], topic_id: int | None = None
    ) -> ChannelDigest:
        pass
