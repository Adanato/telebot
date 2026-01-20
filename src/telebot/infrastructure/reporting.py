import os
import logging
from markdown_pdf import MarkdownPdf, Section
from datetime import datetime
from telebot.domain.models import ChannelDigest
import re


logger = logging.getLogger(__name__)

class PDFRenderer:
    def __init__(self, output_dir: str = "reports"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def render(self, digest: ChannelDigest, filename: str) -> str:
        """
        Render a ChannelDigest to a PDF using the markdown-pdf library.
        """
        return self.render_from_markdown(digest.to_markdown(), filename)

    def render_from_markdown(self, markdown_text: str, filename: str) -> str:
        """Render a raw Markdown string to a professional PDF report using markdown-pdf."""
        output_path = os.path.join(self.output_dir, filename)
        
        # Helper to normalize headers to avoid hierarchy errors in pymupdf (no skipping levels)
        def normalize_headers(md: str) -> str:
            lines = md.split('\n')
            current_max_level = 0
            new_lines = []
            for line in lines:
                match = re.match(r'^(#+)\s+(.*)', line)
                if match:
                    hashes, title = match.groups()
                    level = len(hashes)
                    # Force row 1 to be level 1 if it's the first header
                    if current_max_level == 0:
                        new_level = 1
                    else:
                        # Cannot be more than current_max_level + 1
                        new_level = min(level, current_max_level + 1)
                    
                    current_max_level = max(current_max_level, new_level)
                    new_lines.append(f"{'#' * new_level} {title}")
                else:
                    new_lines.append(line)
            return '\n'.join(new_lines)

        markdown_text = normalize_headers(markdown_text)
        
        try:
            pdf = MarkdownPdf()
            pdf.meta["title"] = "Daily Digest Report"
            pdf.meta["author"] = "Telebot"
            
            # --- Splitting into Sections ---
            # Each # or ## header should ideally start a new section (new page)
            
            # Split by # or ## headers while keeping the headers in the strings
            # This regex looks for lines starting with one or two #
            sections_raw = re.split(r'(?m)^(#{1,2}\s.*)$', markdown_text)
            
            clean_sections = []
            current_section = ""
            
            for part in sections_raw:
                if not part.strip():
                    continue
                
                # If it's a header line
                if re.match(r'^#{1,2}\s', part):
                    if current_section.strip():
                        clean_sections.append(current_section.strip())
                    current_section = part
                else:
                    current_section += "\n" + part
            
            if current_section.strip():
                clean_sections.append(current_section.strip())

            # --- Premium CSS ---
            css = """
            body { 
                font-family: 'Helvetica', 'Arial', sans-serif; 
                color: #2c3e50; 
                line-height: 1.6; 
                padding: 10px;
            }
            h1 { 
                color: #2c3e50; 
                text-align: center; 
                border-bottom: 3px solid #3498db; 
                padding-bottom: 10px;
                margin-bottom: 20px;
                font-size: 24px;
            }
            h2 { 
                color: #2980b9; 
                border-bottom: 1px solid #bdc3c7; 
                padding-bottom: 5px; 
                margin-top: 25px;
                margin-bottom: 10px;
                font-size: 18px;
            }
            p { margin-bottom: 10px; text-align: justify; }
            a { color: #3498db; text-decoration: underline; font-weight: bold; }
            ul { padding-left: 20px; margin-bottom: 15px; }
            li { margin-bottom: 5px; }
            img { 
                max-width: 100%; 
                margin: 15px auto; 
                display: block; 
                border-radius: 6px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            strong { color: #34495e; }
            code { 
                background-color: #f1f3f5; 
                padding: 2px 4px; 
                border-radius: 4px; 
                font-family: monospace; 
                font-size: 0.9em;
                color: #e74c3c;
            }
            """
            
            # Add each part as a separate section (page break between them)
            # If we only have one section, we just add it.
            if not clean_sections:
                logger.debug("No sections found, adding entire text as a single section.")
                pdf.add_section(Section(markdown_text, toc=False, root='.'), user_css=css)
            else:
                logger.debug(f"Adding {len(clean_sections)} sections to PDF.")
                for idx, sect_text in enumerate(clean_sections):
                    logger.debug(f"Adding section {idx + 1}/{len(clean_sections)} (Length: {len(sect_text)})")
                    # Include the first section in TOC to ensure hierarchy starts at level 1
                    pdf.add_section(Section(sect_text, toc=True, root='.'), user_css=css)
            
            logger.debug(f"Saving PDF to {output_path}...")
            pdf.save(output_path)
            
            logger.info(f"PDF successfully generated at {output_path} with {len(clean_sections)} sections")
            return output_path
            
        except Exception as e:
            logger.error(f"Failed to generate PDF from Markdown using markdown-pdf: {e}", exc_info=True)
            return f"Error generating PDF: {str(e)}"
