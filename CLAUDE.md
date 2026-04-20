# CLAUDE.md — Course Scout

## Overview

Telegram art channel scanner. Fetches messages via Telethon, classifies via Claude Agent SDK with per-channel system prompts, ranks via a two-stage LLM pipeline (categorize → preference). Produces daily digest reports (markdown + PDF) with a Top 5 executive summary.

## Architecture

```
Stage 0  CLI (scan)
   ↓
Stage 1  Telethon Fetch (per topic, with 180s timeout)
   ↓
Stage 2  Token-aware chunking + model escalation
            haiku-assigned  → tries haiku → sonnet → opus
            sonnet-assigned → tries sonnet → opus
            (splits chunk only if biggest model can't fit)
   ↓
Stage 3  CATEGORIZE (LLM, parallel per topic)
            per-channel system prompt + 10 few-shot examples (BASE_OUTPUT_GUIDANCE)
            → {category, status} per item
   ↓
Stage 4  ROUTE (Python, deterministic)
            _reclassify_by_topic_name (Request topics → category=request)
            _assign_priority via (category, status) → HIGH/MEDIUM/LOW
   ↓
Stage 5  Programmatic link grounding (reject hallucinated IDs)
   ↓
Stage 6  PREFERENCE (LLM, single call)
            Sees ALL items with (category, priority, status) hints
            Picks Top 5 by Adam's interests (2D / character / anatomy / Asian art)
            Writes Summary paragraph for the rest
   ↓
Stage 7  Markdown + PDF report
```

- **Domain**: `TelegramMessage`, `ChannelDigest`, `DigestItem` (discriminated union: file / course / discussion / request / announcement), `LinkItem`
- **Infrastructure**: `TelethonScraper`, `ClaudeProvider` (Agent SDK), `OrchestratedSummarizer`, `PDFRenderer`
- **Interfaces**: CLI (`typer`), MCP (stdio), SSE server

## Key Commands

```bash
uv run course-scout scan              # Scan yesterday (all topics)
uv run course-scout scan --today      # Scan today (incomplete day)
uv run course-scout scan -d 3         # Last 3 complete days
uv run course-scout scan --no-pdf     # Markdown only
uv run course-scout digest <channel> -t <topic_id>  # Single topic
uv run course-scout list-topics <channel_id>         # List forum topics
```

## Config

- `config.yaml` — topics, agent defaults, prompt templates
- `.env` — Telegram credentials (`TG_API_ID`, `TG_API_HASH`, `PHONE_NUMBER`)
- Auth: Claude Agent SDK auto-detects Claude Max subscription via CLI
- **SDK pinned to 0.1.50** in `pyproject.toml`. Upgrades to 0.1.5x+ broke parser calls (uncovered new tools triggering `error_max_turns` despite `disallowed_tools`). Re-test before bumping.
- **Why Agent SDK**: We use the Agent SDK (not the raw `anthropic` API) because we authenticate via Claude Max subscription, not an API key. The Agent SDK piggybacks on the Claude Code CLI auth.
- **Security**: Built-in tools blocked via `disallowed_tools` (hard deny). Do NOT use `allowed_tools=[]` — that's a permission allowlist, not a restriction. Do NOT use `permission_mode="bypassPermissions"`. `max_turns=1` prevents agentic looping. `setting_sources=[]` prevents filesystem settings injection.

### Per-topic agent config

Each topic can override global defaults and use a specialized system prompt:
```yaml
agent_defaults:
  summarizer_model: "claude-sonnet-4-6"
  chunk_size: 10000      # token-aware chunking is primary; this is a safety cap
  max_messages: 100
  thinking: "adaptive"
  effort: "medium"

prompts:
  course_requests: |    # request channels — only category=request allowed
    ...
  file_sharing: |       # file-drop channels — file + discussion only
    ...
  discussion_lounge: |  # discussion channels — discussion + course + file
    ...
  course_review: |      # review channels — course + discussion only
    ...
  language_chat: |      # multilingual chats — all 5 categories
    ...

tasks:
  - name: "Coloso Requests"
    system_prompt: "course_requests"
    summarizer_model: "claude-haiku-4-5"   # cheap model for simple extraction
```

### Prompt Templates (in config.yaml `prompts:` section)

| Template | Used by | Allowed categories |
|----------|---------|--------------------|
| `course_requests` | All Request channels + GBUYB + Pan Baidu/Bilibili Download Request | request only |
| `file_sharing` | Members Collaboration | file + discussion |
| `discussion_lounge` | Asian Artists, 2D Artists, Webcomic | discussion + course + file |
| `course_review` | Course Review | course + discussion |
| `language_chat` | Russian, Spanish/Portuguese, Hindi/Urdu | all 5 |

**Every per-channel prompt is auto-prepended** with `BASE_OUTPUT_GUIDANCE` (`config.py`):
- Output schema reminders (no string-encoded lists, no trailing data)
- 10 worked input/output examples spanning fulfilled/unfulfilled requests, file shares, discussions, course reviews, SKIPs, interleaved threads, multi-thread channels, reply-across-gap, and stalled-thread patterns

