import asyncio
import logging
import os
from datetime import datetime

from telebot.application.digest import GenerateDigestUseCase
from telebot.infrastructure.config import load_settings
from telebot.infrastructure.logging_config import setup_logging
from telebot.infrastructure.notifier import TelethonNotifier
from telebot.infrastructure.reporting import PDFRenderer
from telebot.infrastructure.summarization import OrchestratedSummarizer
from telebot.infrastructure.telegram import TelethonScraper

logger = logging.getLogger(__name__)


class TelebotWorker:
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
            gemini_key=self.settings.gemini_api_key,
            groq_key=self.settings.groq_api_key,
            provider=self.settings.preferred_provider,
            scraper=self.scraper,
        )

        self.renderer = PDFRenderer()
        self.use_case = GenerateDigestUseCase(self.scraper, self.summarizer)

        self.notifier = TelethonNotifier(
            self.settings.tg_api_id,
            self.settings.tg_api_hash,
            self.settings.session_path,
            default_peer=self.settings.tg_notify_target,
        )

    async def run_task(self, task: dict):
        name = task.get("name", "Unnamed Task")
        channel_id = task.get("channel_id")
        topic_id = task.get("topic_id")
        actions = task.get("actions", ["summarize", "notify"])

        logger.info(f"🚀 Starting task: {name} (Channel: {channel_id}, Topic: {topic_id})")

        try:
            # Execute Digest
            digest = await self.use_case.execute(
                channel_id, topic_id=topic_id, lookback_days=self.settings.lookback_days
            )

            if not digest:
                logger.info(f"ℹ️ No new messages for task: {name}. Skipping.")
                return

            summary_md = digest.to_markdown()

            # Save local report
            report_base = f"digest_{name.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}"
            os.makedirs("reports", exist_ok=True)

            # Notifications
            if "notify" in actions and self.notifier:
                # 1. Send text summary
                await self.notifier.send_message(f"🔔 *Daily Digest: {name}*\n\n{summary_md}")

                # 2. Handle PDF if requested
                if self.settings.report_format == "pdf":
                    pdf_path = self.renderer.render(digest, f"{report_base}.pdf")
                    await self.notifier.send_document(pdf_path, caption=f"📄 PDF Report: {name}")

            logger.info(f"✅ Completed task: {name}")

        except Exception as e:
            logger.error(f"❌ Error executing task {name}: {e}", exc_info=True)

    async def start(self):
        logger.info("🤖 Telebot Worker started.")

        # Initial run on startup if configured
        if self.settings.tasks:
            logger.info(f"Found {len(self.settings.tasks)} tasks. Running initial batch...")
            for task in self.settings.tasks:
                await self.run_task(task)

        # Keep alive/Scheduler placeholder
        # For now, we'll just wait indefinitely
        while True:
            await asyncio.sleep(3600)  # Check every hour (simplified)
            # In a more advanced version, we'd use a real scheduler like APScheduler


def main():
    worker = TelebotWorker()
    asyncio.run(worker.start())


if __name__ == "__main__":
    main()
