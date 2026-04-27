"""Cross-run dedup of CourseItem / FileItem in digest output.

Telegram art channels routinely re-share old course drops and re-link
existing files. Without dedup, the daily digest re-surfaces the same
items week after week. This module persists URL + filename signals to
SQLite and filters items whose signals are all already seen.

Conversational categories (discussion, request, announcement) are NOT
deduped — a new thread about an old course is still useful information.
"""

import logging
import os
import re
import sqlite3
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from course_scout.domain.models import ChannelDigest

logger = logging.getLogger(__name__)

_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "ref",
    "ref_src",
    "_ga",
}

_EXT_RE = re.compile(
    r"\.(zip|rar|7z|tar|gz|tgz|pdf|epub|mobi|azw3|mp4|mkv|mov|webm|m4v)$",
    re.IGNORECASE,
)


def normalize_url(url: str | None) -> str | None:
    """Canonicalize a URL for stable cross-run comparison.

    - lowercases scheme + netloc, strips leading "www."
    - drops fragment and known tracking query params
    - sorts remaining query params for stability
    - strips a trailing slash from non-root paths

    Returns None for empty/unparseable input.
    """
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    try:
        p = urlparse(url)
        if not p.netloc:
            return None
        netloc = p.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        path = p.path or ""
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")
        kept = sorted(
            (k, v)
            for k, v in parse_qsl(p.query, keep_blank_values=True)
            if k.lower() not in _TRACKING_PARAMS
        )
        query = urlencode(kept)
        scheme = p.scheme.lower() or "https"
        return urlunparse((scheme, netloc, path, "", query, ""))
    except Exception:
        return None


def normalize_filename(name: str | None) -> str | None:
    """Canonicalize a filename for stable comparison.

    Lowercases, strips a known archive/media extension, collapses runs
    of non-alphanumeric chars into single underscores. Returns None if
    the result is empty.
    """
    if not name or not isinstance(name, str):
        return None
    base = name.strip().lower()
    base = _EXT_RE.sub("", base)
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    return base or None


class SeenItemRepository:
    """Persists URLs and filenames surfaced in prior digests."""

    def __init__(self, db_path: str = "data/reports.db"):
        """Initialize the repository with the specified database path."""
        self.db_path = db_path
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_links (
                    url TEXT PRIMARY KEY,
                    first_seen_at TEXT NOT NULL,
                    first_channel TEXT,
                    first_title TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_files (
                    file_key TEXT PRIMARY KEY,
                    first_seen_at TEXT NOT NULL,
                    first_channel TEXT,
                    first_title TEXT
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def is_link_seen(self, normalized_url: str) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM seen_links WHERE url = ? LIMIT 1",
                (normalized_url,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def is_file_seen(self, normalized_name: str) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM seen_files WHERE file_key = ? LIMIT 1",
                (normalized_name,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def mark_link_seen(self, normalized_url: str, channel: str, title: str) -> None:
        ts = datetime.now(UTC).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO seen_links "
                "(url, first_seen_at, first_channel, first_title) VALUES (?, ?, ?, ?)",
                (normalized_url, ts, channel, title),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_file_seen(self, normalized_name: str, channel: str, title: str) -> None:
        ts = datetime.now(UTC).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO seen_files "
                "(file_key, first_seen_at, first_channel, first_title) VALUES (?, ?, ?, ?)",
                (normalized_name, ts, channel, title),
            )
            conn.commit()
        finally:
            conn.close()

    def stats(self) -> dict[str, int]:
        conn = self._connect()
        try:
            links = conn.execute("SELECT COUNT(*) FROM seen_links").fetchone()[0]
            files = conn.execute("SELECT COUNT(*) FROM seen_files").fetchone()[0]
            return {"links": links, "files": files}
        finally:
            conn.close()


_DEDUP_CATEGORIES = frozenset({"course", "file"})


class DigestDeduper:
    """Filter previously-seen course/file items from a ChannelDigest in place.

    An item is novel if at least one of these signals is unseen:
      - any external (non-t.me) URL it references
      - (FileItem only) its normalized title

    Items with no identifying signals are kept (we can't dedup blind).
    Surviving items have all their signals recorded so future runs see them.
    """

    def __init__(self, channel_name: str, repo: SeenItemRepository | None = None):
        """Initialize with channel context and an optional shared repository."""
        self.channel_name = channel_name
        self.repo = repo or SeenItemRepository()

    def filter(self, digest: ChannelDigest) -> int:
        kept = []
        dropped = 0
        for item in digest.items:
            if item.category not in _DEDUP_CATEGORIES:
                kept.append(item)
                continue
            if self._is_novel(item):
                kept.append(item)
                self._mark_signals(item)
            else:
                dropped += 1
                logger.debug(
                    "[dedup] dropping seen %s: %r",
                    item.category,
                    getattr(item, "title", None),
                )
        digest.items = kept
        return dropped

    @staticmethod
    def _external_urls(item: Any) -> list[str]:
        return [u for u in (getattr(item, "links", None) or []) if u and "t.me/" not in u]

    def _is_novel(self, item: Any) -> bool:
        normalized_urls = [n for u in self._external_urls(item) if (n := normalize_url(u))]
        url_novel = any(not self.repo.is_link_seen(n) for n in normalized_urls)

        file_key: str | None = None
        if item.category == "file":
            file_key = normalize_filename(getattr(item, "title", ""))
        file_novel = bool(file_key) and not self.repo.is_file_seen(file_key)  # type: ignore[arg-type]

        if not normalized_urls and not file_key:
            return True
        return url_novel or file_novel

    def _mark_signals(self, item: Any) -> None:
        title = getattr(item, "title", "") or ""
        for u in self._external_urls(item):
            n = normalize_url(u)
            if n:
                self.repo.mark_link_seen(n, self.channel_name, title)
        if item.category == "file":
            file_key = normalize_filename(title)
            if file_key:
                self.repo.mark_file_seen(file_key, self.channel_name, title)
