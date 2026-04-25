import asyncio
import os
from datetime import UTC, datetime

import typer

from course_scout.application.digest import GenerateDigestUseCase
from course_scout.domain.models import ChannelDigest
from course_scout.infrastructure.config import Settings, load_settings
from course_scout.infrastructure.logging_config import setup_logging
from course_scout.infrastructure.persistence import SqliteReportRepository
from course_scout.infrastructure.reporting import PDFRenderer
from course_scout.infrastructure.summarization import OrchestratedSummarizer
from course_scout.infrastructure.telegram import TelethonScraper

# Removed local Settings definition, uses infrastructure.config.load_settings()

app = typer.Typer()


@app.command()
def resolve_channel_id(channel_raw: str) -> str | int:
    """Resolve a channel alias or string ID to a proper Telegram peer."""
    try:
        if channel_raw.startswith("-") and channel_raw[1:].isdigit():
            return int(channel_raw)
        if channel_raw.isdigit():
            return int(channel_raw)
    except ValueError:
        pass

    aliases = {
        "coursebusters": -1001603660516,
        "course busters": -1001603660516,
    }
    key = channel_raw.lstrip("@").lower()
    return aliases.get(key, channel_raw)


async def _resolve_topic_by_name(scraper: TelethonScraper, channel_id: str | int, name: str) -> int:
    """Find a topic ID by its title in a forum channel."""
    topics = await scraper.list_topics(channel_id)
    search_lower = name.lower()
    matches = [t for t in topics if search_lower in t["title"].lower()]
    if not matches:
        return 0
    exact = next((t for t in matches if t["title"].lower() == search_lower), None)
    target = exact or matches[0]
    return target["id"]


async def _handle_digest_delivery(
    result: ChannelDigest,
    channel: str,
    topic: str | None,
    pdf: bool,
    send_to: str | None,
    email: str | None,
    settings: Settings,
):
    """Handle the various output and delivery options for a digest."""
    # Console Output
    typer.echo(f"\n--- Digest for {channel} ({result.date}) ---\n")
    typer.echo(result.to_markdown())

    # Path Setup
    today_str = datetime.now().strftime("%Y-%m-%d")
    report_dir = os.path.join("reports", today_str)
    os.makedirs(report_dir, exist_ok=True)

    # Markdown File
    md_filename = f"digest_{topic or channel}_{result.date}.md"
    md_path = os.path.join(report_dir, md_filename)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(result.to_markdown())
    typer.echo(f"\n📝 Markdown Report generated: {md_path}")

    # PDF & Telegram
    pdf_path = None
    if pdf or send_to:
        renderer = PDFRenderer()
        filename = f"digest_{topic or channel}_{result.date}.pdf"
        pdf_path = renderer.render(result, filename=filename, output_dir=report_dir)
        typer.echo(f"📄 PDF Report generated: {pdf_path}")

        if send_to:
            typer.echo(f"\n📨 Sending PDF to {send_to}...")
            from typing import Any

            from telethon import TelegramClient

            client: Any = TelegramClient(
                settings.session_path, settings.tg_api_id, settings.tg_api_hash
            )
            await client.connect()
            try:
                try:
                    _ = int(send_to)
                except ValueError:
                    pass
                # We no longer send the PDF to Telegram per user request
                # await client.send_file(peer, pdf_path, caption=f"Digest for {channel}")
                typer.echo("ℹ️ PDF generated but not sent to Telegram (Markdown only mode).")
            except Exception as e:
                typer.echo(f"❌ Failed to send: {e}")
            finally:
                await client.disconnect()

    if email:
        typer.echo(f"\n📧 Sending email to {email}... (Feature Coming Soon)")

    # Persist to Database
    repository = SqliteReportRepository()
    repository.add_report(
        date=result.date,
        channel_id=str(channel),
        task_name=topic or channel,
        md_path=md_path,
        pdf_path=pdf_path,
        summary="\n".join(result.summaries),
    )
    typer.echo("🗄️ Report metadata saved to database.")


