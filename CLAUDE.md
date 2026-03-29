# CLAUDE.md — Course Scout

## Overview

Telegram art channel scanner. Fetches messages via Telethon, summarizes via Claude Agent SDK, grounds links programmatically. Produces daily digest reports (markdown + PDF) with a ranked Top 5 executive summary.

## Architecture

```
CLI (scan) → Telethon Fetch → Chunk → Claude Summarize (parallel) → Programmatic Grounding → Executive Summary → PDF Report
```

- **Domain**: `TelegramMessage`, `ChannelDigest`, `DigestItem`, `LinkItem`
- **Infrastructure**: `TelethonScraper`, `ClaudeProvider` (Agent SDK), `OrchestratedSummarizer`, `PDFRenderer`
- **Interfaces**: CLI (`typer`), MCP (stdio), SSE server

## Key Commands

```bash
uv run course-scout scan              # Scan yesterday (all 25 topics)
uv run course-scout scan --today      # Scan today (incomplete day)
uv run course-scout scan -d 3         # Scan last 3 complete days
uv run course-scout scan --no-pdf     # Markdown only, skip PDF
uv run course-scout digest <channel> -t <topic_id>  # Single topic digest
uv run course-scout list-topics <channel_id>         # List forum topics
```

## Config

- `config.yaml` — topics, agent defaults, prompt templates, global settings
- `.env` — Telegram credentials (`TG_API_ID`, `TG_API_HASH`, `PHONE_NUMBER`)
- Auth: Claude Agent SDK auto-detects Claude Max subscription via CLI
- **Why Agent SDK**: We use the Agent SDK (not the raw `anthropic` API) because we authenticate via Claude Max subscription, not an API key. The Agent SDK piggybacks on the Claude Code CLI auth.
- **Security**: All built-in tools are blocked via `disallowed_tools` (hard deny). Do NOT use `allowed_tools=[]` — that's just a permission allowlist, not a restriction. Do NOT use `permission_mode="bypassPermissions"` — it auto-approves any tool not explicitly denied. `max_turns=1` prevents agentic looping. `setting_sources` is left unset (safe default — prevents filesystem settings injection).

### Per-topic agent config

Each topic can override global defaults and use a specialized system prompt:
```yaml
agent_defaults:
  summarizer_model: "claude-sonnet-4-6"
  chunk_size: 25
  max_messages: 100
  thinking: "adaptive"
  effort: "medium"

prompts:
  course_requests: |
    You summarize course REQUEST threads...
  file_sharing: |
    You summarize FILE SHARING threads...

tasks:
  - name: "Coloso Requests"
    system_prompt: "course_requests"  # references prompts section
    effort: "high"
  - name: "Russian Chat"
    system_prompt: "language_chat"
    chunk_size: 50
    effort: "low"
```

### Prompt Templates (in config.yaml `prompts:` section)

| Template | Used by | Focus |
|----------|---------|-------|
| `course_requests` | Coloso, Domestika, Class 101, Wingfox, Patreon, ALL REQUESTS, GBUYB | Extract course title, instructor, platform, URL, fulfillment status |
| `file_sharing` | Pan Baidu, Bilibili, Illustration & Painting, Underpaint, Members Collab | Extract download links, passwords, file formats, re-upload requests |
| `language_chat` | Russian, Korean, Japanese, Spanish/Portuguese, Persian, Hindi/Urdu | Translate to English, extract course mentions, group buys, tips |
| `discussion_lounge` | Asian Artists, 2D Artists, Webcomic, Group-buy Chit Chat | Technique discussions, style identification, tool tips, references |
| `course_review` | Course Review | Extract ratings, skill level, instructor, key takeaways |

## Pipeline

1. **Fetch**: Telethon gets messages per topic (capped at `max_messages`, 100 default)
2. **Chunk**: Split into batches of `chunk_size` (25 default)
3. **Summarize**: Each chunk → Claude (Sonnet) → `SummarizerOutputSchema` (parallel across topics)
4. **Merge**: Combine chunk outputs (items, links, actions)
5. **Ground**: Programmatic link validation — reject hallucinated IDs (>32-bit), verify URLs against raw messages
6. **Executive Summary**: Claude generates ranked Top 5 finds + summary paragraph
7. **Report**: Markdown + PDF with clickable links

## Executive Summary Ranking

The Top 5 ranks by actionability (what Adam can use today):
1. Downloadable files — courses, lesson videos, art resources with links/passwords
2. Course reviews with ratings — helps decide what to study
3. Technique discussions — actionable art tips, workflow breakdowns
4. Community resources — spreadsheets, tool links, guides, artist lists
5. Group buy activity — only if actively organizing

Unfulfilled requests are mentioned in the Summary section but never ranked in Top 5.

## Topics Scanned (25)

| Category | Topics | Effort |
|----------|--------|--------|
| Discussion Lounges | Asian Artists, 2D Artists, Webcomic, Course Review | medium |
| Language Chats | Russian, Korean, Japanese, Spanish/Portuguese, Persian, Hindi/Urdu | low (Russian), medium (rest) |
| Course Requests | Coloso, 2D, Animation, Domestika, Class 101, Wingfox, Patreon, ALL REQUESTS | high (Coloso), medium (rest) |
| File Sources | Pan Baidu, Bilibili, Illustration & Painting, Underpaint, Members Collab | high (Pan Baidu), medium (rest) |
| External | GBUYB | medium |

## Usage Profile (per scan)

- ~11 API calls (1 per active topic + 1 executive summary)
- ~25K output tokens
- ~7 min wall time (parallel summarization)
- ~2.5% of 5-hour Claude Max window
- ~0.5% of daily budget
- Can run ~45x/day before hitting limits

## Conventions

- No verifier agent — programmatic grounding replaces LLM verification
- Min 3 messages to trigger summarization (skip noise)
- Default scan = yesterday (midnight to midnight), `--today` for rolling
- PDF is default output (use `--no-pdf` to skip)
- Per-topic logs in `logs/scans/YYYY-MM-DD_HHMMSS/<topic_name>.log`
- Reports saved to `reports/YYYY-MM-DD/scan_YYYY-MM-DD.{md,pdf}`
- Usage summary printed after each scan (tokens, cost, Max plan budget %)

## TODO

### Docker / NAS Deployment
- Dockerfile for running scheduled scans on UGREEN NAS
- Cron-based daily scan with Telegram notification of results
- Session file persistence across container restarts

### Claude Agent SDK Enhancements
- **Custom `@tool` decorator** — vault search (check if course exists), Calibre lookup (check if PDF downloaded), NAS art course catalog cross-reference
- **MCP server integration** — connect `vault-mcp` and `arete` to Agent SDK for cross-referencing during summarization
- **Multi-agent `agents` param** — specialized sub-agents per content type instead of prompt templates
- **Hooks system** — `PreToolUse` audit logging to replace manual per-topic file loggers
- **`ClaudeSDKClient` session reuse** — persistent connection to avoid subprocess respawn (blocked by per-query system_prompt changes)
- **`betas: ["context-1m"]`** — 1M context for high-volume topics (500+ messages)
- **`max_budget_usd`** — hard cost cap per scan as safety net

### Output Improvements
- Deduplication across chunks when a discussion spans chunk boundaries
- Cross-topic deduplication (same course requested in multiple topics)
- Historical tracking — compare today's scan to yesterday's, highlight new items only
