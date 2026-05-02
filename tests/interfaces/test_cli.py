"""CLI smoke tests.

Coverage:
- list-topics    : end-to-end with mocked scraper
- resolve-channel-id : alias + numeric ID resolution
- scan           : argument plumbing (full execution lives in
                   integration tests against fakes)
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from course_scout.interfaces.cli.main import app

runner = CliRunner()


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.patcher_settings = patch("course_scout.interfaces.cli.main.load_settings")
        self.mock_load_settings = self.patcher_settings.start()
        # Settings shape used by both list_topics and scan
        self.mock_load_settings.return_value.tg_api_id = 123
        self.mock_load_settings.return_value.tg_api_hash = "hash"
        self.mock_load_settings.return_value.session_path = "test.session"
        self.mock_load_settings.return_value.phone_number = None
        self.mock_load_settings.return_value.login_code = None
        self.mock_load_settings.return_value.tasks = []

    def tearDown(self):
        self.patcher_settings.stop()

    @patch("course_scout.interfaces.cli.main.TelethonScraper")
    def test_list_topics_command(self, MockScraper):
        mock_scraper_inst = MockScraper.return_value
        mock_scraper_inst.list_topics = AsyncMock(return_value=[{"id": 123, "title": "Test Topic"}])

        result = runner.invoke(app, ["list-topics", "@testchannel"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("ID: 123 | Title: Test Topic", result.output)

    def test_resolve_channel_id_alias(self):
        result = runner.invoke(app, ["resolve-channel-id", "coursebusters"])
        self.assertEqual(result.exit_code, 0)
        # The command function returns the int but typer converts to str
        self.assertIn("-1001603660516", result.output)

    def test_resolve_channel_id_numeric(self):
        # `--` separator stops Typer from treating the leading-dash arg as a flag
        result = runner.invoke(app, ["resolve-channel-id", "--", "-1001603660516"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("-1001603660516", result.output)

    def test_scan_no_tasks_configured(self):
        # Default mock_load_settings returns tasks = []
        result = runner.invoke(app, ["scan"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("No tasks configured", result.output)
