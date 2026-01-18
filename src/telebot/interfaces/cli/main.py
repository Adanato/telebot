import asyncio
import logging
from typing import Optional

import typer
from pydantic_settings import BaseSettings

from telebot.application.digest import GenerateDigestUseCase
from telebot.infrastructure.gemini import GeminiSummarizer
from telebot.infrastructure.telegram import TelethonScraper


class Settings(BaseSettings):
    tg_api_id: int
    tg_api_hash: str
    gemini_api_key: str
    phone_number: Optional[str] = None
    login_code: Optional[str] = None
    session_path: str = "telebot.session"

    model_config = {"env_file": ".env", "extra": "ignore"}


app = typer.Typer()


@app.command()
def digest(
    channel: str,
    topic: Optional[int] = typer.Option(None, "--topic", "-t", help="Specific Topic ID"),
    days: int = typer.Option(1, "--days", "-d", help="Number of days to look back"),
    pdf: bool = typer.Option(False, "--pdf", help="Generate a PDF report"),
    email: Optional[str] = typer.Option(None, "--email", help="Email the report to this address"),
):
    """Generate a daily digest for a Telegram channel or specific Topic."""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )
    
    settings = Settings()  # type: ignore

    # ... (ID handling) ...
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

    scraper = TelethonScraper(
        settings.tg_api_id, 
        settings.tg_api_hash, 
        settings.session_path,
        phone=settings.phone_number,
        login_code=settings.login_code
    )
    summarizer = GeminiSummarizer(settings.gemini_api_key)
    use_case = GenerateDigestUseCase(scraper, summarizer)

    result = asyncio.run(use_case.execute(channel_id, topic_id=topic, lookback_days=days))  # type: ignore

    # Console Output (Text Only)
    header = f"Daily Digest for {channel}"
    if topic:
        header += f" (Topic: {topic})"
    typer.echo(f"\n--- {header} ({result.date}) ---\n")
    typer.echo("SUMMARY:")
    for s in result.summaries:
        typer.echo(f"- {s}")

    if result.action_items:
        typer.echo("\nACTION ITEMS:")
        for ai in result.action_items:
            typer.echo(f"[] {ai}")

    if result.key_links:
        typer.echo("\nKEY LINKS:")
        for link in result.key_links:
            typer.echo(f"- {link}")

    # PDF Output
    if pdf:
        from telebot.infrastructure.reporting import PDFRenderer
        renderer = PDFRenderer()
        pdf_path = renderer.render(result, filename=f"digest_{topic or channel}_{result.date}.pdf")
        typer.echo(f"\n📄 PDF Report generated: {pdf_path}")
        
    # Email Delivery (Placeholder)
    if email:
        typer.echo(f"\n📧 Sending email to {email}... (Feature Coming Soon)")


@app.command()
def list_topics(channel: str):
    """List all topics in a forum-enabled Telegram group/channel."""
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

    from telethon import functions
    from telethon.tl.functions.messages import GetForumTopicsRequest

    async def list_them():
        scraper = TelethonScraper(
            settings.tg_api_id, 
            settings.tg_api_hash, 
            settings.session_path,
            phone=settings.phone_number,
            login_code=settings.login_code
        )
        await scraper.client.start(
            phone=settings.phone_number,
            code_callback=lambda: settings.login_code if settings.login_code else input("Enter code: ")
        )
        async with scraper.client:
            result = await scraper.client(
                functions.messages.GetForumTopicsRequest(
                    peer=channel_id, offset_date=None, offset_id=0, offset_topic=0, limit=100
                )
            )
            for topic in result.topics:
                typer.echo(f"ID: {topic.id} | Title: {topic.title}")

    asyncio.run(list_them())


if __name__ == "__main__":
    app()
