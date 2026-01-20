from markdown_pdf import MarkdownPdf, Section

pdf = MarkdownPdf()
markdown_text = """
# Link Test
This is a [clickable link](https://google.com).
- [Another link](https://t.me/c/1603660516/166550/488602)
"""
pdf.add_section(Section(markdown_text))
pdf.save("test_links.pdf")
print("PDF saved to test_links.pdf")
