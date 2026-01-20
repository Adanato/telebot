import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import os

from telebot.infrastructure.notifier import TelethonNotifier


class TestTelethonNotifier(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api_id = 123
        self.api_hash = "hash"
        self.session_path = "test.session"
        self.default_peer = "test_peer"
        self.notifier = TelethonNotifier(
            self.api_id, self.api_hash, self.session_path, self.default_peer
        )

    @patch("telebot.infrastructure.notifier.TelegramClient")
    async def test_send_message_success(self, MockClient):
        mock_client_inst = MockClient.return_value
        mock_client_inst.connect = AsyncMock()
        mock_client_inst.disconnect = AsyncMock()
        mock_client_inst.send_message = AsyncMock()

        success = await self.notifier.send_message("hello", chat_id="target")

        self.assertTrue(success)
        mock_client_inst.send_message.assert_called_once_with("target", "hello")
        mock_client_inst.connect.assert_called_once()
        mock_client_inst.disconnect.assert_called_once()

    @patch("telebot.infrastructure.notifier.TelegramClient")
    async def test_send_message_default_peer(self, MockClient):
        mock_client_inst = MockClient.return_value
        mock_client_inst.connect = AsyncMock()
        mock_client_inst.disconnect = AsyncMock()
        mock_client_inst.send_message = AsyncMock()

        success = await self.notifier.send_message("hello")

        self.assertTrue(success)
        mock_client_inst.send_message.assert_called_once_with("test_peer", "hello")

    @patch("telebot.infrastructure.notifier.TelegramClient")
    async def test_send_message_no_peer_fails(self, MockClient):
        notifier_no_peer = TelethonNotifier(self.api_id, self.api_hash, self.session_path)
        success = await notifier_no_peer.send_message("hello")
        self.assertFalse(success)

    @patch("telebot.infrastructure.notifier.TelegramClient")
    async def test_send_document_success(self, MockClient):
        mock_client_inst = MockClient.return_value
        mock_client_inst.connect = AsyncMock()
        mock_client_inst.disconnect = AsyncMock()
        mock_client_inst.send_file = AsyncMock()

        with patch("os.path.exists", return_value=True):
            success = await self.notifier.send_document("path/to/file", caption="cap")

        self.assertTrue(success)
        mock_client_inst.send_file.assert_called_once_with("test_peer", "path/to/file", caption="cap")

    @patch("telebot.infrastructure.notifier.TelegramClient")
    async def test_send_document_file_not_found(self, MockClient):
        mock_client_inst = MockClient.return_value
        mock_client_inst.connect = AsyncMock()
        mock_client_inst.disconnect = AsyncMock()

        with patch("os.path.exists", return_value=False):
            success = await self.notifier.send_document("missing.pdf")

        self.assertFalse(success)
        mock_client_inst.send_file.assert_not_called()

    @patch("telebot.infrastructure.notifier.TelegramClient")
    async def test_send_error_handling(self, MockClient):
        mock_client_inst = MockClient.return_value
        mock_client_inst.connect = AsyncMock()
        mock_client_inst.disconnect = AsyncMock()
        # Mock failure during action
        mock_client_inst.send_message.side_effect = Exception("Network Down")

        success = await self.notifier.send_message("hello")

        self.assertFalse(success)
        mock_client_inst.disconnect.assert_called_once()
