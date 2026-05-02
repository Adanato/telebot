"""Tests for TaskNotes Inbox publisher.

Verifies the published stub matches the user's TaskNotes plugin convention
(see .obsidian/plugins/tasknotes/data.json). Specifically:

- status: "1-inbox" — matches customStatuses[0].value, NOT the literal "inbox"
- priority: "normal" — matches customPriorities[2].value
- tags include "task" — matches taskTag (taskIdentificationMethod=tag)
- dateCreated preserved across re-runs (idempotent age stamp)
- PDF link uses skim:// so click opens in Skim, not Preview
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from course_scout.infrastructure.tasknotes import TaskNotesPublisher

_REPORT_MD = """# Course Scout Daily Scan — 2026-05-02

## Executive Summary

Strong day for art content. Pan Baidu drops + Krenz 2025 updates.

## Top 5 Finds

1. [FILE] **A Course** — One-line summary. *Topic: Test*
2. [FILE] **Another Course** — Two-line summary. *Topic: Test*
3. [DISCUSSION] **Some Talk** — Three-line summary. *Topic: Test*

## Summary

Other items follow.
"""


class TestTaskNotesPublisher(unittest.TestCase):
    def _publish(self, with_pdf: bool = True) -> tuple[Path, str]:
        """Publish a stub into a temp vault and return (stub_path, content)."""
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        vault = Path(tmp.name)
        (vault / "TaskNotes" / "Inbox").mkdir(parents=True)

        report_dir = vault / "reports" / "2026-05-02"
        report_dir.mkdir(parents=True)
        report_md = report_dir / "scan_2026-05-02.md"
        report_md.write_text(_REPORT_MD, encoding="utf-8")
        report_pdf = report_dir / "scan_2026-05-02.pdf" if with_pdf else None
        if report_pdf is not None:
            report_pdf.write_bytes(b"%PDF-fake")  # any non-empty content

        publisher = TaskNotesPublisher(vault_dir=vault)
        stub = publisher.publish(report_md, report_pdf)
        return stub, stub.read_text(encoding="utf-8")

    def test_status_value_matches_plugin_convention(self):
        """Frontmatter must say `status: 1-inbox`, not the literal `inbox`.

        The TaskNotes plugin filters Inbox views by exact match on the
        customStatuses[0].value — `1-inbox` — so writing `inbox` causes the
        stub to silently disappear from the kanban.
        """
        _stub, content = self._publish()
        self.assertIn("status: 1-inbox\n", content)
        self.assertNotIn("status: inbox\n", content)

    def test_priority_matches_plugin_convention(self):
        """Frontmatter `priority: normal` matches customPriorities[2].value."""
        _stub, content = self._publish()
        self.assertIn("priority: normal\n", content)

    def test_task_tag_present(self):
        """taskIdentificationMethod=tag → frontmatter must include `task` tag."""
        _stub, content = self._publish()
        self.assertIn("- task\n", content)

    def test_pdf_link_uses_skim_uri(self):
        """PDF link must be skim:// so click opens in Skim, not Preview/file://."""
        _stub, content = self._publish(with_pdf=True)
        self.assertIn("skim:///", content)
        # The skim:// link must be the markdown for the report PDF specifically
        self.assertIn(
            "[📄 Open full report in Skim →](skim:///",
            content,
        )

    def test_pdf_link_omitted_when_no_pdf(self):
        """If no PDF is supplied, the stub should not include a Skim link."""
        _stub, content = self._publish(with_pdf=False)
        self.assertNotIn("Open full report in Skim", content)
        # But the markdown link should still be present
        self.assertIn("Open full markdown", content)

    def test_idempotent_date_created(self):
        """Re-publishing on the same date preserves dateCreated; bumps dateModified."""
        # First publish
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        vault = Path(tmp.name)
        (vault / "TaskNotes" / "Inbox").mkdir(parents=True)
        report_dir = vault / "reports" / "2026-05-02"
        report_dir.mkdir(parents=True)
        report_md = report_dir / "scan_2026-05-02.md"
        report_md.write_text(_REPORT_MD, encoding="utf-8")

        publisher = TaskNotesPublisher(vault_dir=vault)
        stub1 = publisher.publish(report_md)
        text1 = stub1.read_text(encoding="utf-8")
        # Extract the dateCreated from the first run
        import re

        m1 = re.search(r"^dateCreated:\s*(\S+)", text1, re.MULTILINE)
        self.assertIsNotNone(m1)
        first_created = m1.group(1)

        # Second publish (force a new mtime)
        import time

        time.sleep(1.1)  # ensure dateModified differs
        publisher.publish(report_md)
        text2 = stub1.read_text(encoding="utf-8")
        m2 = re.search(r"^dateCreated:\s*(\S+)", text2, re.MULTILINE)
        m_mod = re.search(r"^dateModified:\s*(\S+)", text2, re.MULTILINE)
        self.assertEqual(m2.group(1), first_created, "dateCreated should be preserved")
        # dateModified should differ from dateCreated after the re-run
        self.assertNotEqual(m_mod.group(1), first_created)

    def test_finds_count_in_title(self):
        """Title gets the find count appended when finds parse cleanly."""
        _stub, content = self._publish()
        self.assertIn('title: "course-scout 2026-05-02 — 3 finds"', content)
