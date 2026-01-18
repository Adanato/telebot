from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel
from pydantic_settings import BaseSettings

from telebot.application.digest import GenerateDigestUseCase
from telebot.infrastructure.gemini import GeminiSummarizer
from telebot.infrastructure.telegram import TelethonScraper
from telebot.domain.models import ChannelDigest

class Settings(BaseSettings):
    tg_api_id: int
    tg_api_hash: str
    gemini_api_key: str
    phone_number: Optional[str] = None
    login_code: Optional[str] = None
    api_token: str = "dev-token"  # Simple auth for now
    session_path: str = "telebot.session"

    model_config = {"env_file": ".env", "extra": "ignore"}

settings = Settings()
app = FastAPI(title="Telebot API")

# Dependency to verify API token
async def verify_token(x_api_token: str = Header(...)):
    if x_api_token != settings.api_token:
        raise HTTPException(status_code=401, detail="Invalid API Token")
    return x_api_token

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/digest/{channel}", response_model=ChannelDigest)
async def get_digest(
    channel: str, 
    topic: Optional[int] = None,
    _token: str = Depends(verify_token)
):
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
    
    try:
        result = await use_case.execute(channel_id, topic_id=topic)  # type: ignore
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/topics/{channel}")
async def list_topics(
    channel: str,
    _token: str = Depends(verify_token)
):
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
        try:
            result = await scraper.client(
                functions.messages.GetForumTopicsRequest(
                    peer=channel_id, offset_date=None, offset_id=0, offset_topic=0, limit=100
                )
            )
            return [
                {"id": t.id, "title": t.title} 
                for t in result.topics
            ]
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
