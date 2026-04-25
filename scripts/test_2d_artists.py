"""Single-topic smoke test for 2D Artists Lounge.

Bypasses typer (which chokes on the negative channel ID) and runs the exact
same scan pipeline as `course-scout scan` but filtered to one task.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from course_scout.infrastructure.config import load_settings
from course_scout.infrastructure.logging_config import setup_logging
from course_scout.infrastructure.telegram import TelethonScraper
from course_scout.interfaces.cli.main import _scan_all_tasks


async def main() -> None:
    setup_logging(log_level=logging.DEBUG)
    settings = load_settings()

    target = "2D Artists Lounge"
    tasks = [t for t in settings.resolved_tasks if t.name == target]
    if not tasks:
        print(f"Task '{target}' not found. Available:")
        for t in settings.resolved_tasks:
            print(f"  - {t.name}")
        sys.exit(1)

    print(f"━━━ Isolated test: {target} ━━━")
    print(f"channel={tasks[0].channel_id} topic={tasks[0].topic_id}")

    scraper = TelethonScraper(
        settings.tg_api_id,
        settings.tg_api_hash,
        settings.session_path,
        phone=settings.phone_number,
        login_code=settings.login_code,
    )

    results = await _scan_all_tasks(scraper, settings, tasks, days=1, include_today=True)

    if not results:
        print("\nNo activity found.")
        return

    for name, digest, provider in results:
        print(f"\n─── {name} ───")
        print(f"items={len(digest.items) if hasattr(digest, 'items') else '?'}")
        if hasattr(provider, "usage"):
            print(provider.usage.summary())


if __name__ == "__main__":
    asyncio.run(main())