@app.command()
def digest(
    channel: str,
    topic: str | None = typer.Option(None, "--topic", "-t", help="Topic ID or Name"),
    days: int = typer.Option(1, "--days", "-d", help="Days to look back"),
    pdf: bool = typer.Option(False, "--pdf", help="Generate a PDF report"),
    send_to: str | None = typer.Option(None, "--send-to", help="User/Chat to notify"),
    email: str | None = typer.Option(None, "--email", help="Email the report"),
    today: bool = typer.Option(False, "--today", help="Summarize from 12 AM today"),
):
    """Generate a daily digest for a Telegram channel or specific Topic."""
    setup_logging()
    settings = load_settings()
    channel_id = resolve_channel_id(channel)
    scraper = TelethonScraper(
        settings.tg_api_id,
        settings.tg_api_hash,
        settings.session_path,
        phone=settings.phone_number,
        login_code=settings.login_code,
    )

    resolved_topic_id = None
    if topic:
        if topic.isdigit():
            resolved_topic_id = int(topic)
        else:
            typer.echo(f"Resolving topic '{topic}' in {channel_id}...")
            resolved_topic_id = asyncio.run(_resolve_topic_by_name(scraper, channel_id, topic))
            if not resolved_topic_id:
                typer.echo(f"❌ Topic '{topic}' not found.")
                raise typer.Exit(code=1)
            typer.echo(f"✅ Resolved to Topic ID: {resolved_topic_id}")

    summarizer = OrchestratedSummarizer(
        summarizer_model=settings.agent_defaults.summarizer_model,
        scraper=scraper,
    )
    use_case = GenerateDigestUseCase(scraper, summarizer)
    result = asyncio.run(
        use_case.execute(
            channel_id,
            topic_id=resolved_topic_id,
            lookback_days=days,
            timezone=settings.timezone,
            window_mode=settings.window_mode,
            today_only=today,
        )
    )  # type: ignore

    if not result:
        typer.echo(f"ℹ️ No new messages found for {channel} in the last {days} days.")
        return

    asyncio.run(_handle_digest_delivery(result, channel, topic, pdf, send_to, email, settings))


def _setup_run_logs():
    """Create a per-run log directory and return the path.

    Lives under the same root as the main log file (default /tmp/course-scout,
    overridable via COURSE_SCOUT_LOG_DIR). This way you can `tail -f` both the
    main log and any per-topic log from the same parent directory.
    """
    from course_scout.infrastructure.logging_config import DEFAULT_LOG_DIR

    run_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    root = os.environ.get("COURSE_SCOUT_LOG_DIR", DEFAULT_LOG_DIR)
    run_dir = os.path.join(root, "scans", run_id)
    os.makedirs(run_dir, exist_ok=True)
    return run_dir


def _add_topic_logger(run_dir, topic_name):
    """Add a file handler for a specific topic, return the logger."""
    import logging

    safe_name = topic_name.replace(" ", "_").replace("/", "_").lower()
    log_path = os.path.join(run_dir, f"{safe_name}.log")
    topic_logger = logging.getLogger(f"course_scout.topic.{safe_name}")
    topic_logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    topic_logger.addHandler(fh)
    return topic_logger


async def _fetch_all_topics(scraper, tasks, start_date, end_date):
    """Fetch messages for all topics sequentially via Telethon."""
    from course_scout.infrastructure.config import ResolvedTaskConfig

    fetched: dict[str, tuple[ResolvedTaskConfig, list]] = {}
    for task in tasks:
        name = task.name
        try:
            messages = await scraper.get_messages(
                task.channel_id, start_date, end_date=end_date, topic_id=task.topic_id
            )
            # Cap at per-topic max_messages
            messages = messages[: task.max_messages]
            if len(messages) >= 3:
                fetched[name] = (task, messages)
                typer.echo(f"   📨 {name}: {len(messages)} messages")
            elif messages:
                typer.echo(f"   ⏭️  {name}: {len(messages)} messages (skipped, <3)")
            else:
                typer.echo(f"   ⏭️  {name}: no messages")
        except Exception as e:
            typer.echo(f"   ❌ {name}: fetch error — {e}")
    return fetched


