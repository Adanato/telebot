import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from course_scout.interfaces.mcp.main import (
    generate_digest,
    get_use_case,
    list_topics,
)


class TestMCPInterface(unittest.IsolatedAsyncioTestCase):
    @patch("course_scout.interfaces.mcp.main.GenerateDigestUseCase.execute", new_callable=AsyncMock)
    @patch("course_scout.interfaces.mcp.main.PDFRenderer")
    @patch("course_scout.interfaces.mcp.main._get_settings")
    async def test_generate_digest_success(self, mock_get_settings, MockRenderer, mock_execute):
        mock_digest = MagicMock()
        mock_digest.to_markdown.return_value = "# Markdown"
        mock_execute.return_value = mock_digest

        mock_renderer_inst = MockRenderer.return_value
        mock_renderer_inst.render.return_value = "report.pdf"

        result = await generate_digest("coursebusters", pdf=True)

        self.assertIn("# Markdown", result)
        self.assertIn("PDF Report generated", result)
        mock_execute.assert_called_once()

    @patch("course_scout.interfaces.mcp.main.TelethonScraper")
    @patch("course_scout.interfaces.mcp.main._get_settings")
    async def test_list_topics_success(self, mock_get_settings, MockScraper):
        mock_scraper_inst = MockScraper.return_value
        mock_scraper_inst.list_topics = AsyncMock(return_value=[{"id": 1, "title": "T1"}])

        result = await list_topics("coursebusters")
        self.assertIn("ID: 1 | Title: T1", result)

    @patch("course_scout.interfaces.mcp.main.OrchestratedSummarizer")
    @patch("course_scout.interfaces.mcp.main.TelethonScraper")
    @patch("course_scout.interfaces.mcp.main._get_settings")
    def test_get_use_case(self, mock_get_settings, mock_scraper, mock_summarizer):
        uc = get_use_case()
        self.assertIsNotNone(uc)
        mock_summarizer.assert_called()
