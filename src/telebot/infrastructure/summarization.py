from typing import Optional
import datetime
from telebot.domain.models import ChannelDigest, TelegramMessage
from telebot.domain.services import SummarizerInterface, ScraperInterface
from telebot.infrastructure.agents import AgentOrchestrator, StructuredMessage, SummarizerInputSchema, VerifierInputSchema
import logging
import asyncio

logger = logging.getLogger(__name__)


class OrchestratedSummarizer(SummarizerInterface):
    """
    AISummarizer that handles multi-provider orchestration (Gemini/Groq).
    """
    def __init__(self, gemini_key: str, groq_key: Optional[str] = None, provider: str = "gemini", scraper: Optional[ScraperInterface] = None):
        self.orchestrator = AgentOrchestrator(
            gemini_key=gemini_key, 
            groq_key=groq_key, 
            preferred_provider=provider
        )
        self.scraper = scraper

    async def summarize(
        self, messages: list[TelegramMessage], topic_id: int | None = None
    ) -> ChannelDigest:
        """
        Summarizes messages using a synchronous 3-agent pipeline.
        Even though this is an async method, it executes blocking synchronous agent calls.
        """
        try:
            # 1. Prepare Structured Input
            structured_messages = []
            for m in messages:
                content = str(m.text) if m.text else "[Media/File]"
                if m.link:
                    content += f" [Link: {m.link}]"

                structured_messages.append(
                    StructuredMessage(
                        id=m.id,
                        author=m.author or "Unknown",
                        content=content,
                        timestamp=str(m.date),
                        link=m.link,
                        reply_to_id=m.reply_to_id,
                        forward_from=m.forward_from_author
                    )
                )

            # 2. Map all valid links for grounding (Technical Verification)
            # This map allows us to verify if a suggested ID or URL actually existed in the scraped data.
            link_map = {m.id: m.link for m in structured_messages if m.link}
            # Also catch any raw URLs mentioned in text if we want more exhaustive coverage
            import re
            url_pattern = re.compile(r'https?://\S+')
            all_raw_urls = set()
            for m in structured_messages:
                all_raw_urls.update(url_pattern.findall(m.content))

            digest_date = datetime.date.today()
            topic_title = f"Topic {topic_id}" if topic_id else "General Channel"

            # 3. Run Summarizer Agent (Synchronous)
            logger.info(f"Running Summarizer Agent for {topic_title}...")
            
            summarizer_input = SummarizerInputSchema(
                messages=structured_messages,
                topic_context=f"Topic: {topic_title}, Date: {digest_date}",
                chat_message="Extract courses, discussions, files, and requests. Focus on grounding every item in a source message ID."
            )
            
            summarizer = self.orchestrator.get_summarizer_agent()
            draft_summary = summarizer.run(summarizer_input)
            
            logger.info("Summarizer complete. Running Verifier...")

            # 4. Run Verifier Agent (Synchronous)
            raw_msg_str = "\n".join([f"[{m.id}] {m.author}: {m.content[:300]}" for m in structured_messages])
            
            verifier_input = VerifierInputSchema(
                original_messages=raw_msg_str,
                summarizer_output=draft_summary,
                chat_message="Fix context errors and check logical consistency."
            )
            
            verifier = self.orchestrator.get_verifier_agent()
            verified_data = verifier.run(verifier_input)

            # 5. Programmatic Grounding & Active Repair
            logger.info("Performing programmatic link grounding and repair...")
            
            # Ground Key Links
            grounded_links = []
            for link in verified_data.verified_links:
                # Try to extract message ID from link if it's a t.me/c/ link
                msg_id_match = re.search(r'/(\d+)$', link.url)
                msg_id = int(msg_id_match.group(1)) if msg_id_match else None

                if link.url in link_map.values() or link.url in all_raw_urls:
                    grounded_links.append(link)
                elif msg_id and self.scraper:
                    # ACTIVE REPAIR: Try to fetch the message from Telegram
                    logger.info(f"Link not in batch. Attempting active repair for message ID {msg_id}...")
                    
                    # Try to infer CID from existing messages
                    batch_cid = messages[0].link.split('/')[4] if messages and '/c/' in messages[0].link else None
                    if batch_cid:
                        full_cid = f"-100{batch_cid}" if not batch_cid.startswith("-") else batch_cid
                        fetched = await self.scraper.get_message_by_id(full_cid, msg_id, topic_id=topic_id)
                        if fetched:
                            logger.info(f"Successfully repaired link for message {msg_id}")
                            grounded_links.append(link)
                        else:
                            logger.warning(f"Removing hallucinated/missing link: {link.url}")
                    else:
                        logger.warning(f"Removing link (could not resolve channel): {link.url}")
                else:
                    logger.warning(f"Removing hallucinated link: {link.title} ({link.url})")
            
            # Ground Item Links
            for item in verified_data.verified_items:
                valid_item_links = []
                for ilink in item.links:
                    if ilink in link_map.values() or ilink in all_raw_urls:
                        valid_item_links.append(ilink)
                    else:
                        logger.warning(f"Removing hallucinated item link from {item.title}: {ilink}")
                item.links = valid_item_links

            if verified_data.corrections_made:
                logger.info(f"Verifier made {len(verified_data.corrections_made)} corrections.")

            # 6. Map to ChannelDigest
            return ChannelDigest(
                channel_name=topic_title,
                date=digest_date,
                summaries=[verified_data.verified_summary],
                items=verified_data.verified_items,
                action_items=verified_data.verified_action_items,
                key_links=grounded_links,
            )

        except Exception as e:
            logger.error(f"Error during summarization: {e}", exc_info=True)
            return ChannelDigest(
                channel_name="Error Notice",
                date=datetime.date.today(),
                summaries=[
                    "### ⚠️ Summarization Incomplete",
                    "We encountered an issue while processing the messages for this digest (likely a service rate limit or connection timeout).",
                    "Please check the system logs for technical details or try running the command again later."
                ],
            )
