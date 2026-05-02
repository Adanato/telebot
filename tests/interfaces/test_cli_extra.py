"""Extra CLI smoke coverage: error-path behavior."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from course_scout.interfaces.cli.main import app


class TestCLIExtra(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    @patch("course_scout.interfaces.cli.main.TelethonScraper")
    @patch("course_scout.interfaces.cli.main.load_settings")
    def test_resolve_and_list_topics_error_paths(self, mock_load_settings, MockScraper):
        # Settings stub
        mock_load_settings.return_value.tg_api_id = 123
        mock_load_settings.return_value.tg_api_hash = "hash"
        mock_load_settings.return_value.session_path = "test.session"
        mock_load_settings.return_value.phone_number = None
        mock_load_settings.return_value.login_code = None

        # 1. Resolve channel ID
        result = self.runner.invoke(app, ["resolve-channel-id", "@test"])
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(len(result.output) > 0)

        # 2. List topics — auth failure path
        mock_scraper_inst = MockScraper.return_value
        mock_scraper_inst.list_topics = AsyncMock(side_effect=Exception("Auth fail"))
        result_list = self.runner.invoke(app, ["list-topics", "@test"])
        # The async helper raises, runner captures non-zero exit
        self.assertNotEqual(result_list.exit_code, 0)
