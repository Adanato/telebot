import asyncio
import datetime

from pydantic_settings import BaseSettings
from telebot.infrastructure.gemini import GeminiSummarizer

from telebot.infrastructure.telegram import TelethonScraper


class Settings(BaseSettings):
    tg_api_id: int
    tg_api_hash: str
    gemini_api_key: str
    phone_number: str | None = None
    login_code: str | None = None
    session_path: str = "telebot.session"

    model_config = {"env_file": ".env", "extra": "ignore"}


async def main():
    settings = Settings()
    scraper = TelethonScraper(
        settings.tg_api_id,
        settings.tg_api_hash,
        settings.session_path,
        phone=settings.phone_number,
        login_code=settings.login_code,
    )

    # Asian Artists Discussion Topic ID: 166550
    # Course Busters Channel ID: -1001603660516
    channel_id = int("-1001603660516")
    topic_id = 166550
    start_date = datetime.datetime.now() - datetime.timedelta(days=1)

    print(f"Fetching messages for topic {topic_id} since {start_date}...")
    messages = await scraper.get_messages(channel_id, start_date, topic_id=topic_id)

    # 1. Output raw messages to file
    raw_output = ""
    for msg in messages:
        raw_output += f"Date: {msg.date}\nAuthor: {msg.author}\nText: {msg.text}\n{'-' * 40}\n"

    with open("raw_messages.txt", "w", encoding="utf-8") as f:
        f.write(raw_output)
    print(f"Raw messages saved to raw_messages.txt ({len(messages)} messages)")

    if not messages:
        print("No messages found, skipping summarization.")
        return

    # 2. Output model summary to file
    print("Generating summary...")
    summarizer = GeminiSummarizer(settings.gemini_api_key)
    digest = await summarizer.summarize(messages, topic_id=topic_id)

    model_output = f"Channel: {digest.channel_name}\nDate: {digest.date}\n\n"
    model_output += "SUMMARIES:\n" + "\n".join([f"- {s}" for s in digest.summaries]) + "\n\n"
    model_output += "ACTION ITEMS:\n" + "\n".join([f"- {a}" for a in digest.action_items]) + "\n\n"
    model_output += "KEY LINKS:\n" + "\n".join([f"- {lk}" for lk in digest.key_links])

    with open("model_output.txt", "w", encoding="utf-8") as f:
        f.write(model_output)
    print("Model output saved to model_output.txt")


if __name__ == "__main__":
    asyncio.run(main())
