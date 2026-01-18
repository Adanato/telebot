import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from typer.testing import CliRunner
from telebot.interfaces.cli.main import app

runner = CliRunner()

class TestCLI(unittest.TestCase):
    @patch("telebot.interfaces.cli.main.GenerateDigestUseCase.execute", new_callable=AsyncMock)
    @patch("telebot.interfaces.cli.main.PDFRenderer")
    def test_digest_command_with_pdf(self, MockRenderer, mock_execute):
        # Setup mock execute return
        mock_digest = MagicMock()
        mock_digest.summaries = ["Summary"]
        mock_digest.date = "2023-01-01"
        mock_digest.action_items = []
        mock_digest.key_links = []
        mock_execute.return_value = mock_digest
        
        # Setup mock renderer
        mock_renderer_inst = MockRenderer.return_value
        mock_renderer_inst.render.return_value = "reports/test.pdf"

        result = runner.invoke(app, ["digest", "@testchannel", "--pdf"])
        
        self.assertEqual(result.exit_code, 0)
        self.assertIn("PDF Report generated", result.output)
        mock_execute.assert_called_once()
        mock_renderer_inst.render.assert_called_once()

    @patch("telebot.interfaces.cli.main.TelethonScraper")
    def test_list_topics_command(self, MockScraper):
        mock_scraper_inst = MockScraper.return_value
        mock_scraper_inst.client = MagicMock()
        mock_scraper_inst.client.start = AsyncMock()
        mock_scraper_inst.client.__aenter__ = AsyncMock(return_value=mock_scraper_inst.client)
        
        # Mock result of GetForumTopicsRequest
        mock_topic = MagicMock()
        mock_topic.id = 123
        mock_topic.title = "Test Topic"
        
        mock_result = MagicMock()
        mock_result.topics = [mock_topic]
        
        # Mock client call result
        mock_scraper_inst.client.side_effect = AsyncMock(return_value=mock_result)

        result = runner.invoke(app, ["list-topics", "@testchannel"])
        
        self.assertEqual(result.exit_code, 0)
        self.assertIn("ID: 123 | Title: Test Topic", result.output)

    def test_digest_invalid_channel(self):
        # This will fail on Settings validation if .env is missing, 
        # but in tests we often mock Settings or provide defaults.
        with patch("telebot.interfaces.cli.main.Settings") as MockSettings:
            MockSettings.return_value.tg_api_id = 1
            MockSettings.return_value.tg_api_hash = "hash"
            MockSettings.return_value.gemini_api_key = "key"
            
            # If execute fails
            with patch("telebot.interfaces.cli.main.GenerateDigestUseCase.execute", side_effect=Exception("Failed")):
                result = runner.invoke(app, ["digest", "@invalid"])
                self.assertNotEqual(result.exit_code, 0)
