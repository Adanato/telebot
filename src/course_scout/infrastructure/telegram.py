import asyncio
import datetime
import logging
import os
from typing import Any, cast

from telethon import TelegramClient

from course_scout.domain.models import TelegramMessage
from course_scout.domain.services import ScraperInterface

logger = logging.getLogger(__name__)

# Per-topic fetch timeout — read from runtime config (`runtime.topic_fetch_timeout`).
# Telethon will retry connection drops indefinitely (we've seen 30+ minute hangs);
# after this timeout, skip the topic and move on.


class TelethonScraper(ScraperInterface):
    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_path: str,
        phone: str | None = None,
        login_code: str | None = None,
    ):
        """Initialize the scraper with API credentials and session info."""
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_path = session_path
        self.phone = phone
        self.login_code = login_code

    async def get_messages(
        self,
        channel_id: str | int,
        start_date: datetime.datetime,
        end_date: datetime.datetime | None = None,
        topic_id: int | None = None,
    ) -> list[TelegramMessage]:
        """Fetch messages from a channel/topic starting from a specific date."""
        # Prepare media directory
        media_dir = os.path.join(os.getcwd(), "media_cache")
        os.makedirs(media_dir, exist_ok=True)

        messages = []

        # Create client fresh for each call to avoid state issues
        client: Any = TelegramClient(self.session_path, self.api_id, self.api_hash)

        try:
            await client.connect()

            if not await client.is_user_authorized():

                def get_code() -> str:
                    return str(self.login_code) if self.login_code else input("Enter code: ")

                await client.start(
                    phone=self.phone,
                    code_callback=get_code,
                )

            logger.info(
                f"Fetching messages from {channel_id}, topic={topic_id}, since {start_date}"
            )

            async def _iterate():
                async for message in client.iter_messages(
                    channel_id, offset_date=start_date, reverse=True, reply_to=topic_id, limit=100
                ):
                    if end_date and message.date > end_date:
                        logger.debug(f"Reached end_date {end_date}. Stopping fetch.")
                        break

                    if message.text or message.media:
                        telegram_msg = await self._process_message(
                            channel_id, message, topic_id, media_dir
                        )
                        messages.append(telegram_msg)
                        logger.debug(f"Fetched message ID {message.id}")

            from course_scout.infrastructure.runtime import get_runtime

            topic_timeout = get_runtime().topic_fetch_timeout
            try:
                await asyncio.wait_for(_iterate(), timeout=topic_timeout)
            except TimeoutError:
                logger.warning(
                    f"Fetch timed out after {topic_timeout}s for "
                    f"channel={channel_id}, topic={topic_id}. "
                    f"Returning {len(messages)} partial messages."
                )

            logger.info(f"Fetched {len(messages)} messages from {channel_id}")
        finally:
            await client.disconnect()

        return messages

    async def get_pinned_messages(
        self, channel_id: str | int, topic_id: int | None = None
    ) -> list[TelegramMessage]:
        """Fetch all currently-pinned messages in a channel (or forum topic).

        Uses Telethon's InputMessagesFilterPinned. Returns empty list on any error
        — pinning is an enhancement, not a blocker for the scan.
        """
        from telethon.tl.types import InputMessagesFilterPinned

        messages: list[TelegramMessage] = []
        client: Any = TelegramClient(self.session_path, self.api_id, self.api_hash)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await client.start(phone=self.phone, code_callback=lambda: self.login_code)

            try:
                async for message in client.iter_messages(
                    channel_id,
                    filter=InputMessagesFilterPinned(),
                    reply_to=topic_id,
                    limit=50,
                ):
                    if message.text or message.media:
                        messages.append(await self._process_message(channel_id, message, topic_id))
            except Exception as e:
                logger.warning(f"Pin fetch failed for channel={channel_id} topic={topic_id}: {e}")
        finally:
            await client.disconnect()

        return messages

    async def get_message_by_id(
        self, channel_id: str | int, message_id: int, topic_id: int | None = None
    ) -> TelegramMessage | None:
        """Fetch a specific message by ID and verify it exists."""
        client: Any = TelegramClient(self.session_path, self.api_id, self.api_hash)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await client.start(phone=self.phone, code_callback=lambda: self.login_code)

            # Telethon requires numeric IDs to be integers
            try:
                entity = int(channel_id)
            except ValueError:
                entity = channel_id

            message = await client.get_messages(entity, ids=[message_id])
            if message and message[0]:
                return await self._process_message(channel_id, message[0], topic_id)
            return None
        finally:
            await client.disconnect()

    async def search_messages(
        self, channel_id: str | int, query: str, topic_id: int | None = None, limit: int = 5
    ) -> list[TelegramMessage]:
        """Search for messages containing the given query string."""
        client: Any = TelegramClient(self.session_path, self.api_id, self.api_hash)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await client.start(phone=self.phone, code_callback=lambda: self.login_code)

            try:
                entity = int(channel_id)
            except ValueError:
                entity = channel_id

            messages = []
            async for message in client.iter_messages(
                entity, search=query, limit=limit, reply_to=topic_id
            ):
                messages.append(await self._process_message(channel_id, message, topic_id))
            return messages
        finally:
            await client.disconnect()

    async def list_topics(self, channel_id: str | int) -> list[dict]:
        """List forum topics for a given channel."""
        from telethon import functions

        client: Any = TelegramClient(self.session_path, self.api_id, self.api_hash)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await client.start(phone=self.phone, code_callback=lambda: self.login_code)

            try:
                entity = int(channel_id)
            except ValueError:
                entity = channel_id

            result = await client(
                functions.messages.GetForumTopicsRequest(
                    peer=cast(Any, entity),
                    offset_date=None,
                    offset_id=0,
                    offset_topic=0,
                    limit=100,
                )
            )
            return [{"id": t.id, "title": t.title} for t in result.topics]
        finally:
            await client.disconnect()

    def _format_message_link(self, cid: str | int, mid: int, topic_id: int | None = None) -> str:
        """Format private chat links correctly for forum-aware deep-linking."""
        cid_str = str(cid)
        topic_suffix = f"/{topic_id}" if topic_id else ""

        if cid_str.startswith("-100") and cid_str[4:].isdigit():
            # Private supergroup/channel format for deep-linking
            stripped_id = cid_str[4:]
            return f"https://t.me/c/{stripped_id}{topic_suffix}/{mid}"
        elif cid_str.startswith("-") and cid_str[1:].isdigit():
            # Basic group
            stripped_id = cid_str[1:]
            return f"https://t.me/c/{stripped_id}{topic_suffix}/{mid}"
        return f"https://t.me/{cid_str}/{mid}"

    async def _process_message(  # noqa: C901
        self, channel_id: str | int, message, topic_id: int | None, media_dir: str | None = None
    ) -> TelegramMessage:
        """Convert a Telethon message to our domain model."""
        forward_from_author = None
        if message.fwd_from and message.fwd_from.from_name:
            forward_from_author = message.fwd_from.from_name

        reply_to_id = message.reply_to.reply_to_msg_id if message.reply_to else None

        # ── Engagement signals ──
        reaction_count = 0
        if getattr(message, "reactions", None) and getattr(message.reactions, "results", None):
            reaction_count = sum(getattr(r, "count", 0) or 0 for r in message.reactions.results)
        views = getattr(message, "views", None)
        forwards = getattr(message, "forwards", 0) or 0
        reply_count = 0
        if getattr(message, "replies", None):
            reply_count = getattr(message.replies, "replies", 0) or 0

        # ── Document filename (non-image docs: zips/pdfs/rars) ──
        document_filename = None
        is_image = False
        if hasattr(message, "photo") and message.photo:
            is_image = True
        elif hasattr(message, "document") and message.document:
            mime_type = getattr(message.document, "mime_type", "") or ""
            if mime_type.startswith("image/"):
                is_image = True
            else:
                # Walk DocumentAttributeFilename for the real filename
                for attr in getattr(message.document, "attributes", []) or []:
                    name = getattr(attr, "file_name", None)
                    if name:
                        document_filename = name
                        break
                if not document_filename:
                    document_filename = getattr(getattr(message, "file", None), "name", None)

        # ── Link preview (webpage metadata for URLs) ──
        web_preview_title = None
        web_preview_description = None
        web_preview_url = None
        web_preview_site = None
        webpage = getattr(getattr(message, "media", None), "webpage", None)
        if webpage and getattr(webpage, "type", None) != "empty":
            web_preview_title = getattr(webpage, "title", None)
            web_preview_description = getattr(webpage, "description", None)
            web_preview_url = getattr(webpage, "url", None)
            web_preview_site = getattr(webpage, "site_name", None)

        # ── Download media if it's an image ──
        local_path = None
        if is_image and media_dir:
            try:
                ext = message.file.ext or ".jpg"
                filename = f"media_{message.id}{ext}"
                full_path = os.path.join(media_dir, filename)
                if os.path.exists(full_path):
                    local_path = full_path
                else:
                    local_path = await message.download_media(file=full_path)
            except Exception as e:
                logger.error(f"Failed to download image for message {message.id}: {e}")

        m_author = getattr(message.sender, "username", None)
        return TelegramMessage(
            id=message.id,
            text=message.text or "",
            date=message.date,
            author=m_author,
            link=self._format_message_link(channel_id, message.id, topic_id),
            reply_to_id=reply_to_id,
            forward_from_chat=None,
            forward_from_author=forward_from_author,
            local_media_path=local_path,
            reaction_count=reaction_count,
            views=views,
            forwards=forwards,
            reply_count=reply_count,
            document_filename=document_filename,
            web_preview_title=web_preview_title,
            web_preview_description=web_preview_description,
            web_preview_url=web_preview_url,
            web_preview_site=web_preview_site,
        )
