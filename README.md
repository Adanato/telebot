# Course Scout

Telegram art channel scanner that generates daily digests with programmatically grounded links and PDF reports. Pre-configured for Course Busters and GBUYB art communities.

## Key Features

- **3-Stage Agent Pipeline** (Claude-powered):
  - **Summarizer Agent** (Sonnet): Extracts courses, files, discussions with source anchoring
  - **Verifier Agent** (Haiku): Fact-checks against raw data for accuracy
  - **Programmatic Grounding**: Ensures every link exists in source data
- **Art Interest Filter**: Prioritizes 2D illustration, anatomy, color theory, character design
- **Active Link Repair**: Queries Telegram via Telethon to verify/fix message IDs
- **Forum/Topic Support**: Scans specific forum topics in supergroups
- **PDF Reports**: Clickable, verified deep-links
- **MCP Interface**: Exposes tools for external AI agents

## Architecture (DDA)

- **Domain**: Core models (`TelegramMessage`, `ChannelDigest`) and service interfaces
- **Application**: Business logic orchestrating scrapers and summarizers
- **Infrastructure**: Telethon scraper, Claude provider, PDF rendering
- **Interfaces**: CLI, MCP (stdio), SSE server

## Setup

1. **Install Dependencies**:
   ```bash
   uv sync
   ```

2. **Configuration**:
   Create a `.env` file:
   ```env
   TG_API_ID=...
   TG_API_HASH=...
   ANTHROPIC_API_KEY=...
   PHONE_NUMBER=...
   ```

## Usage

### CLI
```bash
# Generate a digest for a channel topic
uv run course-scout digest -1001603660516 --topic 166550 --days 1

# List forum topics
uv run course-scout list-topics -1001603660516
```

### MCP Server
```bash
uv run python -m course_scout.interfaces.mcp.main
```

### Worker (batch all tasks from config.yaml)
```bash
uv run course-scout-worker
```

## Tests
```bash
uv run pytest
uv run ruff check src/
```
