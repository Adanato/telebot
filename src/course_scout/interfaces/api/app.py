from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic_settings import BaseSettings

from course_scout.application.digest import GenerateDigestUseCase
from course_scout.domain.models import ChannelDigest
from course_scout.infrastructure.summarization import OrchestratedSummarizer
from course_scout.infrastructure.telegram import TelethonScraper


class Settings(BaseSettings):
    tg_api_id: int
    tg_api_hash: str
    phone_number: str | None = None
    login_code: str | None = None
    api_token: str = "dev-token"  # Simple auth for now
    session_path: str = "course_scout.session"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()  # type: ignore
app = FastAPI(title="Course Scout API")


# Dependency to verify API token
async def verify_token(x_api_token: str = Header(...)):
    if x_api_token != settings.api_token:
        raise HTTPException(status_code=401, detail="Invalid API Token")
    return x_api_token


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/digest/{channel}", response_model=ChannelDigest)
async def get_digest(channel: str, topic: int | None = None, _token: str = Depends(verify_token)):
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
        login_code=settings.login_code,
    )
    summarizer = OrchestratedSummarizer(scraper=scraper)
    use_case = GenerateDigestUseCase(scraper, summarizer)

    try:
        result = await use_case.execute(channel_id, topic_id=topic)  # type: ignore
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/topics/{channel}")
async def list_topics(channel: str, _token: str = Depends(verify_token)):
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
        login_code=settings.login_code,
    )
    try:
        # Using the scraper's internalized logic instead of raw client
        topics = await scraper.list_topics(channel_id)
        return topics
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
