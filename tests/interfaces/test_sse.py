import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import datetime

# Import directly
from telebot.interfaces.api.sse import (
    resolve_channel_alias,
    _resolve_topic_id,
    handle_pdf_output,
    _run_digest_background,
    start_digest_generation,
    check_task_status,
    JOBS
)


class TestSSEInterface(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        JOBS.clear()

    @patch("telebot.interfaces.api.sse.PDFRenderer")
    @patch("telebot.interfaces.api.sse.TelegramClient")
    @patch("telebot.interfaces.api.sse.settings")
    async def test_handle_pdf_output(self, mock_settings, MockClient, MockRenderer):
        mock_renderer_inst = MockRenderer.return_value
        mock_renderer_inst.render.return_value = "reports/test.pdf"
        
        mock_client_inst = MockClient.return_value
        mock_client_inst.connect = AsyncMock()
        mock_client_inst.disconnect = AsyncMock()
        mock_client_inst.send_file = AsyncMock()

        digest = MagicMock()
        # Call the imported function
        extra = await handle_pdf_output(digest, 123, "user1", "chan1")
        
        self.assertIn("PDF Report generated", extra)
        mock_client_inst.send_file.assert_called_once()

    async def test_resolve_channel_alias(self):
        self.assertEqual(resolve_channel_alias("coursebusters"), -1001603660516)

    async def test_check_task_status_missing(self):
        res = await check_task_status("missing")
        self.assertIn("not found", res)
