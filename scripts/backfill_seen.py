"""Backfill seen_files / seen_links from existing daily reports.

Walks ~/NAS/course-scout/reports/YYYY-MM-DD/scan_*.md, parses the per-channel
[FILES] sections, and marks every file title + every external (non-t.me) URL
ever surfaced as "seen" in data/reports.db.

After running, future scans that re-encounter the same file title or external
URL will treat it as a duplicate (per `infrastructure/dedup.py::DigestDeduper`)
and only conversational categories (discussion, request, announcement) will
surface for those items.

Usage:
    cd ~/NAS/course-scout && uv run python scripts/backfill_seen.py
    cd ~/NAS/course-scout && uv run python scripts/backfill_seen.py --dry-run
    cd ~/NAS/course-scout && uv run python scripts/backfill_seen.py -v
"""

from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path

from course_scout.infrastructure.dedup import (
    SeenItemRepository,
    normalize_filename,
    normalize_url,
)

DEFAULT_REPORTS_DIR = Path.home() / "NAS" / "course-scout" / "reports"
DEFAULT_DB_PATH = Path.home() / "NAS" / "course-scout" / "data" / "reports.db"

# `## 📌 Channel Name` heading
_CHANNEL_RE = re.compile(r"^##\s*📌\s*(.+?)\s*$")
# `## [FILES] Files Shared` (or any `## [TAG]` header — we only flip files-state on FILES)
_SECTION_RE = re.compile(r"^##\s+\[(\w+)\]")
# `- 🔥 **Title** — ...` (status emoji optional). Consumes leading bullet only.
_FILE_RE = re.compile(r"^-\s+(?:🔥|⏳|✅|⏸)?\s*\*\*(.+?)\*\*")
# Any markdown link `[label](https://...)` within a line
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def parse_report(md_path: Path):
    """Stream ``(kind, …)`` tuples extracted from one report.

    Yields either ``("file", title, channel)`` or ``("link", url, channel, title)``.
    Tracks the most-recent channel header and file title so URLs that appear
    in continuation lines are attributed to the right item.
    """
    current_channel = "unknown"
    current_title = ""
    in_files_section = False

    for raw in md_path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()

        m = _CHANNEL_RE.match(line)
        if m:
            current_channel = m.group(1).strip()
            in_files_section = False
            current_title = ""
            continue

        m = _SECTION_RE.match(line)
        if m:
            in_files_section = m.group(1).upper() == "FILES"
            current_title = ""
            continue

        m = _FILE_RE.match(line)
        if m:
            current_title = m.group(1).strip()
            if in_files_section:
                yield ("file", current_title, current_channel)

        for lm in _LINK_RE.finditer(line):
            url = lm.group(2)
            if "t.me/" in url:
                continue  # internal Telegram link — dedup ignores these
            yield ("link", url, current_channel, current_title or "(untitled)")


def main() -> None:  # noqa: C901 — argparse + dispatch; refactor is a separate task
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    ap.add_argument("--dry-run", action="store_true", help="Parse + report counts without writing")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    md_files = sorted(args.reports_dir.glob("*/scan_*.md"))
    if not md_files:
        logging.error("No reports found in %s", args.reports_dir)
        return
    logging.info("Found %d report(s) under %s", len(md_files), args.reports_dir)

    # First-occurrence wins (matches mark_*_seen which uses INSERT OR IGNORE).
    file_keys: dict[str, tuple[str, str]] = {}
    link_keys: dict[str, tuple[str, str]] = {}

    for md in md_files:
        per_file = 0
        per_link = 0
        for record in parse_report(md):
            if record[0] == "file":
                _, title, channel = record
                key = normalize_filename(title)
                if key and key not in file_keys:
                    file_keys[key] = (title, channel)
                    per_file += 1
            elif record[0] == "link":
                _, url, channel, title = record
                n = normalize_url(url)
                if n and n not in link_keys:
                    link_keys[n] = (title, channel)
                    per_link += 1
        logging.debug("%s: +%d files +%d links", md.parent.name, per_file, per_link)

    logging.info(
        "Parsed: %d unique file titles, %d unique non-t.me URLs", len(file_keys), len(link_keys)
    )

    if args.dry_run:
        logging.info("--dry-run: not writing to %s", args.db)
        # Show 10 examples of each
        sample_files = list(file_keys.items())[:10]
        sample_links = list(link_keys.items())[:10]
        if sample_files:
            logging.info("Sample file keys:")
            for k, (title, _ch) in sample_files:
                logging.info("  %s  ←  %s", k, title)
        if sample_links:
            logging.info("Sample link URLs:")
            for url, (title, _ch) in sample_links:
                logging.info("  %s  ←  %s", url, title)
        return

    repo = SeenItemRepository(str(args.db))
    before = repo.stats()

    inserted_files = 0
    inserted_links = 0
    for key, (title, channel) in file_keys.items():
        if not repo.is_file_seen(key):
            repo.mark_file_seen(key, channel, title)
            inserted_files += 1
    for url, (title, channel) in link_keys.items():
        if not repo.is_link_seen(url):
            repo.mark_link_seen(url, channel, title)
            inserted_links += 1

    after = repo.stats()
    logging.info(
        "Inserted: %d files (DB %d → %d), %d links (DB %d → %d)",
        inserted_files,
        before["files"],
        after["files"],
        inserted_links,
        before["links"],
        after["links"],
    )


if __name__ == "__main__":
    main()
