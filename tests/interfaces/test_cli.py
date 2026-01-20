import datetime
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from telebot.domain.models import ChannelDigest
from telebot.interfaces.cli.main import app

runner = CliRunner()


class TestCLI(unittest.TestCase):
    def setUp(self):
        # Always mock Settings to avoid .env issues
        self.patcher_settings = patch("telebot.interfaces.cli.main.Settings")
        self.mock_settings = self.patcher_settings.start()
        self.mock_settings.return_value.tg_api_id = 123
        self.mock_settings.return_value.tg_api_hash = "hash"
        self.mock_settings.return_value.gemini_api_key = "key"
        self.mock_settings.return_value.session_path = "test.session"

    def tearDown(self):
        self.patcher_settings.stop()

    @patch("telebot.interfaces.cli.main.GenerateDigestUseCase.execute", new_callable=AsyncMock)
    @patch("telebot.interfaces.cli.main.PDFRenderer")
    def test_digest_command_with_pdf(self, MockRenderer, mock_execute):
        # Setup mock execute return
        mock_digest = MagicMock(spec=ChannelDigest)
        mock_digest.summaries = ["Summary"]
        mock_digest.date = datetime.date(2025, 1, 1)
        mock_digest.action_items = []
        mock_digest.key_links = []
        mock_digest.to_markdown.return_value = "# Markdown Content"
        mock_execute.return_value = mock_digest

        # Setup mock renderer
        mock_renderer_inst = MockRenderer.return_value
        mock_renderer_inst.render.return_value = "reports/test.pdf"

        # Also mock file write
        with patch("builtins.open", MagicMock()):
            result = runner.invoke(app, ["digest", "@testchannel", "--pdf"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("PDF Report generated", result.output)
        mock_execute.assert_called_once()
        mock_renderer_inst.render.assert_called_once()

    @patch("telebot.interfaces.cli.main.TelethonScraper")
    def test_list_topics_command(self, MockScraper):
        mock_scraper_inst = MockScraper.return_value
        mock_scraper_inst.list_topics = AsyncMock(return_value=[{"id": 123, "title": "Test Topic"}])

        result = runner.invoke(app, ["list-topics", "@testchannel"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("ID: 123 | Title: Test Topic", result.output)
