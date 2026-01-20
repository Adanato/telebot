import asyncio

from telebot.infrastructure.telegram import TelethonScraper
from telebot.interfaces.mcp.main import settings


async def search_channel():
    print(f"Connecting to Telegram as {settings.phone_number}...")
    scraper = TelethonScraper(
        api_id=settings.tg_api_id,
        api_hash=settings.tg_api_hash,
        session_path=settings.session_path,
        phone=settings.phone_number,
        login_code=settings.login_code,
    )

    # ID found from search: 1603660516 -> -1001603660516
    channel_id = "-1001603660516"
    print(f"Testing access to ID: {channel_id}...")

    try:
        topics = await scraper.list_topics(channel_id)
        print(f"✅ Success! Found {len(topics)} topics.")
        for t in topics[:5]:  # Show first 5
            print(f"- {t['title']}")

    except Exception as e:
        print(f"❌ Failed to list topics for ID: {e}")


if __name__ == "__main__":
    asyncio.run(search_channel())
