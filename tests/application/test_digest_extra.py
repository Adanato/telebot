import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from telebot.application.digest import GenerateDigestUseCase


class TestDigestUseCaseExtra(unittest.IsolatedAsyncioTestCase):
    async def test_execute_empty_messages(self):
        mock_scraper = AsyncMock()
        mock_summarizer = AsyncMock()
        
        # Scraper returns no messages
        mock_scraper.get_messages.return_value = []
        
        use_case = GenerateDigestUseCase(mock_scraper, mock_summarizer)
        result = await use_case.execute("@test")
        
        self.assertIsNone(result)
        mock_summarizer.summarize.assert_not_called()

    async def test_execute_error_recovery(self):
        mock_scraper = AsyncMock()
        mock_summarizer = AsyncMock()
        
        mock_msg = MagicMock()
        mock_msg.text = "msg"
        mock_scraper.get_messages.return_value = [mock_msg]
        
        # Summarizer fails
        mock_summarizer.summarize.side_effect = Exception("AI Error")
        
        use_case = GenerateDigestUseCase(mock_scraper, mock_summarizer)
        result = await use_case.execute("@test")
        
        # Should still return something (Error Notice digest)
        self.assertIsNotNone(result)
        self.assertEqual(result.channel_name, "Error Notice")
