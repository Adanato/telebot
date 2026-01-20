import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from telebot.application.digest import GenerateDigestUseCase
from telebot.domain.models import ChannelDigest, TelegramMessage


class TestGenerateDigestUseCase(unittest.IsolatedAsyncioTestCase):
    async def test_execute_success(self):
        # Mock dependencies
        mock_scraper = MagicMock()
        mock_scraper.get_messages = AsyncMock()

        mock_summarizer = MagicMock()
        mock_summarizer.summarize = AsyncMock()

        # Setup return values
        messages = [TelegramMessage(id=1, text="msg", date=datetime.now(), link="link")]
        mock_scraper.get_messages.return_value = messages

        expected_digest = ChannelDigest(
            channel_name="Test", date=datetime.now().date(), summaries=["Summary"]
        )
        mock_summarizer.summarize.return_value = expected_digest

        # Execute use case
        use_case = GenerateDigestUseCase(mock_scraper, mock_summarizer)
        result = await use_case.execute(channel_id="123", topic_id=456)

        # Verify interactions
        mock_scraper.get_messages.assert_called_once()
        mock_summarizer.summarize.assert_called_once_with(messages, topic_id=456)
        self.assertEqual(result, expected_digest)

    async def test_execute_no_messages(self):
        # Mock dependencies
        mock_scraper = MagicMock()
        mock_scraper.get_messages = AsyncMock()
        mock_scraper.get_messages.return_value = []  # Empty list

        mock_summarizer = MagicMock()
        mock_summarizer.summarize = AsyncMock()

        # Execute use case
        use_case = GenerateDigestUseCase(mock_scraper, mock_summarizer)
        result = await use_case.execute(channel_id="123")

        # Verify scraper called but summarizer NOT called
        mock_scraper.get_messages.assert_called_once()
        mock_summarizer.summarize.assert_not_called()

        # Verify fallback result
        self.assertIsNone(result)
