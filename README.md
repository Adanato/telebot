# Telebot

Telegram Channel Summarizer Bot using Telethon and Gemini AI.

[View the Roadmap](ROADMAP.md) for future agentic and MCP features.

## Setup

1. **Install dependencies**:
   ```bash
   uv sync
   ```

2. **Configure environment**:
   Copy `.env.example` to `.env` and fill in your credentials from [my.telegram.org](https://my.telegram.org/) and [Google AI Studio](https://aistudio.google.com/).

3. **Run the CLI**:
   ```bash
   uv run python -m telebot.interfaces.cli.main digest @channel_username
   ```

4. **Run the API Server**:
   ```bash
   uv run uvicorn telebot.interfaces.api.app:app --host 0.0.0.0 --port 8000
   ```

5. **Authentication**:
   - The bot uses `PHONE_NUMBER` and `LOGIN_CODE` from `.env` for initial session creation.
   - The API uses `API_TOKEN` for simple header-based authentication.

## Architecture (DDA)

- **Domain**: Core models and service interfaces.
- **Application**: Orchestration logic (Use Cases).
- **Infrastructure**: Concrete implementations (Telethon, Gemini).
- **Interfaces**: CLI entry point.

## Quality Assurance

- **Linting**: `uv run ruff check .`
- **Type Checking**: `uv run pyright`
- **DDA Verification**: `uv run lint-imports`
- **Tests**: `uv run pytest`
