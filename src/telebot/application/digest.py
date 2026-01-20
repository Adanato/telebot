from datetime import datetime, timedelta

from telebot.domain.models import ChannelDigest
from telebot.domain.services import ScraperInterface, SummarizerInterface


class GenerateDigestUseCase:
    def __init__(self, scraper: ScraperInterface, summarizer: SummarizerInterface):
        """Initialize with scraper and summarizer services."""
        self.scraper = scraper
        self.summarizer = summarizer

    async def execute(
        self, channel_id: str | int, topic_id: int | None = None, lookback_days: int = 1
    ) -> ChannelDigest:
        """Execute the digest generation pipeline."""
        # Default to last 24 hours
        start_date = datetime.now() - timedelta(days=lookback_days)
        messages = await self.scraper.get_messages(channel_id, start_date, topic_id=topic_id)

        if not messages:
            return None

        try:
            digest = await self.summarizer.summarize(messages, topic_id=topic_id)
            return digest
        except Exception as e:
            from telebot.domain.models import ChannelDigest
            import logging
            logging.getLogger(__name__).error(f"Error during summarization: {e}")
            return ChannelDigest(
                channel_name="Error Notice",
                summaries=[f"Summarization Incomplete: {e}"],
                items=[],
                key_links=[],
                action_items=[]
            )
