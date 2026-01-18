import asyncio
from unittest import TestCase
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner
from telebot.interfaces.cli.main import app
from telebot.domain.models import ChannelDigest
from datetime import date

runner = CliRunner()

class TestCLI(TestCase):
    @patch('telebot.interfaces.cli.main.TelethonScraper')
    @patch('telebot.interfaces.cli.main.GeminiSummarizer')
    @patch('telebot.interfaces.cli.main.GenerateDigestUseCase')
    @patch('telebot.interfaces.cli.main.Settings')
    def test_digest_command(self, MockSettings, MockUseCase, MockSummarizer, MockScraper):
        # Setup mocks
        mock_settings = MockSettings.return_value
        mock_settings.tg_api_id = 123
        mock_settings.tg_api_hash = "abc"
        
        mock_use_case_instance = MockUseCase.return_value
        
        # Async mock return
        expected_digest = ChannelDigest(
             channel_name="TestChannel",
             date=date(2023, 1, 1),
             summaries=["Summary 1"],
             action_items=["Do this"],
             key_links=["link1"]
        )
        
        # Since asyncio.run is used in the command, we need to make execute awaitable
        async def async_return():
            return expected_digest
            
        mock_use_case_instance.execute.side_effect = lambda *args, **kwargs: async_return()

        result = runner.invoke(app, ["digest", "TestChannel", "--days", "2"])
        
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Daily Digest for TestChannel", result.stdout)
        self.assertIn("Summary 1", result.stdout)
        
        # Verify calls
        MockUseCase.assert_called_once()
        mock_use_case_instance.execute.assert_called_with("TestChannel", topic_id=None, lookback_days=2)
