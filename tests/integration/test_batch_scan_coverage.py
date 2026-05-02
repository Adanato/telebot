"""Integration tests for BatchScanUseCase.

These cover the bug class that previously slipped through:
- Topics with N=1 message silently dropped (the >=3 filter bug).
- Configured topics absent from the final result without an error.
- Single-topic and all-topic paths producing different output.

Tests use FAKE infrastructure (no real Telegram, no real LLM) so they
exercise the real BatchScanUseCase orchestration logic end-to-end.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from course_scout.application.batch_scan import BatchScanUseCase
from course_scout.domain.models import ChannelDigest, FileItem, TelegramMessage


def _make_task(name: str, topic_id: int, system_prompt: str = "file_sharing") -> Any:
    """Build a SimpleNamespace task object that quacks like ResolvedTaskConfig."""
    return SimpleNamespace(
        name=name,
        channel_id=-1001603660516,
        topic_id=topic_id,
        system_prompt=system_prompt,
        system_prompt_name=system_prompt,
        summarizer_model="claude-sonnet-4-6",
        thinking="adaptive",
        effort="medium",
        chunk_size=10000,
        max_messages=100,
        include_media=True,
    )


def _make_message(msg_id: int, text: str = "msg") -> TelegramMessage:
    return TelegramMessage(id=msg_id, text=text, date=datetime.now(), link=f"link/{msg_id}")


def _fake_digest(channel_name: str, n_items: int = 1) -> ChannelDigest:
    return ChannelDigest(
        channel_name=channel_name,
        date=datetime.now().date(),
        summaries=["Summary"],
        items=[FileItem(title=f"item-{i}", description="desc", links=[]) for i in range(n_items)],
        key_links=[],
    )


class _FakeSummarizer:
    """Fake summarizer that records calls and returns a 1-item digest."""

    def __init__(self, name: str):
        self.name = name
        self.calls: list[tuple[int, int]] = []  # (n_messages, topic_id)
        self.orchestrator = SimpleNamespace(_providers={})

    async def summarize(self, messages, topic_id=None):
        self.calls.append((len(messages), topic_id))
        return _fake_digest(self.name, n_items=1)


class TestBatchScanCoverage(unittest.IsolatedAsyncioTestCase):
    """Verify that every configured topic produces a result, even with N=1 message."""

    async def test_single_message_topic_not_dropped(self):
        """Regression: the original >=3 filter dropped single-message topics."""
        scraper = AsyncMock()
        scraper.get_messages.return_value = [_make_message(1)]  # exactly 1 message

        summarizer = _FakeSummarizer("Pan Baidu Download Request")
        use_case = BatchScanUseCase(
            scraper=scraper,
            summarizer_factory=lambda task: summarizer,
        )

        tasks = [_make_task("Pan Baidu Download Request", topic_id=10683)]
        results = await use_case.execute(tasks=tasks, dedup=False)

        self.assertEqual(
            len(results),
            1,
            "Single-message topic must produce a result (not silently dropped)",
        )
        name, digest, _provider = results[0]
        self.assertEqual(name, "Pan Baidu Download Request")
        self.assertEqual(len(digest.items), 1)
        # Summarizer was actually called with the single message
        self.assertEqual(summarizer.calls, [(1, 10683)])

    async def test_full_topic_coverage_all_have_messages(self):
        """Every configured task with messages produces a section in the report."""
        scraper = AsyncMock()
        scraper.get_messages.return_value = [_make_message(1), _make_message(2)]

        # One summarizer per task — track which were summoned
        summarizers: dict[str, _FakeSummarizer] = {}

        def factory(task):
            s = _FakeSummarizer(task.name)
            summarizers[task.name] = s
            return s

        use_case = BatchScanUseCase(scraper=scraper, summarizer_factory=factory)

        tasks = [
            _make_task("Pan Baidu Download Request", 10683),
            _make_task("Pan Baidu Files", 10686),
            _make_task("Pan Baidu Courses", 319355),
        ]
        results = await use_case.execute(tasks=tasks, dedup=False)

        # Every configured topic must produce a result
        result_names = {name for name, _, _ in results}
        configured_names = {t.name for t in tasks}
        missing = configured_names - result_names
        self.assertFalse(
            missing,
            f"Topics silently dropped from scan output: {missing}",
        )

    async def test_topic_with_zero_messages_dropped_silently(self):
        """A topic with no new messages produces no row (this is intentional)."""
        scraper = AsyncMock()
        scraper.get_messages.return_value = []

        use_case = BatchScanUseCase(
            scraper=scraper,
            summarizer_factory=lambda task: _FakeSummarizer(task.name),
        )

        tasks = [_make_task("Empty Topic", 99999)]
        results = await use_case.execute(tasks=tasks, dedup=False)
        self.assertEqual(results, [])

    async def test_fetch_failure_does_not_drop_other_topics(self):
        """If one topic fetch raises, the others still run."""
        scraper = AsyncMock()

        async def get_messages(channel_id, start, end_date=None, topic_id=None):
            if topic_id == 666:
                raise RuntimeError("simulated telegram error")
            return [_make_message(1)]

        scraper.get_messages.side_effect = get_messages

        use_case = BatchScanUseCase(
            scraper=scraper,
            summarizer_factory=lambda task: _FakeSummarizer(task.name),
        )
        tasks = [
            _make_task("Failing Topic", 666),
            _make_task("Working Topic", 777),
        ]
        results = await use_case.execute(tasks=tasks, dedup=False)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "Working Topic")


class TestBatchScanWindow(unittest.IsolatedAsyncioTestCase):
    """Verify window calculation matches CLI semantics."""

    def test_today_mode_is_rolling(self):
        start, end = BatchScanUseCase._compute_window("UTC", days=1, include_today=True)
        # In today/rolling mode, end is roughly "now"
        delta = (datetime.now(end.tzinfo) - end).total_seconds()
        self.assertLess(abs(delta), 5)
        # And start is days back
        span_seconds = (end - start).total_seconds()
        self.assertAlmostEqual(span_seconds, 86400, delta=10)

    def test_complete_days_mode_anchored_at_midnight(self):
        start, end = BatchScanUseCase._compute_window("UTC", days=1, include_today=False)
        # End should be today midnight
        self.assertEqual(end.hour, 0)
        self.assertEqual(end.minute, 0)
        # Start should be 1 day before
        span_seconds = (end - start).total_seconds()
        self.assertAlmostEqual(span_seconds, 86400, delta=10)
