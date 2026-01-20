from abc import ABC, abstractmethod
from datetime import datetime

from telebot.domain.models import ChannelDigest, TelegramMessage


class ScraperInterface(ABC):
    @abstractmethod
    async def get_messages(
        self, channel_id: str | int, start_date: datetime, topic_id: int | None = None
    ) -> list[TelegramMessage]:
        pass

    @abstractmethod
    async def get_message_by_id(self, channel_id: str | int, message_id: int, topic_id: int | None = None) -> TelegramMessage | None:
        """Fetch a specific message by ID and verify it exists."""
        pass

    @abstractmethod
    async def search_messages(self, channel_id: str | int, query: str, topic_id: int | None = None, limit: int = 5) -> list[TelegramMessage]:
        """Search for messages containing the given query."""
        pass

    @abstractmethod
    async def list_topics(self, channel_id: str | int) -> list[dict]:
        """List forum topics for a given channel."""
        pass


class SummarizerInterface(ABC):
    @abstractmethod
    async def summarize(
        self, messages: list[TelegramMessage], topic_id: int | None = None
    ) -> ChannelDigest:
        pass


class AIProvider(ABC):
    @abstractmethod
    def generate_structured(
        self, 
        model_id: str, 
        system_prompt: str, 
        input_data: str, 
        output_schema: type
    ) -> any:
        """Generate structured output using the provider's SDK."""
        pass
