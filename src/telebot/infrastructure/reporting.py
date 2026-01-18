import os
import markdown
import logging
try:
    from weasyprint import HTML, CSS
    WEASYPRINT_AVAILABLE = True
except (ImportError, OSError):
    WEASYPRINT_AVAILABLE = False

from telebot.domain.models import ChannelDigest

logger = logging.getLogger(__name__)

class PDFRenderer:
    def __init__(self, output_dir: str = "reports"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def render(self, digest: ChannelDigest, filename: str = "digest.pdf") -> str:
        """
        Converts the ChannelDigest (with Markdown content) into a PDF.
        """
        if not WEASYPRINT_AVAILABLE:
            error_msg = "WeasyPrint is not installed or system dependencies are missing. Cannot generate PDF."
            logger.error(error_msg)
            return error_msg
        
        # 1. Convert Markdown Sections to HTML
        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: sans-serif; padding: 20px; }}
                h1 {{ color: #2c3e50; border-bottom: 2px solid #2c3e50; padding-bottom: 10px; }}
                h2 {{ color: #34495e; margin-top: 20px; }}
                h3 {{ color: #7f8c8d; }}
                ul {{ line-height: 1.6; }}
                li {{ margin-bottom: 5px; }}
                img {{ max-width: 100%; border-radius: 5px; margin: 10px 0; }}
                .action-items {{ background-color: #fce4ec; padding: 15px; border-radius: 5px; }}
                .key-links {{ background-color: #e3f2fd; padding: 15px; border-radius: 5px; }}
            </style>
        </head>
        <body>
            <h1>{digest.channel_name} - {digest.date}</h1>
        """
        
        md = markdown.Markdown(extensions=['extra', 'nl2br'])
        
        for summary_part in digest.summaries:
            # Check if this part has image references (local paths)
            # For now, we assume the Formatter might not embed them directly yet, 
            # but we can append a gallery if we had a list of images.
            # Ideally the Formatter puts ![img](path) in the markdown.
            
            html_part = md.convert(summary_part)
            html_content += f"<div>{html_part}</div>"

        # Explicit Action Items Section
        if digest.action_items:
            html_content += "<div class='action-items'><h2>🚀 Action Items</h2><ul>"
            for item in digest.action_items:
                html_content += f"<li>{item}</li>"
            html_content += "</ul></div>"

        # KEY LINKS Section
        if digest.key_links:
             html_content += "<div class='key-links'><h2>🔗 Key Links</h2><ul>"
             for link in digest.key_links:
                 html_content += f"<li><a href='{link}'>{link}</a></li>"
             html_content += "</ul></div>"

        html_content += "</body></html>"

        # 2. Write PDF
        output_path = os.path.join(self.output_dir, filename)
        HTML(string=html_content).write_pdf(output_path)
        
        return output_path
