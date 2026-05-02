"""Unit tests for CLI helper functions.

Targets the parts of interfaces/cli/main.py that don't need a CliRunner —
pure helpers + tiny utilities. These bring CLI coverage up cheaply.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from course_scout.interfaces.cli.main import (
    _filter_tasks_by_topic,
    _make_summarizer_factory,
    _maybe_publish_task,
    _resolve_channel_id,
    _setup_run_logs,
)


def _task(name: str, topic_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        channel_id=-1001603660516,
        topic_id=topic_id,
        system_prompt="file_sharing",
        system_prompt_name="file_sharing",
        summarizer_model="claude-sonnet-4-6",
        thinking="adaptive",
        effort="medium",
        chunk_size=10000,
        max_messages=100,
        include_media=True,
    )


class TestResolveChannelId(unittest.TestCase):
    def test_alias_coursebusters(self):
        self.assertEqual(_resolve_channel_id("coursebusters"), -1001603660516)

    def test_alias_with_at_prefix_and_caps(self):
        # The function strips '@' and lowercases before alias lookup
        self.assertEqual(_resolve_channel_id("@CourseBusters"), -1001603660516)

    def test_numeric_string(self):
        self.assertEqual(_resolve_channel_id("12345"), 12345)

    def test_negative_numeric_string(self):
        self.assertEqual(_resolve_channel_id("-100123"), -100123)

    def test_unknown_passthrough(self):
        # Unknown alias falls through to original string
        self.assertEqual(_resolve_channel_id("@unknown_channel"), "@unknown_channel")


class TestFilterTasksByTopic(unittest.TestCase):
    def setUp(self):
        self.tasks = [
            _task("Pan Baidu Download Request", 10683),
            _task("Pan Baidu Files", 10686),
            _task("2D Artists Lounge", 3077),
        ]
        self.scraper = MagicMock()

    def test_no_topic_returns_all(self):
        result = _filter_tasks_by_topic(self.tasks, None, self.scraper)
        self.assertEqual(result, self.tasks)

    def test_numeric_topic_id(self):
        result = _filter_tasks_by_topic(self.tasks, "10683", self.scraper)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].topic_id, 10683)

    def test_name_substring_match(self):
        result = _filter_tasks_by_topic(self.tasks, "baidu files", self.scraper)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].topic_id, 10686)

    def test_unknown_topic_exits(self):
        import typer

        # The fallback path also queries Telethon — make list_topics return [].
        self.scraper.list_topics = AsyncMock(return_value=[])
        with self.assertRaises(typer.Exit):
            _filter_tasks_by_topic(self.tasks, "nonexistent", self.scraper)


class TestMakeSummarizerFactory(unittest.TestCase):
    @patch("course_scout.interfaces.cli.main.OrchestratedSummarizer")
    def test_factory_passes_task_attrs(self, MockSummarizer):
        scraper = MagicMock()
        factory = _make_summarizer_factory(scraper)
        task = _task("X", 1)
        task.summarizer_model = "claude-haiku-4-5"
        task.effort = "low"

        _ = factory(task)

        # Verify the summarizer was constructed with the task's attributes
        MockSummarizer.assert_called_once()
        kwargs = MockSummarizer.call_args.kwargs
        self.assertEqual(kwargs["summarizer_model"], "claude-haiku-4-5")
        self.assertEqual(kwargs["effort"], "low")
        self.assertIs(kwargs["scraper"], scraper)


class TestSetupRunLogs(unittest.TestCase):
    def test_creates_dated_dir(self):
        with patch.dict(os.environ, {"COURSE_SCOUT_LOG_DIR": "/tmp/cs-test-logs"}):
            run_dir = _setup_run_logs()
            self.assertTrue(run_dir.startswith("/tmp/cs-test-logs/scans/"))
            self.assertTrue(os.path.isdir(run_dir))


class TestMaybePublishTask(unittest.TestCase):
    """The auto-publish hook at the end of `scan` is best-effort + degrades gracefully."""

    @patch("course_scout.infrastructure.tasknotes.TaskNotesPublisher")
    def test_skips_when_vault_dir_missing(self, MockPublisher):
        from pathlib import Path

        publisher = MockPublisher.return_value
        publisher.vault_dir = Path("/nonexistent/vault/path/abc123xyz")
        # Should NOT call publish() because vault dir doesn't exist
        _maybe_publish_task("reports/2026-05-02/scan_2026-05-02.md", pdf_generated=True)
        publisher.publish.assert_not_called()

    @patch("course_scout.infrastructure.tasknotes.TaskNotesPublisher")
    def test_publishes_when_vault_present(self, MockPublisher, tmp_path=None):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / "TaskNotes" / "Inbox").mkdir(parents=True)

            publisher = MockPublisher.return_value
            publisher.vault_dir = vault
            publisher.publish.return_value = vault / "TaskNotes" / "Inbox" / "stub.md"

            md_path = vault / "scan_2026-05-02.md"
            md_path.write_text("# header\n## Top 5 Finds\n1. test\n", encoding="utf-8")

            _maybe_publish_task(str(md_path), pdf_generated=False)

            publisher.publish.assert_called_once()

    @patch("course_scout.infrastructure.tasknotes.TaskNotesPublisher")
    def test_swallows_publisher_errors(self, MockPublisher):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            vault = Path(tmp)
            publisher = MockPublisher.return_value
            publisher.vault_dir = vault
            publisher.publish.side_effect = OSError("disk full")

            # Must not raise — failures are logged + reported as a warning line
            _maybe_publish_task("scan_2026-05-02.md", pdf_generated=True)


class TestResolveTopicByName(unittest.IsolatedAsyncioTestCase):
    async def test_exact_match_preferred(self):
        from course_scout.interfaces.cli.main import _resolve_topic_by_name

        scraper = MagicMock()
        scraper.list_topics = AsyncMock(
            return_value=[
                {"id": 1, "title": "2D Artists Lounge"},
                {"id": 2, "title": "Asian Artists Discussion"},
            ]
        )
        result = await _resolve_topic_by_name(scraper, -100123, "2D Artists Lounge")
        self.assertEqual(result, 1)

    async def test_substring_match_fallback(self):
        from course_scout.interfaces.cli.main import _resolve_topic_by_name

        scraper = MagicMock()
        scraper.list_topics = AsyncMock(
            return_value=[
                {"id": 1, "title": "2D Artists Lounge"},
                {"id": 2, "title": "Asian Artists Discussion"},
            ]
        )
        result = await _resolve_topic_by_name(scraper, -100123, "lounge")
        self.assertEqual(result, 1)

    async def test_no_match_returns_zero(self):
        from course_scout.interfaces.cli.main import _resolve_topic_by_name

        scraper = MagicMock()
        scraper.list_topics = AsyncMock(return_value=[])
        result = await _resolve_topic_by_name(scraper, -100123, "missing")
        self.assertEqual(result, 0)