### Token budgets and escalation

```python
_MODEL_BUDGETS = {
    "claude-haiku-4-5":  180_000,    # 200K context − 20K headroom
    "claude-sonnet-4-6": 950_000,    # 1M native, no beta header needed
    "claude-opus-4-7":   950_000,    # 1M native, no beta header
}
_ESCALATION = {
    "claude-haiku-4-5":  ["haiku", "sonnet", "opus"],
    "claude-sonnet-4-6": ["sonnet", "opus"],
    "claude-opus-4-7":   ["opus"],
}
```

`OrchestratedSummarizer.summarize()` estimates tokens (~3 chars/token), picks the smallest model in the chain whose budget fits, then chunks against THAT budget. Splitting only happens if even the top model can't fit.

## Pipeline (detailed)

1. **Fetch**: Telethon gets messages per topic with **180s timeout** (`TOPIC_FETCH_TIMEOUT_SEC` in `telegram.py`). On timeout, returns partial messages and continues — does NOT block the whole scan.
2. **Token-aware chunk**: see escalation above. Single call when fits, else greedy-pack into ≤budget chunks.
3. **Categorize**: Each chunk → Claude → `SummarizerOutputSchema`. Parallel across topics. Per-channel prompt + base guidance + few-shot examples.
4. **Python post-processing**:
   - `_reclassify_by_topic_name`: items in topics with "Request" / "Download" in name → forced category=request (defense against parser drift)
   - `_assign_priority`: deterministic priority from (category, status). LLM does not pick priority.
5. **Merge**: Combine chunk outputs (items, links).
6. **Ground**: Programmatic link validation — reject hallucinated IDs (>32-bit), verify URLs against raw messages.
7. **Preference / Executive Summary**: One Claude call sees ALL items, ranks Top 5 by Adam-relevance (LLM judgment), writes Summary for rest.
8. **Report**: Markdown + PDF with clickable links. Section headers use `[FILES]`, `[REQUESTS]`, `[DISCUSSION]`, etc. (no emojis).

## Failure-mode defenses

| Mode | Defense | Where |
|---|---|---|
| Telegram connection drops | 180s per-topic timeout, return partial | `telegram.py::TOPIC_FETCH_TIMEOUT_SEC` |
| Parser emits `{"items": "[...]"}` (string-encoded list) | JSON repair: parse string fields as JSON, truncate to last balanced bracket | `claude_provider.py::_repair_string_json_fields` |
| Topic name says "Request" but parser emits `file` | Python post-processing forces request category | `main.py::_reclassify_by_topic_name` |
| Parser sets wrong priority | Deterministic Python override | `main.py::_assign_priority` |
| Context overflow on big topics | Token-aware chunking + model escalation | `summarization.py::_pick_model` |

## Benchmark Setup (`benchmark/`)

Standalone dev tooling (not wired into main CLI). Two benches per pipeline stage:

```
benchmark/
├── sample.py                    # extract fixtures from logs/course_scout.log
│                                # --days 1|7|30 [--full-topic to merge same-topic-same-day chunks]
├── label.py                     # interactive labeler for CATEGORIZE
├── autolabel_categorize.py      # parser self-labels (smoke test for plumbing)
├── bench_categorize.py          # score category accuracy + Hungarian-matched set F1
├── bench_preference.py          # score Precision@5 on top-5 ranking
├── bench_sweep.py               # YAML-driven multi-config sweep, shared semaphore
├── compare_chunking.py          # chunked vs full-topic structural comparison
├── group_by.py                  # per-prompt accuracy slice
├── inspect_failures.py          # show thinking trace for failures
├── quick.py                     # one-shot autolabel + categorize eval
├── configs/default.yaml         # sweep config: models × efforts to test
├── fixtures/{1,7,30}d.jsonl     # parser-input chunks
├── fixtures/{1,7,30}d_full.jsonl  # one chunk = one topic's full day
├── labels/                      # hand-labeled ground truth (TODO)
├── results/                     # cached predictions + score reports
└── TODO.md                      # ground-truth labeling backlog
```

**Current bench status**: F1 = 0.878 on 1d (parser self-labels). Real ground-truth labeling pending — see `benchmark/TODO.md`.

**Concurrency**: 5 parallel calls (Anthropic Max plan guidance). Higher risks "concurrent connections" 429.

## Topics Scanned

| Category | Channels | Model | Prompt |
|----------|----------|-------|--------|
| Discussion lounges | Asian Artists, 2D Artists, Webcomic | sonnet-4-6 | discussion_lounge |
| Course review | Course Review | sonnet-4-6 | course_review |
| Language chats | Russian, Spanish/Portuguese, Hindi/Urdu | sonnet-4-6 | language_chat |
| Course requests | Coloso, 2D Related, Animation, Domestika, Class 101, Wingfox, Patreon, ALL REQUESTS | haiku-4-5 | course_requests |
| Download requests | Pan Baidu, Bilibili | haiku-4-5 | course_requests |
| File sources | Members Collaboration | sonnet-4-6 | file_sharing |
| External | GBUYB | haiku-4-5 | course_requests |

## Conventions

