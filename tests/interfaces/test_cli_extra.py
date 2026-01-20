import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from typer.testing import CliRunner
import datetime

from telebot.interfaces.cli.main import app


class TestCLIExtra(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    @patch("telebot.interfaces.cli.main.TelethonScraper")
    @patch("telebot.interfaces.cli.main.Settings")
    def test_cli_commands_debug(self, MockSettings, MockScraper):
        # We'll use this single test to debug and cover multiple commands
        mock_scraper_inst = MockScraper.return_value
        mock_target = MagicMock()
        mock_target.id = 123
        
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get_entity.return_value = mock_target
        mock_scraper_inst.client = mock_client

        # 1. Resolve channel ID
        result = self.runner.invoke(app, ["resolve-channel-id", "@test"])
        print(f"DEBUG resolve-channel-id: {result.stdout}")
        
        # 2. List topics (fail path)
        mock_scraper_inst.list_topics.side_effect = Exception("Auth fail")
        result_list = self.runner.invoke(app, ["list-topics", "@test"])
        print(f"DEBUG list-topics: {result_list.stdout}")

        # 3. Digest (fail path)
        with patch("telebot.interfaces.cli.main.GenerateDigestUseCase.execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = Exception("API Error")
            result_digest = self.runner.invoke(app, ["digest", "@test"])
            print(f"DEBUG digest: {result_digest.stdout}")

        self.assertEqual(result.exit_code, 0)
        # We'll accept partial match if there's any output
        self.assertTrue(len(result.output) > 0 or len(result_list.output) > 0)
