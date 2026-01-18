import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime
from telebot.infrastructure.telegram import TelethonScraper

class TestTelethonScraper(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api_id = 12345
        self.api_hash = "fake_hash"
        self.session_path = "test.session"
        # We need to patch before init
        with patch("telebot.infrastructure.telegram.TelegramClient"):
            self.scraper = TelethonScraper(self.api_id, self.api_hash, self.session_path)

    async def test_get_messages_flow(self):
        mock_inst = self.scraper.client
        mock_inst.start = AsyncMock()
        mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
        mock_inst.__aexit__ = AsyncMock()
        
        # Mocking the async for message in client.iter_messages
        mock_message = MagicMock()
        mock_message.id = 1
        mock_message.text = "Hello World"
        mock_message.date = datetime.now()
        mock_message.sender = MagicMock(username="testuser")
        # Handle the case where sender might be a Channel or something else
        mock_message.sender.username = "testuser"
        mock_message.media = None
        mock_message.fwd_from = None
        mock_message.reply_to = None
        
        # iter_messages is called as: async for message in self.client.iter_messages(...)
        # So iter_messages(...) must return an async iterator
        mock_inst.iter_messages.return_value.__aiter__.return_value = [mock_message]

        messages = await self.scraper.get_messages("channel", datetime.now())
        
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].text, "Hello World")
        self.assertEqual(messages[0].author, "testuser")

    async def test_get_messages_with_media(self):
        mock_inst = self.scraper.client
        mock_inst.start = AsyncMock()
        mock_inst.__aenter__ = AsyncMock(return_value=mock_inst)
        mock_inst.__aexit__ = AsyncMock()
        
        mock_message = MagicMock()
        mock_message.id = 2
        mock_message.text = "With Media"
        mock_message.date = datetime.now()
        mock_message.media = MagicMock()
        mock_message.fwd_from = None
        mock_message.reply_to = None
        mock_message.sender = MagicMock(username="u")
        # Mock download_media
        mock_message.download_media = AsyncMock(return_value="media_cache/photo.jpg")
        
        mock_inst.iter_messages.return_value.__aiter__.return_value = [mock_message]

        messages = await self.scraper.get_messages("channel", datetime.now())
        
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].local_media_path, "media_cache/photo.jpg")
        mock_message.download_media.assert_called_once()
