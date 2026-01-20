import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from telebot.interfaces.mcp.main import (
    generate_digest,
    list_topics,
    get_use_case,
)


class TestMCPInterface(unittest.IsolatedAsyncioTestCase):
    @patch("telebot.interfaces.mcp.main.GenerateDigestUseCase.execute", new_callable=AsyncMock)
    @patch("telebot.interfaces.mcp.main.PDFRenderer")
    @patch("telebot.interfaces.mcp.main.settings")
    async def test_generate_digest_success(self, mock_settings, MockRenderer, mock_execute):
        mock_digest = MagicMock()
        mock_digest.to_markdown.return_value = "# Markdown"
        mock_execute.return_value = mock_digest
        
        mock_renderer_inst = MockRenderer.return_value
        mock_renderer_inst.render.return_value = "report.pdf"

        result = await generate_digest("coursebusters", pdf=True)
        
        self.assertIn("# Markdown", result)
        self.assertIn("PDF Report generated", result)
        mock_execute.assert_called_once()

    @patch("telebot.interfaces.mcp.main.TelethonScraper")
    @patch("telebot.interfaces.mcp.main.settings")
    async def test_list_topics_success(self, mock_settings, MockScraper):
        mock_scraper_inst = MockScraper.return_value
        mock_scraper_inst.list_topics = AsyncMock(return_value=[{"id": 1, "title": "T1"}])
        
        result = await list_topics("coursebusters")
        self.assertIn("ID: 1 | Title: T1", result)

    @patch("telebot.interfaces.mcp.main.OrchestratedSummarizer")
    @patch("telebot.interfaces.mcp.main.TelethonScraper")
    @patch("telebot.interfaces.mcp.main.settings")
    def test_get_use_case(self, mock_settings, mock_scraper, mock_summarizer):
        uc = get_use_case()
        self.assertIsNotNone(uc)
        mock_summarizer.assert_called()
