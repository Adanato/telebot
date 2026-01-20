
import asyncio
import uuid
import datetime
import logging
from typing import Optional, Dict, Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from telebot.interfaces.mcp.main import get_use_case, settings
from telebot.infrastructure.reporting import PDFRenderer
from telebot.infrastructure.telegram import TelethonScraper

mcp = FastMCP("Telebot Summarizer", host="0.0.0.0", port=8000)
logger = logging.getLogger("telebot.sse")

# Job Store: {job_id: {"status": str, "result": str | None, "error": str | None, "timestamp": datetime}}
JOBS: Dict[str, Dict[str, Any]] = {}

# Hardcoded aliases
CHANNEL_ALIASES = {
    "coursebusters": -1001603660516,
    "course busters": -1001603660516,
    "monitoring": -1002236838334,
}

def resolve_channel_alias(channel_id: str | int) -> str | int:
    """Resolve a channel alias to its ID if it exists in the mapping."""
    if isinstance(channel_id, str):
        key = channel_id.lstrip("@").lower()
        if key in CHANNEL_ALIASES:
            return CHANNEL_ALIASES[key]
    return channel_id

async def _run_digest_background(job_id: str, channel_id: str, topic_id: int | str | None, lookback_days: int, provider: str, pdf: bool, send_to_user: Optional[str] = None):
    """Background task wrapper for digest generation."""
    logger.info(f"Starting background job {job_id} for {channel_id}")
    JOBS[job_id]["status"] = "running"
    
    try:
        # 1. Resolve Channel
        resolved_channel = resolve_channel_alias(channel_id)

        # 2. Resolve Topic ID
        resolved_topic_id = None
        if isinstance(topic_id, int):
            resolved_topic_id = topic_id
        elif isinstance(topic_id, str):
            if topic_id.isdigit():
                resolved_topic_id = int(topic_id)
            else:
                # Name resolution
                scraper = TelethonScraper(
                     api_id=settings.tg_api_id,
                     api_hash=settings.tg_api_hash,
                     session_path=settings.session_path,
                     phone=settings.phone_number,
                     login_code=settings.login_code
                )
                topics = await scraper.list_topics(resolved_channel)
                search_lower = topic_id.lower()
                matches = [t for t in topics if search_lower in t["title"].lower()]
                
                if not matches:
                    raise ValueError(f"Topic '{topic_id}' not found in channel.")
                
                # Pick best match
                exact = next((t for t in matches if t["title"].lower() == search_lower), None)
                target = exact or matches[0]
                resolved_topic_id = target["id"]

        # 3. Generate Digest
        use_case = get_use_case(provider=provider)
        try:
            if isinstance(resolved_channel, str): 
                if resolved_channel.startswith("-") and resolved_channel[1:].isdigit():
                    peer = int(resolved_channel)
                elif resolved_channel.isdigit():
                    peer = int(resolved_channel)
                else:
                    peer = resolved_channel
            else:
                peer = resolved_channel
        except ValueError:
            peer = resolved_channel

        digest = await use_case.execute(peer, topic_id=resolved_topic_id, lookback_days=lookback_days)
        
        # 4. Format Output
        response = digest.to_markdown()
        
        if pdf:
            renderer = PDFRenderer(output_dir="reports")
            topic_str = str(resolved_topic_id) if resolved_topic_id else "general"
            filename = f"digest_{topic_str}_{datetime.datetime.now().strftime('%Y-%m-%d')}.pdf"
            pdf_path = renderer.render(digest, filename=filename)
            response += f"\n\n📄 **PDF Report generated**: {pdf_path}"
            
            # Send to User if requested
            if send_to_user:
                try:
                    logger.info(f"Sending PDF to user {send_to_user}...")
                    from telethon import TelegramClient
                    client = TelegramClient(settings.session_path, settings.tg_api_id, settings.tg_api_hash)
                    await client.connect()
                    try:
                         # Ensure we can resolve user. send_to_user might be '@adam' or numeric ID
                         # If coming from Orion, it's likely a numeric ID of the user.
                         try:
                             user_peer = int(send_to_user)
                         except ValueError:
                             user_peer = send_to_user
                             
                         await client.send_file(user_peer, pdf_path, caption=f"Here is your digest for {channel_id}!")
                         logger.info(f"Sent PDF to {send_to_user}")
                         response += f"\n✅ Sent PDF to {send_to_user}"
                    finally:
                        await client.disconnect()
                except Exception as ex:
                     logger.error(f"Failed to send PDF to user: {ex}")
                     response += f"\n⚠️ Failed to send PDF to user: {ex}"
        
        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["result"] = response
        logger.info(f"Job {job_id} completed successfully.")
        
    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}", exc_info=True)
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["error"] = str(e)


@mcp.tool()
async def list_topics(channel_id: str, search: Optional[str] = None) -> str:
    """
    List available forum topics in a Telegram channel id or alias (synchronous).
    """
    try:
        resolved_channel = resolve_channel_alias(channel_id)
        
        scraper = TelethonScraper(
            api_id=settings.tg_api_id,
            api_hash=settings.tg_api_hash,
            session_path=settings.session_path,
            phone=settings.phone_number,
            login_code=settings.login_code
        )
        topics = await scraper.list_topics(resolved_channel)
        if not topics:
            return "No topics found or channel is not a forum."
        
        if search:
            search_lower = search.lower()
            topics = [t for t in topics if search_lower in t["title"].lower()]
            if not topics:
                return f"No topics found matching '{search}' in {channel_id}."
        
        lines = [f"ID: {t['id']} | Title: {t['title']}" for t in topics]
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing topics: {str(e)}"

@mcp.tool()
async def start_digest_generation(
    channel_id: str, 
    topic_id: int | str | None = None, 
    lookback_days: int = 1,
    provider: str = "groq",
    pdf: bool = True,
    user_id: str | None = None
) -> str:
    """
    Start a background job to generate a summary digest. Returns a Job ID instantly.
    
    Args:
        channel_id: The Telegram channel ID or alias (e.g. 'coursebusters').
        topic_id: Topic ID (int) or Name (string).
        lookback_days: Days to look back (default 1).
        provider: 'groq' (default) or 'gemini'.
        pdf: Generate PDF (default True).
        user_id: Optional Telegram User ID/Username to send the PDF to upon completion.
        
    Returns:
        String containing the Job ID to check status with.
    """
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "status": "pending",
        "result": None,
        "error": None,
        "timestamp": datetime.datetime.now()
    }
    
    # Spawn background task
    asyncio.create_task(_run_digest_background(job_id, channel_id, topic_id, lookback_days, provider, pdf, user_id))
    
    return f"Job started successfully. Job ID: {job_id}\nUse check_task_status('{job_id}') to see results."

@mcp.tool()
async def check_task_status(job_id: str) -> str:
    """
    Check the status of a background job.
    
    Args:
        job_id: The ID string returned by start_digest_generation.
        
    Returns:
        Status message, or the final result if completed.
    """
    job = JOBS.get(job_id)
    if not job:
        return f"Job ID {job_id} not found."
    
    status = job["status"]
    if status == "running" or status == "pending":
        return f"Job {job_id} is currently {status}. Please check again later."
    elif status == "completed":
        return f"✅ Job Completed!\n\n{job['result']}"
    elif status == "failed":
        return f"❌ Job Failed: {job['error']}"
    
    return f"Unknown status: {status}"

if __name__ == "__main__":
    print("Starting Telebot MCP Server on http://0.0.0.0:8000 (SSE)")
    uvicorn.run(mcp.sse_app, host="0.0.0.0", port=8000)
