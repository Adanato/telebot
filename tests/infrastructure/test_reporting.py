import os
import unittest
from datetime import datetime
from unittest.mock import patch

from telebot.domain.models import ChannelDigest
from telebot.infrastructure.reporting import PDFRenderer


class TestPDFRenderer(unittest.TestCase):
    def setUp(self):
        self.output_dir = "test_reports_unit"
        self.renderer = PDFRenderer(output_dir=self.output_dir)
        self.digest = ChannelDigest(
            channel_name="Test Channel",
            date=datetime(2025, 1, 1).date(),
            summaries=["# Title\nSummary 1", "## Section\nSummary 2"],
            action_items=["Action 1"],
            key_links=[{"title": "Link", "url": "https://test.com"}],
        )

    def tearDown(self):
        if os.path.exists(self.output_dir):
            for f in os.listdir(self.output_dir):
                os.remove(os.path.join(self.output_dir, f))
            try:
                os.rmdir(self.output_dir)
            except OSError:
                pass

    @patch("telebot.infrastructure.reporting.MarkdownPdf")
    @patch("telebot.infrastructure.reporting.Section")
    def test_render_success(self, MockSection, MockMarkdownPdf):
        mock_pdf = MockMarkdownPdf.return_value
        filename = "test_report.pdf"

        path = self.renderer.render(self.digest, filename=filename)

        self.assertIn(filename, path)
        self.assertTrue(mock_pdf.add_section.called)
        self.assertTrue(mock_pdf.save.called)
        # Check that we split by headers
        # Our digest has summaries starting with # and ##
        # re.split should find them.
        self.assertGreaterEqual(mock_pdf.add_section.call_count, 1)

    @patch("telebot.infrastructure.reporting.MarkdownPdf")
    def test_render_error_handling(self, MockMarkdownPdf):
        mock_pdf = MockMarkdownPdf.return_value
        mock_pdf.save.side_effect = Exception("Disk Full")

        path = self.renderer.render(self.digest, filename="crash.pdf")

        self.assertIn("Error:", path)
        self.assertIn("Disk Full", path)

    def test_section_splitting_logic(self):
        # Test the regex splitting directly via render_from_markdown with a patch
        with patch("telebot.infrastructure.reporting.MarkdownPdf") as MockPdf:
            mock_pdf = MockPdf.return_value
            text = "# Header 1\nContent 1\n## Header 2\nContent 2"
            self.renderer.render_from_markdown(text, "test.pdf")

            # Should have called add_section twice for the two headers
            self.assertEqual(mock_pdf.add_section.call_count, 2)