async def _scan_all_tasks(scraper, settings, tasks, days, include_today=False):  # noqa: C901
    """Scan all tasks: fetch messages sequentially, then summarize in parallel."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)

    if include_today:
        # Rolling window from N days ago to now
        start_date = now - timedelta(days=days)
        end_date = now
    else:
        # Fixed complete days: yesterday midnight to today midnight
        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = today_midnight
        start_date = today_midnight - timedelta(days=days)

    start_str = start_date.strftime("%Y-%m-%d %H:%M")
    end_str = end_date.strftime("%Y-%m-%d %H:%M")
    typer.echo(f"📅 Window: {start_str} → {end_str}")

    run_dir = _setup_run_logs()
    typer.echo(f"📁 Run logs: {run_dir}/")

    # Phase 1: Fetch
    typer.echo("📡 Fetching messages from all topics...")
    fetched = await _fetch_all_topics(scraper, tasks, start_date, end_date)

    if not fetched:
        return []

    # Phase 2: Summarize all topics in parallel (each gets its own summarizer + logger)
    active = len(fetched)
    typer.echo(f"\n🧠 Summarizing {active} topics in parallel...")

    async def _summarize_one(name, task, messages):
        topic_log = _add_topic_logger(run_dir, name)
        topic_log.info(
            f"Starting: {len(messages)} msgs, topic={task.topic_id}, "
            f"model={task.summarizer_model}, thinking={task.thinking}, "
            f"effort={task.effort}, chunk_size={task.chunk_size}"
        )
        try:
            summarizer = OrchestratedSummarizer(
                summarizer_model=task.summarizer_model,
                system_prompt=task.system_prompt,
                thinking=task.thinking,
                effort=task.effort,
                chunk_size=task.chunk_size,
                scraper=scraper,
                include_media=task.include_media,
            )
            digest = await summarizer.summarize(messages, topic_id=task.topic_id)
            if digest:
                _enforce_category_allowlist(digest, task.system_prompt_name, topic_name=name)
                _reclassify_by_topic_name(digest, name)
                _assign_priority(digest)

                # Pin-diff: run against cache, prepend to summaries if changed.
                # Swallowed errors can't break the main scan.
                from course_scout.infrastructure.pins import diff_and_record

                try:
                    pin_md = await diff_and_record(scraper, task.channel_id, task.topic_id)
                    if pin_md:
                        digest.summaries.insert(0, pin_md)
                        topic_log.info("Pin changes detected and injected into summary")
                except Exception as e:
                    topic_log.warning(f"Pin diff failed: {e}")

                msg_count = len(digest.items)
                topic_log.info(f"Completed: {msg_count} items extracted")
                typer.echo(f"   ✅ {name}: {msg_count} items")
                # Log usage to topic file
                provider = (
                    list(summarizer.orchestrator._providers.values())[0]
                    if summarizer.orchestrator._providers
                    else None
                )  # noqa: E501
                usage = getattr(provider, "usage", None)
                if usage is not None:
                    for call in usage.calls:
                        topic_log.info(
                            f"  {call['model']}: {call['input_tokens']} in / "
                            f"{call['output_tokens']} out / {call['duration_ms']}ms"
                        )
                return (name, digest, provider)
        except Exception as e:
            topic_log.error(f"Failed: {e}", exc_info=True)
            typer.echo(f"   ❌ {name}: {e}")
        return None

    coros = [_summarize_one(name, task, msgs) for name, (task, msgs) in fetched.items()]
    raw_results = await asyncio.gather(*coros)

    # Collect results and merge usage stats
    results = []
    for r in raw_results:
        if r is not None:
            name, digest, provider = r
            results.append((name, digest, provider))

    return results


# Override audit log. Every time a Python post-processor rewrites the LLM's output,
# we record (stage, topic, before_category, after_category, title). Written to
# logs/overrides.jsonl for offline inspection. See `_override_log()`.
_OVERRIDE_LOG_PATH = "logs/overrides.jsonl"


def _override_log(
    stage: str,
    topic: str,
    before: str,
    after: str | None,
    title: str,
    reason: str = "",
) -> None:
    """Append a single override event to the JSONL audit log."""
    import json
    from datetime import datetime
    from pathlib import Path

    path = Path(_OVERRIDE_LOG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "stage": stage,
                    "topic": topic,
                    "before": before,
                    "after": after,  # None if item was dropped
                    "title": title[:120],
                    "reason": reason,
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def _reclassify_by_topic_name(digest, topic_name: str) -> None:
    """Force all actionable items into RequestItem when the topic is a Requests feed.

    Parser LLMs often promote a fulfilled request (with download link) to category=file,
    even when the source topic is "Coloso Requests" / "Domestika Requests" / etc. The
    topic framing is ground truth: a request topic contains requests, not files. We
    rewrite the category deterministically and dedupe by title.

    Discussions are left alone — those can legitimately be meta-discussions in a
    request topic (e.g. "how do group buys work").
    """
    from course_scout.domain.models import RequestItem

    if not any(k in topic_name.lower() for k in ("request", "download")):
        return

    new_items = []
    seen_titles: set[str] = set()
    for item in digest.items:
        if item.category == "discussion":
            new_items.append(item)
            continue
        # Dedupe: if we already kept an item with this title, skip
        key = item.title.strip().lower()
        if key in seen_titles:
            _override_log(
                "reclassify", topic_name, item.category, None, item.title, "duplicate title"
            )
            continue
        seen_titles.add(key)
        if item.category == "request":
            new_items.append(item)
        else:
            _override_log(
                "reclassify",
                topic_name,
                item.category,
                "request",
                item.title,
                "request-topic → request",
            )
            # Convert FileItem/CourseItem/AnnouncementItem → RequestItem
            data = item.model_dump(exclude={"category"})
            new_items.append(RequestItem(**data))
    digest.items = new_items


# Category allowlist per prompt family. Maps YAML prompt name → allowed categories.
# If the parser emits a category outside this set (e.g. file_sharing emits
# `course`), the item is remapped to the nearest allowed category — or dropped
# if nothing fits. Enforces the prompt restriction in Python, since prompt-only
# enforcement leaks at semantic boundaries (e.g. Gumroad + media attachments).
_PROMPT_ALLOWED_CATEGORIES: dict[str, set[str]] = {
    "course_requests": {"request"},
    "file_sharing": {"file", "discussion"},
    "discussion_lounge": {"discussion", "course", "file", "request"},
    "course_review": {"course", "discussion"},
    "language_chat": {"file", "course", "discussion", "request", "announcement"},
}

# Remap rules for each allowlist violation. If the parser emits X in a channel
# that allows Y, convert X → Y (preserving all other fields). Order-sensitive:
# earlier fallbacks preferred. `None` = drop the item entirely.
_CATEGORY_REMAP: dict[tuple[str, str], str | None] = {
    # file_sharing: no courses, no requests, no announcements
    (
        "file_sharing",
        "course",
    ): "file",  # storefront-like items → file (likely has a download in context)  # noqa: E501
    ("file_sharing", "request"): None,  # drop — re-upload asks are noise here
    ("file_sharing", "announcement"): "discussion",  # community news → discussion
    # discussion_lounge: requests allowed; only announcement gets remapped
    ("discussion_lounge", "announcement"): "discussion",
    # course_review: only course + discussion
    ("course_review", "file"): "course",
    ("course_review", "request"): None,
    ("course_review", "announcement"): "discussion",
}


def _enforce_category_allowlist(
    digest, system_prompt_name: str | None, topic_name: str = ""
) -> None:  # noqa: E501
    """Drop/remap items whose category is outside the prompt's allowlist.

    Runtime guardrail — prompt restrictions leak at semantic boundaries (e.g. a
    Gumroad link + media attachments in a file_sharing channel makes the model
    uncertain between `file` and `course`). This enforces the restriction in
    Python after the parser returns. Every remap/drop is logged to
    `logs/overrides.jsonl` for audit.
    """
    if system_prompt_name is None:
        return
    allowed = _PROMPT_ALLOWED_CATEGORIES.get(system_prompt_name)
    if allowed is None:
        return

    from course_scout.domain.models import (
        AnnouncementItem,
        CourseItem,
        DiscussionItem,
        FileItem,
        RequestItem,
    )

    category_to_cls = {
        "course": CourseItem,
        "file": FileItem,
        "discussion": DiscussionItem,
        "request": RequestItem,
        "announcement": AnnouncementItem,
    }

    new_items = []
    for item in digest.items:
        if item.category in allowed:
            new_items.append(item)
            continue
        # Violation — look up remap
        remap_target = _CATEGORY_REMAP.get((system_prompt_name, item.category))
        if remap_target is None:
            _override_log(
                "allowlist",
                topic_name,
                item.category,
                None,
                item.title,
                f"{system_prompt_name} drops {item.category}",
            )
            continue
        target_cls = category_to_cls[remap_target]
        try:
            data = item.model_dump(exclude={"category"})
            new_items.append(target_cls(**data))
            _override_log(
                "allowlist",
                topic_name,
                item.category,
                remap_target,
                item.title,
                f"{system_prompt_name} remaps {item.category}→{remap_target}",
            )
        except Exception as e:
            _override_log(
                "allowlist", topic_name, item.category, None, item.title, f"remap failed: {e}"
            )
            continue
    digest.items = new_items


# Deterministic priority from (category, status). The parser emits priority too,
# but we OVERWRITE it here — priority is a pipeline routing signal, not LLM judgment.
_PRIORITY_MAP: dict[tuple[str, str | None], str] = {
    ("file", None): "HIGH",
    ("course", "FULFILLED"): "HIGH",
    ("course", None): "MEDIUM",
    ("discussion", None): "MEDIUM",
    ("request", "FULFILLED"): "MEDIUM",
    ("request", None): "LOW",
    ("announcement", None): "LOW",
}


def _assign_priority(digest) -> None:
    """Overwrite each item's priority based on (category, status). Deterministic."""
    for item in digest.items:
        cat = item.category
        status = getattr(item, "status", None)
        # Try exact (cat, status) first, fall back to (cat, None)
        priority = _PRIORITY_MAP.get((cat, status)) or _PRIORITY_MAP.get((cat, None))
        if priority is not None:
            item.priority = priority


