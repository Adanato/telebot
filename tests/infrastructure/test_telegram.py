import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime
from telebot.infrastructure.telegram import TelethonScraper

class TestTelethonScraper(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api_id = 12345
        self.api_hash = "fake_hash"
        self.session_path = "test.session"
        self.scraper = TelethonScraper(self.api_id, self.api_hash, self.session_path)

    @patch("telebot.infrastructure.telegram.TelegramClient")
    async def test_get_messages_flow(self, MockClient):
        mock_inst = MockClient.return_value
        mock_inst.connect = AsyncMock()
        mock_inst.is_user_authorized = AsyncMock(return_value=True)
        mock_inst.disconnect = AsyncMock()
        
        # Mock iter_messages
        mock_message = MagicMock()
        mock_message.id = 1
        mock_message.text = "Hello World"
        mock_message.date = datetime.now()
        mock_message.sender = MagicMock(username="testuser")
        mock_message.media = None
        mock_message.fwd_from = None
        mock_message.reply_to = None
        
        # iter_messages is an async iterator
        async def mock_iter(*args, **kwargs):
            yield mock_message
            
        mock_inst.iter_messages.return_value.__aiter__.side_effect = lambda: mock_iter()

        messages = await self.scraper.get_messages("channel", datetime.now())
        
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].text, "Hello World")
        self.assertEqual(messages[0].author, "testuser")
        mock_inst.connect.assert_called_once()
        mock_inst.disconnect.assert_called_once()

    @patch("telebot.infrastructure.telegram.TelegramClient")
    @patch("os.path.exists")
    async def test_get_messages_with_image(self, mock_exists, MockClient):
        mock_exists.return_value = False
        mock_inst = MockClient.return_value
        mock_inst.connect = AsyncMock()
        mock_inst.is_user_authorized = AsyncMock(return_value=True)
        mock_inst.disconnect = AsyncMock()
        
        mock_message = MagicMock()
        mock_message.id = 2
        mock_message.text = "With Image"
        mock_message.date = datetime.now()
        mock_message.fwd_from = None
        mock_message.reply_to = None
        mock_message.sender = MagicMock(username="u")
        
        # Mock image media
        mock_message.media = MagicMock()
        mock_message.photo = True
        mock_message.file.ext = ".jpg"
        mock_message.download_media = AsyncMock(return_value="media_cache/media_2.jpg")
        
        async def mock_iter(*args, **kwargs):
            yield mock_message
            
        mock_inst.iter_messages.return_value.__aiter__.side_effect = lambda: mock_iter()

        messages = await self.scraper.get_messages("channel", datetime.now())
        
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].local_media_path, "media_cache/media_2.jpg")
        mock_message.download_media.assert_called_once()

    @patch("telebot.infrastructure.telegram.TelegramClient")
    async def test_get_messages_not_authorized(self, MockClient):
        mock_inst = MockClient.return_value
        mock_inst.connect = AsyncMock()
        mock_inst.is_user_authorized = AsyncMock(return_value=False)
        mock_inst.start = AsyncMock()
        mock_inst.disconnect = AsyncMock()
        
        async def mock_iter(*args, **kwargs):
            if False: yield # Empty iterator
            
        mock_inst.iter_messages.return_value.__aiter__.side_effect = lambda: mock_iter()

        await self.scraper.get_messages("channel", datetime.now())
        
        mock_inst.start.assert_called_once()
