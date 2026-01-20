import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from telebot.application.worker import TelebotWorker


class TestTelebotWorker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Patch load_settings to avoid file IO
        with patch("telebot.application.worker.load_settings") as mock_load:
            self.mock_settings = MagicMock()
            self.mock_settings.tasks = [{"name": "test", "channel_id": "@chan", "topics": ["T1"]}]
            self.mock_settings.lookback_days = 1
            self.mock_settings.report_format = "pdf"
            self.mock_settings.tg_notify_target = "target"
            mock_load.return_value = self.mock_settings
            
            # Patch collaborators in __init__
            with patch("telebot.application.worker.TelethonScraper"), \
                 patch("telebot.application.worker.OrchestratedSummarizer"), \
                 patch("telebot.application.worker.PDFRenderer"), \
                 patch("telebot.application.worker.TelethonNotifier"):
                self.worker = TelebotWorker(config_path="config.yaml")
                
                # Manually set mocks for control
                self.worker.use_case = AsyncMock()
                self.worker.notifier = AsyncMock()
                self.worker.renderer = MagicMock()

    @patch("asyncio.sleep", new_callable=AsyncMock)
    async def test_run_task_success(self, mock_sleep):
        # Setup mock result
        mock_digest = MagicMock()
        mock_digest.summaries = ["Summary"]
        mock_digest.to_markdown.return_value = "# Markdown"
        self.worker.use_case.execute.return_value = mock_digest
        
        self.worker.renderer.render.return_value = "report.pdf"

        # Run task
        await self.worker.run_task(self.mock_settings.tasks[0])

        self.worker.use_case.execute.assert_called_once()
        self.worker.notifier.send_message.assert_called()
        self.worker.notifier.send_document.assert_called_with("report.pdf", caption=unittest.mock.ANY)

    async def test_run_task_no_messages(self):
        self.worker.use_case.execute.return_value = None

        await self.worker.run_task(self.mock_settings.tasks[0])

        self.worker.notifier.send_message.assert_not_called()
        self.worker.notifier.send_document.assert_not_called()

    @patch("telebot.application.worker.TelebotWorker.run_task", new_callable=AsyncMock)
    async def test_start_logic(self, mock_run_task):
        # Mock sleep to exit loop
        with patch("asyncio.sleep", side_effect=[None, Exception("StopLoop")]):
            try:
                await self.worker.start()
            except Exception as e:
                if str(e) != "StopLoop":
                    raise e
        
        # Should have called run_task for the one task in settings
        mock_run_task.assert_called_with(self.mock_settings.tasks[0])

    @patch("telebot.application.worker.TelebotWorker.start")
    def test_main_startup(self, mock_start):
        from telebot.application.worker import main
        with patch("asyncio.run"):
             main()
        self.assertTrue(mock_start.called)