async def _generate_executive_summary(all_results, date_str):
    """Generate a personalized executive summary from all topic digests.

    Stage 3 of the pipeline: the LLM sees EVERY item (with category + priority +
    status hints) and preference-ranks top-5 by Adam's personal interests. Unlike
    the earlier deterministic ranker, this lets the LLM elevate a relevant COURSE
    or REQUEST above a generic FILE when it matches Adam's focus areas.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        TextBlock,
        query,
    )

    flat = [(item, name) for name, result in all_results for item in result.items]

    def _fmt(item, topic):
        cat = item.category.upper()
        status = getattr(item, "status", None)
        priority = item.priority or ""
        links = f" | Links: {', '.join(item.links[:2])}" if item.links else ""
        meta = " · ".join(filter(None, [priority, status]))
        meta_str = f" ({meta})" if meta else ""
        return f"- [{cat}]{meta_str} {item.title} | topic={topic}: {item.description[:200]}{links}"

    items_block = "\n".join(_fmt(i, t) for i, t in flat) if flat else "(none)"

    prompt = f"""Today's ({date_str}) scan results from art community Telegram channels.
Pick the 5 items most relevant to Adam and write a summary.

Each item has: [CATEGORY] (PRIORITY · STATUS) title | topic=source: description

CATEGORY semantics:
- [FILE] = downloadable file/archive/link shared in-chat
- [COURSE] = course recommendation, review, or shared course
- [DISCUSSION] = technique discussion, debate, tool comparison
- [REQUEST] = someone asking for a resource
- [ANNOUNCEMENT] = community news

