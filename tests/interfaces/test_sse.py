import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Import directly
from course_scout.interfaces.api.sse import (
    JOBS,
    check_task_status,
    handle_pdf_output,
    resolve_channel_alias,
)


class TestSSEInterface(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        JOBS.clear()

    @patch("course_scout.interfaces.api.sse.PDFRenderer")
    @patch("telethon.TelegramClient")  # lazy-imported inside handle_pdf_output
    @patch("course_scout.interfaces.api.sse._get_settings")
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
