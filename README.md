# Telebot

A resilient Telegram Channel Summarizer that generates high-quality daily digests with programmatically grounded links and professional PDF reports.

## 🚀 Key Features

- **Orchestrated Summarization**: A robust 3-stage agent pipeline:
  - **Summarizer Agent**: Extracts key discussions, files, and links with source anchoring.
  - **Verifier Agent**: Fact-checks the summary against raw data for contextual accuracy.
  - **Programmatic Grounding**: Ensures every link exists in the source data.
- **Active Link Repair**: Automatically queries Telegram via Telethon to verify/fix IDs that aren't in the initial scraping batch.
- **Forum/Topic Support**: Direct support for forum-enabled channels (supergroups).
- **Professional PDF Reports**: Generates PDFs with stable header hierarchies and clickable, verified deep-links.
- **MCP Interface**: Exposes core functionality (digest generation, topic listing) as Model Context Protocol (MCP) tools for external AI agents.

## 🏛️ Architecture (DDA)

The project follows a strict **Domain-Driven Architecture**:
- **Domain**: Core models (`TelegramMessage`, `ChannelDigest`) and service interfaces.
- **Application**: Business logic orchestrating scrapers and summarizers.
- **Infrastructure**: Concrete implementations (Telethon, Gemini/Groq providers, PDF rendering).
- **Interfaces**:
  - `CLI`: Standard command-line tool.
  - `MCP`: Stdio-based server for AI agents.

## 🛠️ Setup

1. **Install Dependencies**:
   ```bash
   uv sync
   ```

2. **Configuration**:
   Create a `.env` file with these keys:
   ```env
   TG_API_ID=...
   TG_API_HASH=...
   GEMINI_API_KEY=...
   GROQ_API_KEY=... # Optional
   PHONE_NUMBER=... # For Telegram auth
   LOGIN_CODE=...   # Optional
   ```

## 📖 Usage

### Command Line Interface
```bash
# Generate a digest for a channel
uv run telebot digest @channel_name --pdf --provider groq

# Generate a digest for a specific forum topic
uv run telebot digest -t 123456 -100... --pdf
```

### MCP Server (for AI Agents)
Telebot exposes an MCP server using `stdio`. To interface with it from another agent project (like Orion):
```bash
uv run python -m telebot.interfaces.mcp.main
```
**Available Tools**:
- `generate_digest`: Parameters: `channel_id`, `topic_id`, `lookback_days`, `provider`, `pdf`.
- `list_topics`: Parameter: `channel_id`.

## Assurance
- **Logs**: Persistent logs are saved in `logs/telebot.log`.
- **Tests**: `uv run pytest`
- **Linting**: `uv run ruff check .`
