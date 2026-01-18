# Telebot Roadmap

Future features and architectural improvements for the Telegram Channel Summarizer Bot.

## Phase 1: Agentic Capabilities (Next)

### [ ] MCP Server Integration
Expose the Telegram scraper as a **Model Context Protocol (MCP)** server.
- Allows AI agents (Claude Desktop, etc.) to use your Telegram as a data source.
- Implement specialized MCP tools for "Search Messages" and "Get Recent Topics".

### [ ] Tool-Equipped Summarization (Skills)
Give the bot "Skills" to improve its summaries.
- **Search Skill**: Automatically look up news/links mentioned in chats.
- **Notification Skill**: Trigger SMS/Email alerts for high-priority keywords.

## Phase 2: Enhanced Scraping

### [ ] Media Support
Summarize non-text content.
- Image-to-text (using Gemini Multimodal) for diagrams or screenshots.
- Voice-to-text (Whisper/Gemini) for voice messages.

### [ ] Multi-Account Support
Orchestrate scraping across multiple sessions for larger-scale monitoring.

## Phase 3: Advanced Digests

### [ ] Custom Templates
Allow users to define custom Markdown templates for their digests (e.g., "Bullet points only", "Narrative style", "Executive Summary").

### [ ] Sentiment & Trend Analysis
Track sentiment trends over time for specific topics or keywords.
