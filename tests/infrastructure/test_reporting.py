import unittest
import os
from datetime import datetime
from unittest.mock import patch
from telebot.infrastructure.reporting import PDFRenderer
from telebot.domain.models import ChannelDigest

class TestPDFRenderer(unittest.TestCase):
    def setUp(self):
        self.output_dir = "test_reports_unit"
        self.renderer = PDFRenderer(output_dir=self.output_dir)
        self.digest = ChannelDigest(
            channel_name="Test Channel",
            date=datetime.now().date(),
            summaries=["Summary 1", "Summary 2"],
            action_items=["Action 1"],
            key_links=["https://test.com"]
        )

    def tearDown(self):
        if os.path.exists(self.output_dir):
            for f in os.listdir(self.output_dir):
                os.remove(os.path.join(self.output_dir, f))
            os.rmdir(self.output_dir)

    def test_render_success(self):
        filename = "test_report.pdf"
        path = self.renderer.render(self.digest, filename=filename)
        
        self.assertTrue(os.path.exists(path))
        self.assertIn(filename, path)
        self.assertGreater(os.path.getsize(path), 0)

    def test_render_error_handling(self):
        # Mock FPDF.output to simulate a disk/permission error
        with patch("telebot.infrastructure.reporting.FPDF.output") as mock_output:
            mock_output.side_effect = Exception("Disk Full")
            path = self.renderer.render(self.digest, filename="crash.pdf")
            self.assertIn("Error generating PDF", path)
            self.assertIn("Disk Full", path)