PRIORITY is a routing hint (HIGH/MEDIUM/LOW) — derived deterministically from
category + fulfillment. Use it as background, not as a hard ranker. A MEDIUM
item on Adam's focus area beats a HIGH item that's off-topic.

=== ALL ITEMS ===
{items_block}

Adam's interests (use these for preference ranking):
- 2D illustration, character design, concept art
- Anatomy, figure drawing, gesture
- Color theory, lighting, rendering
- Asian artists, anime/manga styles, webtoon
- Courses: Coloso, Schoolism, CGMA, Domestika, Proko
De-prioritize: 3D, game dev, photography, UI/UX, motion graphics (unless directly applicable).

FORMAT (use this exact structure):

## Top 5 Finds

1. [TAG] **Exact item name** — one line on why it matters to Adam. *Topic: {{source}}*
2. [TAG] **Exact item name** — one line on why it matters to Adam. *Topic: {{source}}*
3. [TAG] **Exact item name** — one line on why it matters to Adam. *Topic: {{source}}*
4. [TAG] **Exact item name** — one line on why it matters to Adam. *Topic: {{source}}*
5. [TAG] **Exact item name** — one line on why it matters to Adam. *Topic: {{source}}*

Pick based on Adam-relevance, not category alone. [REQUEST] items can make top-5
if the content genuinely interests him (e.g., a requested Krenz course).

