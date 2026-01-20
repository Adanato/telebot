import asyncio
import os
from typing import Any

import typer
from pydantic_settings import BaseSettings

from telebot.application.digest import GenerateDigestUseCase
from telebot.domain.models import ChannelDigest
from telebot.infrastructure.logging_config import setup_logging
from telebot.infrastructure.reporting import PDFRenderer
from telebot.infrastructure.summarization import OrchestratedSummarizer
from telebot.infrastructure.telegram import TelethonScraper


class Settings(BaseSettings):
    tg_api_id: int
    tg_api_hash: str
    gemini_api_key: str
    groq_api_key: str | None = None
    preferred_provider: str = "gemini"
    phone_number: str | None = None
    login_code: str | None = None
    session_path: str = "telebot.session"

    model_config = {"env_file": ".env", "extra": "ignore"}


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

    # Markdown File
    md_filename = f"digest_{topic or channel}_{result.date}.md"
    md_path = os.path.join("reports", md_filename)
    os.makedirs("reports", exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(result.to_markdown())
    typer.echo(f"\n📝 Markdown Report generated: {md_path}")

    # PDF & Telegram
    if pdf or send_to:
        renderer = PDFRenderer()
        filename = f"digest_{topic or channel}_{result.date}.pdf"
        pdf_path = renderer.render(result, filename=filename)
        typer.echo(f"📄 PDF Report generated: {pdf_path}")

        if send_to:
            typer.echo(f"\n📨 Sending PDF to {send_to}...")
            from telethon import TelegramClient
            client = TelegramClient(
                settings.session_path, settings.tg_api_id, settings.tg_api_hash
            )
            await client.connect()
            try:
                try:
                    peer = int(send_to)
                except ValueError:
                    peer = send_to
                await client.send_file(peer, pdf_path, caption=f"Digest for {channel}")
                typer.echo("✅ Sent successfully!")
            except Exception as e:
                typer.echo(f"❌ Failed to send: {e}")
            finally:
                await client.disconnect()

    if email:
        typer.echo(f"\n📧 Sending email to {email}... (Feature Coming Soon)")


@app.command()
def digest(
    channel: str,
    topic: str | None = typer.Option(None, "--topic", "-t", help="Topic ID or Name"),
    days: int = typer.Option(1, "--days", "-d", help="Days to look back"),
    pdf: bool = typer.Option(False, "--pdf", help="Generate a PDF report"),
    provider: str = typer.Option("groq", "--provider", help="AI Provider (gemini, groq)"),
    send_to: str | None = typer.Option(None, "--send-to", help="User/Chat to notify"),
    email: str | None = typer.Option(None, "--email", help="Email the report"),
):
    """Generate a daily digest for a Telegram channel or specific Topic."""
    setup_logging()
    settings = Settings()  # type: ignore
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
        gemini_key=settings.gemini_api_key,
        groq_key=settings.groq_api_key,
        provider=provider,
        scraper=scraper,
    )
    use_case = GenerateDigestUseCase(scraper, summarizer)
    result = asyncio.run(
        use_case.execute(channel_id, topic_id=resolved_topic_id, lookback_days=days)
    )  # type: ignore

    if not result:
        typer.echo(f"ℹ️ No new messages found for {channel} in the last {days} days.")
        return

    asyncio.run(_handle_digest_delivery(result, channel, topic, pdf, send_to, email, settings))


@app.command()
def list_topics(channel: str):
    """List all topics in a forum-enabled Telegram group/channel."""
    setup_logging()
    settings = Settings()  # type: ignore

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
