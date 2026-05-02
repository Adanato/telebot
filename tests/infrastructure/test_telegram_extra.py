"""Extra TelethonScraper tests: list_topics, search_messages."""

from __future__ import annotations

import datetime
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from course_scout.infrastructure.telegram import TelethonScraper


class TestTelethonScraperExtra(unittest.IsolatedAsyncioTestCase):
    @patch("course_scout.infrastructure.telegram.TelegramClient")
    async def test_list_topics_error_returns_empty(self, MockClient):
        """If list_topics raises during the API call, propagate (caller decides)."""
        mock_inst = MockClient.return_value
        mock_inst.connect = AsyncMock()
        mock_inst.is_user_authorized = AsyncMock(return_value=True)
        mock_inst.disconnect = AsyncMock()

        # The functions.messages.GetForumTopicsRequest call is via __call__ on
        # the client itself, which is awaited.
        class _Denied(RuntimeError):
            pass

        mock_inst.side_effect = _Denied("Request Denied")

        scraper = TelethonScraper(1, "h", "s")
        with self.assertRaises(_Denied):
            await scraper.list_topics("@testchannel")

        mock_inst.disconnect.assert_called_once()

    @patch("course_scout.infrastructure.telegram.TelegramClient")
    async def test_search_messages_yields_results(self, MockClient):
        """search_messages should iterate Telethon results and convert each."""
        mock_inst = MockClient.return_value
        mock_inst.connect = AsyncMock()
        mock_inst.is_user_authorized = AsyncMock(return_value=True)
        mock_inst.disconnect = AsyncMock()

        msg = MagicMock()
        msg.id = 1
        msg.text = "found"
        msg.date = datetime.datetime.now()
        msg.sender = MagicMock(username="user1")
        msg.fwd_from = None
        msg.reply_to = None
        msg.replies = None
        msg.peer_id = MagicMock(channel_id=1234)
        msg.media = None
        msg.photo = False
        msg.document = None

        async def _iter(*args, **kwargs):
            yield msg

        mock_inst.iter_messages.return_value.__aiter__.side_effect = lambda: _iter()

        scraper = TelethonScraper(1, "h", "s")
        results = await scraper.search_messages("@test", "query")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "found")

    @patch("course_scout.infrastructure.telegram.TelegramClient")
    async def test_list_topics_success(self, MockClient):
        """list_topics returns [{id, title}, ...] from Telethon API result."""
        # Custom mock instance whose __call__ behaves async (Telethon's pattern).
        topic_a = MagicMock(id=1, title="2D Lounge")
        topic_b = MagicMock(id=2, title="Asian Artists")

        mock_inst = MagicMock()
        mock_inst.connect = AsyncMock()
        mock_inst.is_user_authorized = AsyncMock(return_value=True)
        mock_inst.disconnect = AsyncMock()
        # `await client(...)` for the GetForumTopicsRequest — make __call__ async
        async_client_call = AsyncMock(return_value=MagicMock(topics=[topic_a, topic_b]))
        mock_inst.side_effect = async_client_call
        MockClient.return_value = mock_inst

        scraper = TelethonScraper(1, "h", "s")
        result = await scraper.list_topics("@channel")
        self.assertEqual(
            result,
            [{"id": 1, "title": "2D Lounge"}, {"id": 2, "title": "Asian Artists"}],
        )