- No verifier agent — programmatic grounding replaces LLM verification
- Default scan = yesterday (midnight to midnight), `--today` for rolling
- PDF is default (use `--no-pdf` to skip)
- Per-topic logs in `logs/scans/YYYY-MM-DD_HHMMSS/<topic_name>.log`
- Reports saved to `reports/YYYY-MM-DD/scan_YYYY-MM-DD.{md,pdf}`
- Section headers in reports use bracket tags (`[FILES]`, `[REQUESTS]`, `[DISCUSSION]`) not emojis
- Priority is **deterministic** (Python from category+status). Parser MUST NOT set it — the schema field exists but the prompt instructs to leave null.

## Observability

Every Python override of parser output is logged to `logs/overrides.jsonl`:
```json
{"ts": "...", "stage": "allowlist|reclassify",
 "topic": "Coloso Requests", "before": "file", "after": "request",
 "title": "...", "reason": "..."}
```

Use this to audit when/why guardrails fire:
```bash
# How often does each stage fire?
jq -r .stage logs/overrides.jsonl | sort | uniq -c

# Which channels trigger the most remaps?
jq -r '[.topic, .stage, .before, .after] | @tsv' logs/overrides.jsonl | sort | uniq -c | sort -rn

# What does file_sharing over-emit?
jq 'select(.stage == "allowlist" and (.title | startswith("WLOP")))' logs/overrides.jsonl
```

If a guardrail fires frequently, fix the prompt upstream. If it never fires, consider deleting the guardrail.

## Bench Methodology (current limits & scoring)

Bench scores at **3 Hungarian thresholds** (60 / 75 / 90) to expose the title-match sensitivity. Also reports:

| Metric | What it measures | Game-ability |
|---|---|---|
| Set F1 @ threshold 60 | Loose match, legacy number | High — similar titles pair |
| Set F1 @ threshold 90 | Tight match, near-identical titles | Low |
| **Strict match F1** | title≥90 AND category correct | Lowest — the "really right" metric |
| Category acc on matched | Category correctness on pairs we found | **Biased up** when recall drops |
| Category acc over gold | Correct / total_gold (unmatched = miss) | Drops when recall drops — prefer this |

**Self-labels are a lower bound on self-agreement, not accuracy.** A consistently-wrong parser scores 1.0 on self-labels. Real F1 requires hand labels.

## Backlog

Prioritized. Items marked ⭐ have highest signal-to-effort ratio.

### Benchmark

- ⭐ **Hand-label 50 items of the 1d fixture** — only way out of the self-label trap (Husain/Shankar consensus). Deferred — labeling fatigue. Tracked in `benchmark/TODO.md`.
- ⭐ **Bootstrap CIs instead of point estimates** — n=13 gives F1 CI of ~[0.55, 0.95]. Per-slice metrics with n<20 should be dropped, not reported.
- **Track status + priority fields in scoring** — currently only `category` is scored. Regressions in status/priority are invisible.
- **Label preference bench** — `bench_preference.py` exists but needs pool items tagged as RELEVANT/MAYBE/IRRELEVANT.
- **Verify coalesced discussion consolidation** — 7d full-topic had 10 fewer `discussion` items vs chunked. Hypothesis: consolidation, not loss. Spot-check 2-3 samples.

### Pipeline hardening

- **Investigate SDK pin** — 0.1.51-0.1.63 breaks parser (new tools not in `disallowed_tools` cause `error_max_turns`). File upstream issue with repro. Unpin once fixed.
- **Pydantic `Extra data` errors on large outputs** — occasional failure mode on long coalesced inputs. Mitigated by `_repair_string_json_fields` but not eliminated.
- **Consider "assert" mode for dev** — when `_enforce_category_allowlist` fires, optionally raise instead of silently remap. Surfaces prompt bugs during bench runs; stays silent in prod.

### Deletable candidates (measure first, then delete)

Reviewer critique flagged these as possible dead code. Instrument via `logs/overrides.jsonl` first, then decide:
- **Model escalation** — personal scanner may never exceed Haiku 180K; if override log shows 0 escalations over a week, delete it.
- **5 prompts** — if override counters show low leakage between channels, test "1 unified prompt + `<channel_profile>` block" for token savings.

### Docker / NAS Deployment

- Dockerfile for scheduled scans on UGREEN NAS
- Cron-based daily scan with Telegram notification of results
- Session file persistence across container restarts

### Claude Agent SDK Enhancements

- **Custom `@tool` decorator** — vault search (course exists?), Calibre lookup (PDF downloaded?), NAS art course catalog cross-reference
- **MCP server integration** — connect `vault-mcp` and `arete` to Agent SDK for cross-referencing during summarization
- **Hooks system** — `PreToolUse` audit logging to replace manual per-topic file loggers
- **`max_budget_usd`** — hard cost cap per scan as safety net

### Output Improvements

- Cross-topic deduplication (same course requested in multiple topics)
- Historical tracking — compare today's scan to yesterday's, highlight new items only
- Cross-stage `trace_id` — track an item from parser output through every Python stage. Currently we can't answer "why did item X get priority HIGH?" without grepping.
