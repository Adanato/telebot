from datetime import datetime, timedelta

from telebot.domain.models import ChannelDigest
from telebot.domain.services import ScraperInterface, SummarizerInterface


class GenerateDigestUseCase:
    def __init__(self, scraper: ScraperInterface, summarizer: SummarizerInterface):
        self.scraper = scraper
        self.summarizer = summarizer

    async def execute(self, channel_id: str, topic_id: int | None = None, lookback_days: int = 1) -> ChannelDigest:
        # Default to last 24 hours
        start_date = datetime.now() - timedelta(days=lookback_days)
        messages = await self.scraper.get_messages(channel_id, start_date, topic_id=topic_id)

        if not messages:
            return ChannelDigest(
                channel_name=str(channel_id),
                date=datetime.now().date(),
                summaries=["No messages found in the last 24 hours."],
            )

        digest = await self.summarizer.summarize(messages, topic_id=topic_id)
        return digest