## Summary

1-2 paragraphs covering the remaining items. Flag time-sensitive items
(expiring links, group buys closing, new course drops, approaching deadlines)."""

    options = ClaudeAgentOptions(
        model="claude-haiku-4-5",
        system_prompt="You write concise executive summaries for daily art community digests.",
        max_turns=1,
        permission_mode="bypassPermissions",
        effort="low",
        thinking={"type": "disabled"},
    )

    last_text = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    last_text = block.text

    if last_text:
        return f"## Executive Summary\n\n{last_text}"
    return "## Executive Summary\n\n*Summary generation failed.*"


def _output_combined_report(all_results, pdf=False):
    """Print and save combined digest from all scan results."""
    typer.echo(f"\n{'━' * 60}")
    typer.echo(f"📋 COMBINED DIGEST — {datetime.now().strftime('%Y-%m-%d')}")
    typer.echo(f"{'━' * 60}\n")

    for name, result in all_results:
        typer.echo(f"## 📌 {name}\n")
        typer.echo(result.to_markdown())
        typer.echo(f"\n{'─' * 40}\n")

    today_str = datetime.now().strftime("%Y-%m-%d")
    report_dir = os.path.join("reports", today_str)
    os.makedirs(report_dir, exist_ok=True)

    # Build executive summary
    typer.echo("📝 Generating executive summary...")
    exec_summary = asyncio.run(_generate_executive_summary(all_results, today_str))

    combined_md = f"# Course Scout Daily Scan — {today_str}\n\n"
    combined_md += exec_summary + "\n\n---\n\n"
    for name, result in all_results:
        combined_md += f"## 📌 {name}\n\n{result.to_markdown()}\n\n---\n\n"

    # Rewrite known social URLs to app-scheme URIs so they open the native
    # app directly instead of bouncing through Telegram's in-app browser.
    from course_scout.infrastructure.deep_links import deep_linkify

    combined_md = deep_linkify(combined_md)

    md_path = os.path.join(report_dir, f"scan_{today_str}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(combined_md)
    typer.echo(f"📝 Combined report: {md_path}")

    if pdf:
        renderer = PDFRenderer()
        pdf_path = renderer.render_from_markdown(
            combined_md, f"scan_{today_str}.pdf", output_dir=report_dir
        )
        typer.echo(f"📄 PDF report: {pdf_path}")

    repository = SqliteReportRepository()
    for name, result in all_results:
        repository.add_report(
            date=result.date,
            channel_id=str(result.channel_name),
            task_name=name,
            md_path=md_path,
            summary="\n".join(result.summaries),
        )
    typer.echo("🗄️ Reports saved to database.")


@app.command()
def scan(
    days: int = typer.Option(1, "--days", "-d", help="Number of complete days to scan"),
    pdf: bool = typer.Option(True, "--pdf/--no-pdf", help="Generate PDF report (default: on)"),
    today: bool = typer.Option(False, "--today", help="Include today (incomplete day)"),
):
    """Scan all configured topics. Defaults to yesterday (last complete day)."""
    setup_logging()
    settings = load_settings()

    scraper = TelethonScraper(
        settings.tg_api_id,
        settings.tg_api_hash,
        settings.session_path,
        phone=settings.phone_number,
        login_code=settings.login_code,
    )

    if not settings.tasks:
        typer.echo("No tasks configured in config.yaml.")
        raise typer.Exit(code=1)

    label = "today" if today else f"last {days} complete day(s)"
    typer.echo(f"━━━ Course Scout — Scanning {len(settings.tasks)} topics ({label}) ━━━\n")

    all_results = asyncio.run(
        _scan_all_tasks(scraper, settings, settings.resolved_tasks, days, include_today=today)
    )

    if not all_results:
        typer.echo("\nNo activity found across any topics.")
        return

    # Separate results from providers for usage aggregation
    display_results = [(name, digest) for name, digest, _provider in all_results]
    _output_combined_report(display_results, pdf)

    # Merge usage from all parallel providers
    from course_scout.infrastructure.providers.claude_provider import UsageStats

    merged = UsageStats()
    for _name, _digest, provider in all_results:
        if hasattr(provider, "usage"):
            u = provider.usage
            merged.total_input_tokens += u.total_input_tokens
            merged.total_output_tokens += u.total_output_tokens
            merged.total_cache_read_tokens += u.total_cache_read_tokens
            merged.total_cache_creation_tokens += u.total_cache_creation_tokens
            merged.total_cost_usd += u.total_cost_usd
            merged.total_duration_ms += u.total_duration_ms
            merged.call_count += u.call_count
            merged.calls.extend(u.calls)

    typer.echo(f"\n{merged.summary()}")


@app.command(name="post-task")
def post_task(
    date: str = typer.Option(
        None,
        "--date",
        help="YYYY-MM-DD; defaults to the most recent dated subdir of reports/",
    ),
    reports_dir: str = typer.Option(
        "reports",
        "--reports-dir",
        help="Parent dir holding YYYY-MM-DD subdirs (default: ./reports/)",
    ),
    vault_dir: str = typer.Option(
        None,
        "--vault-dir",
        help="Override Obsidian vault path (else COURSE_SCOUT_VAULT_DIR env, "
             "else ~/Library/CloudStorage/OneDrive-Personal/Obsidian Vault)",
    ),
):
    """Publish a TaskNotes Inbox stub for a course-scout daily report.

    Reads `<reports-dir>/<date>/scan_<date>.md` (and matching .pdf), extracts
    Executive Summary + Top finds, writes a stub into the vault Inbox so the
    next morning's TaskNotes review surfaces it as an actionable item.

    Run this on the same machine that owns the vault filesystem (the Mac, not
    the NAS Docker container) — see CLAUDE.md "Cross-machine publishing".
    """
    import re as _re
    from pathlib import Path as _Path

    from course_scout.infrastructure.tasknotes import TaskNotesPublisher

    setup_logging()

    parent = _Path(reports_dir).expanduser().resolve()
    if not parent.is_dir():
        typer.echo(f"ERR: reports dir not found: {parent}", err=True)
        raise typer.Exit(2)

    if date is None:
        dated = sorted(
            d.name for d in parent.iterdir()
            if d.is_dir() and _re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.name)
        )
        if not dated:
            typer.echo(f"ERR: no YYYY-MM-DD subdirs in {parent}", err=True)
            raise typer.Exit(2)
        date = dated[-1]

    report_dir = parent / date
    md_files = sorted(report_dir.glob("scan_*.md"))
    if not md_files:
        typer.echo(f"ERR: no scan_*.md in {report_dir}", err=True)
        raise typer.Exit(2)
    md_path = md_files[0]
    pdf_path = md_path.with_suffix(".pdf")
    if not pdf_path.is_file():
        pdf_path = None  # type: ignore[assignment]

    publisher = TaskNotesPublisher(_Path(vault_dir).expanduser() if vault_dir else None)
    stub = publisher.publish(md_path, pdf_path)
    typer.echo(f"wrote {stub}")


@app.command()
def list_topics(channel: str):
    """List all topics in a forum-enabled Telegram group/channel."""
    setup_logging()
    settings = load_settings()

    # Handle numeric IDs
    try:
        if channel.startswith("-") and channel[1:].isdigit():
            channel_id: str | int = int(channel)
        elif channel.isdigit():
            channel_id = int(channel)
        else:
            channel_id = channel
    except ValueError:
        channel_id = channel

    async def list_them():
        scraper = TelethonScraper(
            settings.tg_api_id,
            settings.tg_api_hash,
            settings.session_path,
            phone=settings.phone_number,
            login_code=settings.login_code,
        )
        topics = await scraper.list_topics(channel_id)
        for topic in topics:
            typer.echo(f"ID: {topic['id']} | Title: {topic['title']}")

    asyncio.run(list_them())


if __name__ == "__main__":
    app()
