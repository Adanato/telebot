import asyncio
import logging
import os
from typing import Optional

import typer
from pydantic_settings import BaseSettings

from telebot.application.digest import GenerateDigestUseCase
from telebot.infrastructure.summarization import OrchestratedSummarizer
from telebot.infrastructure.telegram import TelethonScraper
from telebot.infrastructure.reporting import PDFRenderer
from telebot.infrastructure.logging_config import setup_logging


class Settings(BaseSettings):
    tg_api_id: int
    tg_api_hash: str
    gemini_api_key: str
    groq_api_key: Optional[str] = None
    preferred_provider: str = "gemini"
    phone_number: Optional[str] = None
    login_code: Optional[str] = None
    session_path: str = "telebot.session"

    model_config = {"env_file": ".env", "extra": "ignore"}


app = typer.Typer()


@app.command()
def digest(
    channel: str,
    topic: Optional[str] = typer.Option(None, "--topic", "-t", help="Specific Topic ID or Name"),
    days: int = typer.Option(1, "--days", "-d", help="Number of days to look back"),
    pdf: bool = typer.Option(False, "--pdf", help="Generate a PDF report"),
    provider: str = typer.Option("groq", "--provider", help="AI Provider to use (gemini, groq)"),
    send_to: Optional[str] = typer.Option(None, "--send-to", help="Telegram User/Chat to send the PDF to"),
    email: Optional[str] = typer.Option(None, "--email", help="Email the report to this address"),
):
    """Generate a daily digest for a Telegram channel or specific Topic."""
    # Configure logging
    setup_logging()
    
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
        
    # Resolve aliases if possible (CLI might not have full alias map, but sse_server does)
    # For now, let's just rely on user passing valid ID or @username, OR we can add the aliases here too.
    # User passed "coursebusters" (no @).
    # If channel_id is not int and not starting with @, Telethon might fail if it's not in contacts.
    # Let's add basic alias support here too for consistency
    aliases = {
        "coursebusters": -1001603660516,
        "course busters": -1001603660516,
    }
    if isinstance(channel_id, str):
        key = channel_id.lstrip("@").lower()
        if key in aliases:
            channel_id = aliases[key]

    scraper = TelethonScraper(
        settings.tg_api_id, 
        settings.tg_api_hash, 
        settings.session_path,
        phone=settings.phone_number,
        login_code=settings.login_code
    )
    
    # Resolve Topic ID if provided as string
    resolved_topic_id = None
    if topic:
        if topic.isdigit():
            resolved_topic_id = int(topic)
        else:
            typer.echo(f"Resolving topic '{topic}' in {channel_id}...")
            # We need to list topics to find the ID
            import asyncio
            # We can't await here easily unless we wrap all in asyncio.run
            # The main execution IS wrapped below. Let's move this inside the async block or just run it.
            
            async def resolve_topic():
                topics = await scraper.list_topics(channel_id)
                search_lower = topic.lower()
                matches = [t for t in topics if search_lower in t["title"].lower()]
                if not matches:
                    typer.echo(f"❌ Topic '{topic}' not found.")
                    return None
                exact = next((t for t in matches if t["title"].lower() == search_lower), None)
                target = exact or matches[0]
                return target["id"]
            
            resolved_topic_id = asyncio.run(resolve_topic())
            if not resolved_topic_id:
                raise typer.Exit(code=1)
                
            typer.echo(f"✅ Resolved to Topic ID: {resolved_topic_id}")

    summarizer = OrchestratedSummarizer(
        gemini_key=settings.gemini_api_key,
        groq_key=settings.groq_api_key,
        provider=provider,
        scraper=scraper
    )
    use_case = GenerateDigestUseCase(scraper, summarizer)

    result = asyncio.run(use_case.execute(channel_id, topic_id=resolved_topic_id, lookback_days=days))  # type: ignore

    # Console Output (Text Only)
    typer.echo(f"\n--- Digest for {channel} ({result.date}) ---\n")
    typer.echo(result.to_markdown())

    # Markdown Output (Intermediate/Debug)
    md_filename = f"digest_{topic or channel}_{result.date}.md"
    md_path = os.path.join("reports", md_filename)
    
    os.makedirs("reports", exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(result.to_markdown())
    
    typer.echo(f"\n📝 Markdown Report generated: {md_path}")

    # PDF Output
    if pdf or send_to: # Force PDF if sending
        renderer = PDFRenderer()
        pdf_path = renderer.render(result, filename=f"digest_{topic or channel}_{result.date}.pdf")
        typer.echo(f"📄 PDF Report generated: {pdf_path}")
        
        if send_to:
            typer.echo(f"\n📨 Sending PDF to {send_to}...")
            async def send_file():
                from telethon import TelegramClient
                client = TelegramClient(settings.session_path, settings.tg_api_id, settings.tg_api_hash)
                await client.connect()
                try:
                    # Resolve peer
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
            
            asyncio.run(send_file())
        
    # Email Delivery (Placeholder)
    if email:
        typer.echo(f"\n📧 Sending email to {email}... (Feature Coming Soon)")


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
        topics = await scraper.list_topics(channel_id)
        for topic in topics:
            typer.echo(f"ID: {topic['id']} | Title: {topic['title']}")

    asyncio.run(list_them())


if __name__ == "__main__":
    app()
