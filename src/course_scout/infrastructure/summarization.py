import asyncio
import datetime
import logging
import re

from course_scout.domain.models import ChannelDigest, TelegramMessage
from course_scout.domain.services import ScraperInterface, SummarizerInterface
from course_scout.infrastructure.agents import (
    AgentOrchestrator,
    StructuredMessage,
    SummarizerInputSchema,
    SummarizerOutputSchema,
)

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 25

# Usable input budget per model (context window minus headroom for system prompt,
# output schema, tool definitions, and thinking tokens).
_MODEL_BUDGETS: dict[str, int] = {
    "claude-haiku-4-5": 180_000,
    "claude-sonnet-4-6": 950_000,
    "claude-opus-4-7": 950_000,
}
_DEFAULT_BUDGET = 150_000  # safe fallback for unknown models

# Escalation chain: if a topic exceeds the assigned model's budget, climb to a
# bigger model BEFORE splitting. Splitting only happens if even the top model
# can't fit.
_ESCALATION: dict[str, list[str]] = {
    "claude-haiku-4-5": ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-7"],
    "claude-sonnet-4-6": ["claude-sonnet-4-6", "claude-opus-4-7"],
    "claude-opus-4-7": ["claude-opus-4-7"],
}


class OrchestratedSummarizer(SummarizerInterface):
    """AISummarizer using Claude with token-aware chunking + model escalation."""

    def __init__(
        self,
        summarizer_model: str | None = None,
        system_prompt: str | None = None,
        thinking: str = "adaptive",
        effort: str = "medium",
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        scraper: ScraperInterface | None = None,
        include_media: bool = False,
    ):
        """Initialize with per-topic agent configuration."""
        self.assigned_model = summarizer_model or "claude-sonnet-4-6"
        self.system_prompt = system_prompt
        self.thinking = thinking
        self.effort = effort
        self.chunk_size = chunk_size
        self.scraper = scraper
        self.include_media = include_media
        # Default orchestrator (assigned model). Escalation creates fresh ones.
        self.orchestrator = self._make_orchestrator(self.assigned_model)

    def _make_orchestrator(self, model: str) -> AgentOrchestrator:
        return AgentOrchestrator(
            summarizer_model=model,
            system_prompt=self.system_prompt,
            thinking=self.thinking,
            effort=self.effort,
        )

    def _pick_model(self, total_tokens: int) -> tuple[str, int]:
        """Pick the smallest model in the escalation chain that fits the input.

        Returns (model_id, that_model's_budget). Falls back to the largest model
        in the chain if nothing fits (caller will then split-chunk against that
        budget).
        """
        chain = _ESCALATION.get(self.assigned_model, [self.assigned_model])
        for model in chain:
            budget = _MODEL_BUDGETS.get(model, _DEFAULT_BUDGET)
            if total_tokens <= budget:
                return model, budget
        # Nothing fits — return biggest available; caller will split-chunk
        biggest = chain[-1]
        return biggest, _MODEL_BUDGETS.get(biggest, _DEFAULT_BUDGET)

    async def summarize(
        self, messages: list[TelegramMessage], topic_id: int | None = None
    ) -> ChannelDigest:
        """Summarize messages using chunked pipeline.

        1. Chunk messages into batches of CHUNK_SIZE
        2. Summarize each chunk in parallel
        3. Merge chunk summaries
        4. Verify merged result
        5. Ground links
        """
        try:
            structured_messages = self._prepare_structured_input(messages)
            link_map = {m.id: m.link for m in structured_messages if m.link}
            url_pattern = re.compile(r"https?://\S+")
            all_raw_urls = {
                url for m in structured_messages for url in url_pattern.findall(m.content)
            }

            digest_date = datetime.date.today()
            topic_title = f"Topic {topic_id}" if topic_id else "General Channel"

            # Pre-flight: estimate tokens, pick model from escalation chain
            total_tokens = self._estimate_tokens(structured_messages)
            chosen_model, budget = self._pick_model(total_tokens)
            if chosen_model != self.assigned_model:
                logger.info(
                    f"Escalating {topic_title}: {total_tokens} tokens > "
                    f"{self.assigned_model} budget. Using {chosen_model} instead."
                )

            # Chunk against the chosen model's budget (only splits if input still
            # exceeds — escalation already gave us the biggest available context).
            chunks = self._chunk_messages(structured_messages, budget=budget)
            logger.info(
                f"Chunked {len(structured_messages)} messages ({total_tokens} tokens) "
                f"into {len(chunks)} batches using {chosen_model}"
            )

            # Build a per-call orchestrator with the chosen model
            call_orchestrator = (
                self.orchestrator if chosen_model == self.assigned_model
                else self._make_orchestrator(chosen_model)
            )

            if len(chunks) == 1:
                draft = await self._summarize_chunk(chunks[0], topic_title, digest_date, call_orchestrator)
            else:
                chunk_summaries = await asyncio.gather(
                    *[self._summarize_chunk(c, topic_title, digest_date, call_orchestrator) for c in chunks]
                )
                draft = self._merge_summaries(chunk_summaries)

            # Convert flat LLM items to discriminated domain types
            domain_items = draft.to_domain_items()

            # Programmatic grounding (replaces LLM verifier)
            grounded_links = await self._ground_links(
                draft.key_links, link_map, all_raw_urls, messages, topic_id
            )
            self._ground_items(domain_items, link_map, all_raw_urls)
            self._backfill_links(domain_items, link_map)

            return ChannelDigest(
                channel_name=topic_title,
                date=digest_date,
                summaries=[],
                items=domain_items,
                key_links=grounded_links,
            )

        except Exception as e:
            logger.error(f"Error during summarization: {e}", exc_info=True)
            return self._build_error_digest()

    @staticmethod
    def _estimate_tokens(messages: list[StructuredMessage]) -> int:
        """Rough token estimate. ~3 chars/token heuristic (conservative for English+CJK)."""
        total_chars = sum(len(m.content or "") + 40 for m in messages)  # +40 for metadata
        return total_chars // 3

    def _chunk_messages(
        self, messages: list[StructuredMessage], budget: int | None = None
    ) -> list[list[StructuredMessage]]:
        """Token-aware chunking against a per-call budget.

        - If all messages fit in `budget`, return one chunk (no splitting).
        - Otherwise, greedy-pack messages into chunks that each fit the budget.
        - `chunk_size` is a secondary cap on messages per chunk.
        """
        if not messages:
            return []
        if budget is None:
            budget = _MODEL_BUDGETS.get(self.assigned_model, _DEFAULT_BUDGET)

        total = self._estimate_tokens(messages)
        if total <= budget and len(messages) <= self.chunk_size:
            return [messages]

        chunks: list[list[StructuredMessage]] = []
        current: list[StructuredMessage] = []
        current_tokens = 0
        for m in messages:
            m_tokens = self._estimate_tokens([m])
            over_budget = current_tokens + m_tokens > budget
            over_count = len(current) >= self.chunk_size
            if current and (over_budget or over_count):
                chunks.append(current)
                current = []
                current_tokens = 0
            current.append(m)
            current_tokens += m_tokens
        if current:
            chunks.append(current)
        return chunks

    # Cap on images captioned per chunk (most-recent wins if more).
    _MAX_IMAGES_PER_CALL = 20
    _MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB per image

    async def _summarize_chunk(
        self,
        chunk: list[StructuredMessage],
        topic_title: str,
        digest_date: datetime.date,
        orchestrator: AgentOrchestrator | None = None,
    ) -> SummarizerOutputSchema:
        """Summarize a single chunk.

        Vision pre-pass (when self.include_media is True):
        For each message with a media_path, caption it via cheap Haiku vision
        (parallel, one call per image). Inject the caption back into the
        message's content as "[Media/File: <caption>]". The main parser call
        is then TEXT-ONLY and sees captions inline — no base64 blobs.

        This sidesteps the SDK hang observed with Sonnet + many base64 images
        in a single call, and lets the parser stay fast/reliable.
        """
        import os

        if self.include_media:
            from course_scout.infrastructure.vision import caption_paths

            # Collect valid, size-capped image paths (newest first)
            image_msgs = []
            for m in chunk:
                if not m.media_path:
                    continue
                try:
                    if os.path.exists(m.media_path) \
                            and os.path.getsize(m.media_path) <= self._MAX_IMAGE_BYTES:
                        image_msgs.append(m)
                except OSError:
                    pass
            image_msgs.sort(key=lambda m: m.id, reverse=True)
            if len(image_msgs) > self._MAX_IMAGES_PER_CALL:
                dropped = image_msgs[self._MAX_IMAGES_PER_CALL:]
                logger.info(
                    f"[{topic_title}] dropping {len(dropped)} image(s); "
                    f"captioning only {self._MAX_IMAGES_PER_CALL} most recent"
                )
                image_msgs = image_msgs[:self._MAX_IMAGES_PER_CALL]

            # Caption in parallel. Map path → caption.
            if image_msgs:
                paths = [m.media_path for m in image_msgs if m.media_path]
                logger.info(f"[{topic_title}] captioning {len(paths)} image(s)...")
                captions = await caption_paths(paths)
                for m in image_msgs:
                    cap = captions.get(m.media_path or "", "")
                    if cap:
                        # Replace placeholder with captioned form
                        if m.content == "[Media/File]" or not m.content:
                            m.content = f"[Media/File: {cap}]"
                        else:
                            m.content = f"{m.content}\n[Media/File: {cap}]"
                logger.info(
                    f"[{topic_title}] captioned {len([c for c in captions.values() if c])} image(s)"
                )

        # Always clear media_path — parser call is text-only, captions are inline
        for m in chunk:
            m.media_path = None

        summarizer_input = SummarizerInputSchema(
            messages=chunk,
            topic_context=f"Topic: {topic_title}, Date: {digest_date}",
            chat_message=(
                "Extract structured items. Use msg_ids, instructor, platform, status, "
                "priority, password fields. Keep description telegraphic — facts only.\n"
                "Content may include [Media/File: <caption>], [File: <filename>], "
                "or [Link: <site — title — desc>] annotations — treat all of these "
                "as authoritative text for identifying titles, instructors, platforms."
            ),
        )
        summarizer = (orchestrator or self.orchestrator).get_summarizer_agent()
        return await summarizer.run(summarizer_input)

    @staticmethod
    def _merge_summaries(summaries: list[SummarizerOutputSchema]) -> SummarizerOutputSchema:
        """Merge multiple chunk summaries into one."""
        merged_items = []
        merged_links = []
        for s in summaries:
            merged_items.extend(s.items)
            merged_links.extend(s.key_links)
        return SummarizerOutputSchema(
            items=merged_items,
            key_links=merged_links,
        )

    def _prepare_structured_input(
        self, messages: list[TelegramMessage]
    ) -> list[StructuredMessage]:
        """Convert domain messages to structured agent input.

        Inline-annotates `content` with signals the parser otherwise wouldn't see:
        - `[File: <name>]` for non-image document uploads (huge for course drops)
        - `[Link: <site> — <title> — <desc>]` for URLs with webpage previews
        Engagement counts live on TelegramMessage but are not passed to the LLM —
        they bloat the parser payload without clear benefit on small samples.
        """
        structured = []
        for m in messages:
            content = str(m.text) if m.text else "[Media/File]"
            if m.document_filename:
                content = f"{content}\n[File: {m.document_filename}]"
            if m.web_preview_title or m.web_preview_description:
                preview_parts = [
                    p for p in (m.web_preview_site, m.web_preview_title,
                                 (m.web_preview_description or "")[:200])
                    if p
                ]
                if preview_parts:
                    content = f"{content}\n[Link: {' — '.join(preview_parts)}]"
            structured.append(
                StructuredMessage(
                    id=m.id,
                    author=m.author or "Unknown",
                    content=content,
                    timestamp=str(m.date),
                    link=m.link,
                    reply_to_id=m.reply_to_id,
                    forward_from=m.forward_from_author,
                    media_path=m.local_media_path,
                )
            )
        return structured

    async def _ground_links(self, links, link_map, raw_urls, messages, topic_id):
        """Verify and repair key links in the digest."""
        grounded = []
        for link in links:
            msg_id_match = re.search(r"/(\d+)$", link.url)
            msg_id = int(msg_id_match.group(1)) if msg_id_match else None
            if link.url in link_map.values() or link.url in raw_urls:
                grounded.append(link)
            elif msg_id and self.scraper:
                repaired_link = await self._repair_link(msg_id, messages, topic_id)
                if repaired_link:
                    link.url = repaired_link
                    grounded.append(link)
        return grounded

    @staticmethod
    def _ground_items(items, link_map, raw_urls):
        """Filter hallucinated links in items.

        Keeps a link if it:
        - Matches a URL from raw message text
        - Matches a message's .link property (from link_map)
        - Is a t.me link whose msg ID exists in this batch or in the item's msg_ids
        - Is an external (non-t.me) URL (trusted from LLM extraction)
        """
        valid_msg_ids = set(link_map.keys())
        for item in items:
            item_msg_ids = set(getattr(item, "msg_ids", []))
            grounded = []
            for link in item.links:
                if link in raw_urls or link in link_map.values():
                    grounded.append(link)
                elif "t.me/" in link:
                    parts = link.rstrip("/").split("/")
                    try:
                        msg_id = int(parts[-1])
                        if msg_id in valid_msg_ids or msg_id in item_msg_ids:
                            grounded.append(link)
                    except (ValueError, IndexError):
                        pass
                else:
                    grounded.append(link)
            item.links = grounded

    @staticmethod
    def _backfill_links(items, link_map):
        """Ensure items have t.me links for their msg_ids.

        The LLM often fills msg_ids but omits corresponding t.me URLs from links.
        This constructs them from the link_map so the renderer can show them.
        """
        for item in items:
            existing_tg = {u for u in item.links if "t.me/" in u}
            for mid in getattr(item, "msg_ids", []):
                if mid in link_map and link_map[mid] not in existing_tg:
                    item.links.append(link_map[mid])

    async def _repair_link(self, msg_id, messages, topic_id):
        """Attempt active repair of a missing link via Telegram scraper."""
        if msg_id > 2_147_483_647 or msg_id < 0:
            logger.warning(f"Dropping hallucinated message ID: {msg_id}")
            return None

        batch_cid = None
        if messages and "/c/" in messages[0].link:
            batch_cid = messages[0].link.split("/")[4]

        if batch_cid and self.scraper:
            full_cid = f"-100{batch_cid}" if not batch_cid.startswith("-") else batch_cid
            try:
                fetched = await self.scraper.get_message_by_id(
                    full_cid, msg_id, topic_id=topic_id
                )
                return fetched.link if fetched else None
            except Exception as e:
                logger.warning(f"Link repair failed for msg {msg_id}: {e}")
                return None
        return None

    @staticmethod
    def _build_error_digest():
        """Create a placeholder digest for graceful failure handling."""
        return ChannelDigest(
            channel_name="Error Notice",
            date=datetime.date.today(),
            summaries=[
                "### Summarization Incomplete",
                "We encountered an issue while processing the messages for this digest "
                "(likely a service rate limit or connection timeout).",
                "Please check the system logs for technical details.",
            ],
        )
