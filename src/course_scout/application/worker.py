import asyncio
import json
import logging
import os
import time
import traceback
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from course_scout.application.digest import GenerateDigestUseCase
from course_scout.infrastructure.config import load_settings
from course_scout.infrastructure.logging_config import setup_logging
from course_scout.infrastructure.notifier import TelethonNotifier
from course_scout.infrastructure.persistence import SqliteReportRepository
from course_scout.infrastructure.reporting import PDFRenderer
from course_scout.infrastructure.runtime import get_runtime
from course_scout.infrastructure.summarization import OrchestratedSummarizer
from course_scout.infrastructure.telegram import TelethonScraper

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _runtime_log(log_path: str, run_label: str = "scan"):
    """Append one JSON line per run to `log_path`.

    Captures: start/end timestamps, total duration, exit status, error+traceback
    if any. Used to wrap `CourseScoutWorker.start()` so silent crashes leave a
    trail. Logging failures are swallowed — never let logging crash the worker.
    """
    started_at = datetime.now(UTC).isoformat()
    start_t = time.monotonic()
    error: str | None = None
    tb: str | None = None
    try:
        yield
    except BaseException as e:
        error = f"{type(e).__name__}: {e}"
        tb = traceback.format_exc()
        raise
    finally:
        ended_at = datetime.now(UTC).isoformat()
        duration_s = round(time.monotonic() - start_t, 2)
        entry = {
            "label": run_label,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_s": duration_s,
            "exit_status": "failed" if error else "ok",
            "error": error,
            "traceback": tb,
        }
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as log_err:
            logger.warning(f"Could not write runtime log to {log_path}: {log_err}")


class CourseScoutWorker:
    def __init__(self, config_path: str = "config.yaml"):
        """Initialize the background worker."""
        self.settings = load_settings(config_path)
        setup_logging()

        self.scraper = TelethonScraper(
            self.settings.tg_api_id,
            self.settings.tg_api_hash,
            self.settings.session_path,
            phone=self.settings.phone_number,
            login_code=self.settings.login_code,
        )

        self.summarizer = OrchestratedSummarizer(
            summarizer_model=self.settings.agent_defaults.summarizer_model,
            scraper=self.scraper,
        )

        self.renderer = PDFRenderer()
        self.use_case = GenerateDigestUseCase(self.scraper, self.summarizer)

        self.notifier = TelethonNotifier(
            self.settings.tg_api_id,
            self.settings.tg_api_hash,
            self.settings.session_path,
            default_peer=self.settings.tg_notify_target,
            bot_token=self.settings.telegram_bot_token,
        )

        self.repository = SqliteReportRepository()

    async def run_task(self, task: dict):
        name = task.get("name", "Unnamed Task")
        channel_id = task.get("channel_id")
        topic_id = task.get("topic_id")
        actions = task.get("actions", ["summarize", "notify"])

        logger.info(f"🚀 Starting task: {name} (Channel: {channel_id}, Topic: {topic_id})")

        if channel_id is None:
            logger.error(f"Task {name} missing channel_id; skipping.")
            return

        pdf_path: str | None = None

        try:
            # Execute Digest
            digest = await self.use_case.execute(
                channel_id,
                topic_id=topic_id,
                lookback_days=self.settings.lookback_days,
                timezone=self.settings.timezone,
                window_mode=self.settings.window_mode,
            )

            if not digest:
                logger.info(f"ℹ️ No new messages for task: {name}. Skipping.")
                return

            summary_md = digest.to_markdown()

            # Save local reports
            today_str = datetime.now().strftime("%Y-%m-%d")
            report_dir = os.path.join("reports", today_str)
            os.makedirs(report_dir, exist_ok=True)

            report_base = f"digest_{name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}"

            # 1. Save Markdown
            md_path = os.path.join(report_dir, f"{report_base}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(summary_md)
            logger.info(f"📝 Markdown report saved: {md_path}")

            # Notifications
            if "notify" in actions and self.notifier:
                # 1. Send text summary
                await self.notifier.send_message(f"🔔 *Daily Digest: {name}*\n\n{summary_md}")

                # 2. Render PDF (saved to disk/db, but not sent to Telegram)
                pdf_path = None
                if self.settings.report_format == "pdf":
                    pdf_path = self.renderer.render(
                        digest, f"{report_base}.pdf", output_dir=report_dir
                    )
                    # We no longer send the PDF to Telegram per user request
                    # await self.notifier.send_document(pdf_path, caption=f"📄 PDF Report: {name}")

            # Persist to Database
            self.repository.add_report(
                date=digest.date,
                channel_id=str(channel_id),
                task_name=name,
                md_path=md_path,
                pdf_path=pdf_path if self.settings.report_format == "pdf" else None,
                summary="\n".join(digest.summaries),
            )

            logger.info(f"✅ Completed and persisted task: {name}")

        except Exception as e:
            logger.error(f"❌ Error executing task {name}: {e}", exc_info=True)

    async def start(self):
        logger.info("🤖 Course Scout Worker started.")
        log_path = get_runtime().log_path

        async with _runtime_log(log_path, run_label="batch"):
            # Initial run on startup if configured
            if self.settings.tasks:
                today_str = datetime.now().strftime("%Y-%m-%d")
                logger.info(f"Found {len(self.settings.tasks)} tasks. Running initial batch...")

                # Send Batch Start Delimiter
                if self.notifier:
                    await self.notifier.send_message(
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📅 *BATCH START: {today_str}*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━"
                    )

                for task in self.settings.tasks:
                    await self.run_task(task)

                # Send Batch End Delimiter
                if self.notifier:
                    await self.notifier.send_message(
                        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"✅ *BATCH COMPLETE: {today_str}*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━"
                    )

        # Keep alive/Scheduler placeholder
        while True:
            await asyncio.sleep(3600)


def main():
    worker = CourseScoutWorker()
    asyncio.run(worker.start())


if __name__ == "__main__":
    main()
