"""Batch scan use case: orchestrate digest generation across many topics.

This is the application-layer counterpart to interfaces/cli/main.scan().
The CLI used to own this orchestration via _scan_all_tasks(), which caused
drift from the single-topic GenerateDigestUseCase path (e.g. the >=3
message filter that silently dropped Pan Baidu Download Request).

After refactor: both single-topic (--topic flag) and all-topics scan
flow through this use case, so they share post-processing semantics by
construction.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from course_scout.application.digest_processing import (
    assign_priority,
    enforce_category_allowlist,
    reclassify_by_topic_name,
)
from course_scout.domain.models import ChannelDigest

logger = logging.getLogger(__name__)


class BatchScanUseCase:
    """Run summarization for a list of tasks against a shared scraper.

    Returns one (task_name, digest, provider) tuple per task that produced
    output. Tasks with no messages are silently skipped (no row emitted).
    """

    def __init__(self, scraper: Any, summarizer_factory: Any):
        """Initialize with scraper and a factory that builds OrchestratedSummarizer per task.

        summarizer_factory: callable (task) -> OrchestratedSummarizer.
        Injecting a factory keeps this use case independent of the
        infrastructure-layer summarizer class.
        """
        self.scraper = scraper
        self.summarizer_factory = summarizer_factory

    async def execute(
        self,
        tasks: list[Any],
        timezone: str = "UTC",
        days: int = 1,
        include_today: bool = False,
        dedup: bool = True,
        run_dir: str | None = None,
    ) -> list[tuple[str, ChannelDigest, Any]]:
        """Run fetch + summarize across all tasks; return non-empty results."""
        start_date, end_date = self._compute_window(timezone, days, include_today)
        logger.info(f"Batch scan window: {start_date.isoformat()} → {end_date.isoformat()}")

        # Phase 1: sequential fetch (per-channel rate limits make parallel risky)
        fetched = await self._fetch_all(tasks, start_date, end_date)
        if not fetched:
            return []

        # Phase 2: parallel summarization
        coros = [
            self._summarize_one(name, task, messages, dedup, run_dir)
            for name, (task, messages) in fetched.items()
        ]
        raw = await asyncio.gather(*coros)
        return [r for r in raw if r is not None]

    @staticmethod
    def _compute_window(timezone: str, days: int, include_today: bool) -> tuple[datetime, datetime]:
        """Compute (start_date, end_date) per CLI semantics.

        - include_today=True  → rolling window (now - days, now)
        - include_today=False → fixed complete days (today midnight - days, today midnight)
        """
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)
        if include_today:
            return now - timedelta(days=days), now
        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return today_midnight - timedelta(days=days), today_midnight

    async def _fetch_all(
        self, tasks: list[Any], start_date: datetime, end_date: datetime
    ) -> dict[str, tuple[Any, list]]:
        """Fetch messages for every task. Topics with zero messages are dropped.

        IMPORTANT: there is NO minimum-message filter. A topic with one
        message is processed. Empty topics are dropped only because
        there's nothing to summarize. (The previous >=3 filter silently
        hid single-message file-share topics.)
        """
        fetched: dict[str, tuple[Any, list]] = {}
        for task in tasks:
            name = task.name
            try:
                messages = await self.scraper.get_messages(
                    task.channel_id,
                    start_date,
                    end_date=end_date,
                    topic_id=task.topic_id,
                )
                messages = messages[: task.max_messages]
                if messages:
                    fetched[name] = (task, messages)
                    logger.info(f"   📨 {name}: {len(messages)} messages")
                else:
                    logger.info(f"   ⏭️  {name}: no messages")
            except Exception as e:
                logger.error(f"   ❌ {name}: fetch error — {e}", exc_info=True)
        return fetched

    async def _summarize_one(
        self,
        name: str,
        task: Any,
        messages: list,
        dedup: bool,
        run_dir: str | None,
    ) -> tuple[str, ChannelDigest, Any] | None:
        """Summarize one topic, apply post-processing, return (name, digest, provider)."""
        topic_logger = self._topic_logger(run_dir, name)
        topic_logger.info(
            f"Starting: {len(messages)} msgs, topic={task.topic_id}, "
            f"model={task.summarizer_model}, thinking={task.thinking}, "
            f"effort={task.effort}, chunk_size={task.chunk_size}"
        )

        try:
            summarizer = self.summarizer_factory(task)
            digest = await summarizer.summarize(messages, topic_id=task.topic_id)
            if not digest:
                return None

            # Post-processing — same chain that the previous CLI helper applied.
            enforce_category_allowlist(digest, task.system_prompt_name, topic_name=name)
            reclassify_by_topic_name(digest, name)
            assign_priority(digest)

            if dedup:
                from course_scout.infrastructure.dedup import DigestDeduper

                pre = len(digest.items)
                dropped = DigestDeduper(channel_name=name).filter(digest)
                if dropped:
                    topic_logger.info(f"Dedup: dropped {dropped}/{pre} previously-seen item(s)")
            else:
                topic_logger.info("Dedup: skipped (--no-dedup)")

            # Pin diff — best-effort; failures don't break the scan.
            from course_scout.infrastructure.pins import diff_and_record

            try:
                pin_md = await diff_and_record(self.scraper, task.channel_id, task.topic_id)
                if pin_md:
                    digest.summaries.insert(0, pin_md)
                    topic_logger.info("Pin changes detected and injected into summary")
            except Exception as e:
                topic_logger.warning(f"Pin diff failed: {e}")

            topic_logger.info(f"Completed: {len(digest.items)} items extracted")

            # Capture the provider so callers can aggregate usage stats.
            provider = (
                list(summarizer.orchestrator._providers.values())[0]
                if summarizer.orchestrator._providers
                else None
            )
            usage = getattr(provider, "usage", None)
            if usage is not None:
                for call in usage.calls:
                    topic_logger.info(
                        f"  {call['model']}: {call['input_tokens']} in / "
                        f"{call['output_tokens']} out / {call['duration_ms']}ms"
                    )

            return (name, digest, provider)
        except Exception as e:
            topic_logger.error(f"Failed: {e}", exc_info=True)
            return None

    @staticmethod
    def _topic_logger(run_dir: str | None, topic_name: str) -> logging.Logger:
        """Return a logger writing to <run_dir>/<topic_name>.log if run_dir set."""
        safe_name = topic_name.replace(" ", "_").replace("/", "_").lower()
        topic_log = logging.getLogger(f"course_scout.topic.{safe_name}")
        topic_log.setLevel(logging.DEBUG)
        # Avoid double-handler attachment on repeated calls within one run.
        if run_dir and not any(
            isinstance(h, logging.FileHandler)
            and getattr(h, "baseFilename", "").endswith(f"{safe_name}.log")
            for h in topic_log.handlers
        ):
            os.makedirs(run_dir, exist_ok=True)
            log_path = os.path.join(run_dir, f"{safe_name}.log")
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(
                logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            )
            topic_log.addHandler(fh)
        return topic_log
