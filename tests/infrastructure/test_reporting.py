import unittest
from unittest.mock import MagicMock, patch
import telebot.infrastructure.reporting as reporting_module
from telebot.infrastructure.reporting import PDFRenderer
from telebot.domain.models import ChannelDigest
from datetime import date

class TestPDFRenderer(unittest.TestCase):
    def test_render_unavailable(self):
        """Test graceful failure when WeasyPrint is missing."""
        # Force unavailable
        reporting_module.WEASYPRINT_AVAILABLE = False
        
        renderer = PDFRenderer()
        digest = ChannelDigest(
            channel_name="Test",
            date=date(2023,1,1),
            summaries=[]
        )
        
        result = renderer.render(digest, "out.pdf")
        self.assertIn("not installed", result)

    def test_render_success(self):
        """Test PDF generation when WeasyPrint is available."""
        # Mock HTML class and consistency
        mock_html_cls = MagicMock()
        mock_html_inst = mock_html_cls.return_value
        
        # Inject into module
        reporting_module.WEASYPRINT_AVAILABLE = True
        reporting_module.HTML = mock_html_cls
        
        renderer = PDFRenderer()
        digest = ChannelDigest(
            channel_name="Test Channel",
            date=date(2023, 1, 1),
            summaries=["# Title"],
            action_items=["Action 1"]
        )
        
        filename = "test_output.pdf"
        result_path = renderer.render(digest, filename)
        
        self.assertTrue(result_path.endswith(filename))
        mock_html_cls.assert_called()
        
        # Check content
        call_args = mock_html_cls.call_args
        self.assertIn("Test Channel", call_args[1]['string'])
        self.assertIn("Action 1", call_args[1]['string'])
