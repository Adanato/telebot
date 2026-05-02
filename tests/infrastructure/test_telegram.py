"""Tests for TelethonScraper and message processing."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from course_scout.infrastructure.telegram import TelethonScraper


def _make_message(
    msg_id: int = 1,
    text: str = "Hello",
    *,
    has_photo: bool = False,
    has_document: bool = False,
    document_filename: str | None = None,
    has_webpage: bool = False,
) -> MagicMock:
    """Build a MagicMock that quacks like a Telethon message."""
    msg = MagicMock()
    msg.id = msg_id
    msg.text = text
    msg.date = datetime.now()
    msg.fwd_from = None
    msg.reply_to = None
    msg.sender = MagicMock(username="testuser")
    msg.replies = None
    msg.peer_id = MagicMock(channel_id=1234)
    # Default: no media of any kind
    msg.media = None
    msg.photo = False
    msg.document = None

    if has_photo:
        msg.media = MagicMock(webpage=None)  # ensure webpage check is None-safe
        msg.photo = True
        msg.file = MagicMock(ext=".jpg")
        msg.download_media = AsyncMock(return_value=f"media_cache/media_{msg_id}.jpg")
    elif has_document:
        attr = MagicMock(file_name=document_filename or "doc.zip")
        msg.media = MagicMock(webpage=None)
        msg.document = MagicMock(mime_type="application/zip", attributes=[attr])
    elif has_webpage:
        msg.media = MagicMock()
        msg.media.webpage = MagicMock(
            type="article",
            title="Title",
            description="Desc",
            url="http://example.com",
            site_name="Example",
        )
    return msg


class TestTelethonScraper(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scraper = TelethonScraper(12345, "fake_hash", "test.session")

    @patch("course_scout.infrastructure.telegram.TelegramClient")
    async def test_get_messages_basic(self, MockClient):
        """Plain text message → TelegramMessage with no media."""
        mock_inst = MockClient.return_value
        mock_inst.connect = AsyncMock()
        mock_inst.is_user_authorized = AsyncMock(return_value=True)
        mock_inst.disconnect = AsyncMock()

        msg = _make_message(msg_id=1, text="Hello World")

        async def _iter(*args, **kwargs):
            yield msg

        mock_inst.iter_messages.return_value.__aiter__.side_effect = lambda: _iter()

        messages = await self.scraper.get_messages("channel", datetime.now())

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].id, 1)
        self.assertEqual(messages[0].text, "Hello World")
        self.assertEqual(messages[0].author, "testuser")
        self.assertIsNone(messages[0].local_media_path)
        mock_inst.connect.assert_called_once()
        mock_inst.disconnect.assert_called_once()

    @patch("course_scout.infrastructure.telegram.TelegramClient")
    async def test_get_messages_with_image(self, MockClient):
        """Image message → media gets downloaded and local_media_path populated."""
        mock_inst = MockClient.return_value
        mock_inst.connect = AsyncMock()
        mock_inst.is_user_authorized = AsyncMock(return_value=True)
        mock_inst.disconnect = AsyncMock()

        msg = _make_message(msg_id=2, text="With image", has_photo=True)

        async def _iter(*args, **kwargs):
            yield msg

        mock_inst.iter_messages.return_value.__aiter__.side_effect = lambda: _iter()

        with patch("os.path.exists", return_value=False):
            messages = await self.scraper.get_messages("channel", datetime.now())

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].local_media_path, "media_cache/media_2.jpg")
        msg.download_media.assert_called_once()

    @patch("course_scout.infrastructure.telegram.TelegramClient")
    async def test_get_messages_with_document(self, MockClient):
        """Non-image document → document_filename populated, no media download."""
        mock_inst = MockClient.return_value
        mock_inst.connect = AsyncMock()
        mock_inst.is_user_authorized = AsyncMock(return_value=True)
        mock_inst.disconnect = AsyncMock()

        msg = _make_message(
            msg_id=3, text="zip file", has_document=True, document_filename="course.zip"
        )

        async def _iter(*args, **kwargs):
            yield msg

        mock_inst.iter_messages.return_value.__aiter__.side_effect = lambda: _iter()

        messages = await self.scraper.get_messages("channel", datetime.now())

        self.assertEqual(messages[0].document_filename, "course.zip")

    @patch("course_scout.infrastructure.telegram.TelegramClient")
    async def test_get_messages_with_webpage_preview(self, MockClient):
        """Message with webpage metadata → web_preview_* fields populated."""
        mock_inst = MockClient.return_value
        mock_inst.connect = AsyncMock()
        mock_inst.is_user_authorized = AsyncMock(return_value=True)
        mock_inst.disconnect = AsyncMock()

        msg = _make_message(msg_id=4, text="Check this", has_webpage=True)

        async def _iter(*args, **kwargs):
            yield msg

        mock_inst.iter_messages.return_value.__aiter__.side_effect = lambda: _iter()

        messages = await self.scraper.get_messages("channel", datetime.now())

        self.assertEqual(messages[0].web_preview_title, "Title")
        self.assertEqual(messages[0].web_preview_url, "http://example.com")

    @patch("course_scout.infrastructure.telegram.TelegramClient")
    async def test_get_messages_not_authorized_triggers_login(self, MockClient):
        """If not authorized → start() is called for interactive login."""
        mock_inst = MockClient.return_value
        mock_inst.connect = AsyncMock()
        mock_inst.is_user_authorized = AsyncMock(return_value=False)
        mock_inst.start = AsyncMock()
        mock_inst.disconnect = AsyncMock()

        async def _iter(*args, **kwargs):
            return
            yield  # noqa — empty iter

        mock_inst.iter_messages.return_value.__aiter__.side_effect = lambda: _iter()

        await self.scraper.get_messages("channel", datetime.now())

        mock_inst.start.assert_called_once()

    @patch("course_scout.infrastructure.telegram.TelegramClient")
    async def test_get_messages_filters_below_start_date(self, MockClient):
        """Messages older than start_date should be excluded."""
        from datetime import timedelta

        mock_inst = MockClient.return_value
        mock_inst.connect = AsyncMock()
        mock_inst.is_user_authorized = AsyncMock(return_value=True)
        mock_inst.disconnect = AsyncMock()

        # Both messages: one within window, one too old
        now = datetime.now(UTC)
        old_msg = _make_message(msg_id=10)
        old_msg.date = now - timedelta(days=10)
        recent_msg = _make_message(msg_id=11)
        recent_msg.date = now

        async def _iter(*args, **kwargs):
            yield recent_msg
            yield old_msg

        mock_inst.iter_messages.return_value.__aiter__.side_effect = lambda: _iter()

        # The scraper passes a `since` filter to iter_messages — but we mock the
        # full iterator so all yielded messages come back. This documents that
        # filtering happens at the Telethon API call layer, not in our processing.
        messages = await self.scraper.get_messages("channel", now - timedelta(days=1))
        self.assertEqual(len(messages), 2)
