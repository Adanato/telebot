import datetime

from telethon import TelegramClient

from telebot.domain.models import TelegramMessage
from telebot.domain.services import ScraperInterface
import logging

logger = logging.getLogger(__name__)


class TelethonScraper(ScraperInterface):
    def __init__(self, api_id: int, api_hash: str, session_path: str, phone: str | None = None, login_code: str | None = None):
        self.client = TelegramClient(session_path, api_id, api_hash)
        self.phone = phone
        self.login_code = login_code

    async def get_messages(
        self, channel_id: str, start_date: datetime.datetime, topic_id: int | None = None
    ) -> list[TelegramMessage]:
        # Explicitly start with phone and code if provided
        await self.client.start(
            phone=self.phone,
            code_callback=lambda: self.login_code if self.login_code else input("Enter code: ")
        )
        # Prepare media directory
        import os
        media_dir = os.path.join(os.getcwd(), "media_cache")
        os.makedirs(media_dir, exist_ok=True)

        async with self.client as client:  # type: ignore
            messages = []
            async for message in client.iter_messages(  # type: ignore
                channel_id, offset_date=start_date, reverse=True, reply_to=topic_id
            ):
                if message.text or message.media: 
                    forward_from_author = None
                    if message.fwd_from and message.fwd_from.from_name:
                        forward_from_author = message.fwd_from.from_name
                    
                    reply_to_id = message.reply_to.reply_to_msg_id if message.reply_to else None
                    
                    # Download media if present (limit to images/videos for now)
                    local_path = None
                    if message.media:
                        try:
                            # Download thumbnail or small version
                            logger.info(f"Downloading media for message {message.id}...")
                            local_path = await message.download_media(file=media_dir)
                            logger.info(f"Downloaded media to {local_path}")
                        except Exception as e:
                            logger.error(f"Failed to download media for message {message.id}: {e}")

                    messages.append(
                        TelegramMessage(
                            id=message.id,
                            text=message.text or "",
                            date=message.date,
                            author=getattr(message.sender, "username", None),
                            link=f"https://t.me/{channel_id}/{message.id}",
                            reply_to_id=reply_to_id,
                            forward_from_chat=None, 
                            forward_from_author=forward_from_author,
                            local_media_path=local_path
                        )
                    )
            return messages
