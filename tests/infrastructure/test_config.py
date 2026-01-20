import unittest
from unittest.mock import patch, MagicMock
import os

from telebot.infrastructure.config import Settings, load_settings, TaskConfig


class TestConfig(unittest.TestCase):
    def test_task_config_validation(self):
        task = TaskConfig(name="test", channel_id="@test", topics=["topic1"])
        self.assertEqual(task.name, "test")
        self.assertEqual(task.channel_id, "@test")

    def test_settings_default_values(self):
        with patch.dict(os.environ, {
            "TG_API_ID": "123",
            "TG_API_HASH": "hash",
            "GEMINI_API_KEY": "key",
        }):
            settings = Settings()
            self.assertEqual(settings.tg_api_id, 123)
            self.assertEqual(settings.session_path, "telebot.session")

    @patch("telebot.infrastructure.config.yaml.safe_load")
    @patch("builtins.open", new_callable=MagicMock)
    @patch.dict(os.environ, {
        "TG_API_ID": "123",
        "TG_API_HASH": "hash",
        "GEMINI_API_KEY": "key",
    })
    def test_load_settings_success(self, mock_open, mock_yaml):
        mock_yaml.return_value = {
            "global": {"lookback_days": 2},
            "tasks": [{"name": "test", "channel_id": "@test"}]
        }
        
        settings = load_settings("config.yaml")
        
        self.assertEqual(len(settings.tasks), 1)
        self.assertEqual(settings.tasks[0]["name"], "test")
        self.assertEqual(settings.lookback_days, 2)

    @patch("telebot.infrastructure.config.os.path.exists", return_value=False)
    @patch.dict(os.environ, {
        "TG_API_ID": "123",
        "TG_API_HASH": "hash",
        "GEMINI_API_KEY": "key",
    })
    def test_load_settings_file_not_found(self, mock_exists):
        settings = load_settings("non_existent.yaml")
        self.assertEqual(len(settings.tasks), 0)
