import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import datetime

from telebot.infrastructure.telegram import TelethonScraper


class TestTelethonScraperExtra(unittest.IsolatedAsyncioTestCase):
    async def test_list_topics_error(self):
        with patch("telebot.infrastructure.telegram.TelegramClient"):
            scraper = TelethonScraper(1, "h", "s")
            
            # Create a mock that is both an async context manager and a callable coroutine
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            
            # This is the key: Mock the result of the call to return a coroutine that raises
            mock_client.side_effect = Exception("Request Denied")
            
            scraper.client = mock_client
            topics = await scraper.list_topics("@testchannel")
            self.assertEqual(topics, [])

    async def test_search_messages_iterator(self):
        with patch("telebot.infrastructure.telegram.TelegramClient"):
            scraper = TelethonScraper(1, "h", "s")
            
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            
            mock_msg = MagicMock()
            mock_msg.id = 1
            mock_msg.text = "found"
            mock_msg.date = datetime.datetime.now()
            mock_msg.sender = MagicMock()
            mock_msg.sender.first_name = "User"

            # Mock for iter_messages (async iterator)
            async def mock_iter(*args, **kwargs):
                yield mock_msg
                
            mock_client.iter_messages.side_effect = mock_iter
            scraper.client = mock_client

            results = await scraper.search_messages("@test", "query")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].text, "found")
