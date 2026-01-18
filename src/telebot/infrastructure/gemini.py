import datetime

from telebot.domain.models import ChannelDigest, TelegramMessage
from telebot.domain.services import SummarizerInterface
from telebot.infrastructure.agents import AgentOrchestrator
import logging
import asyncio

logger = logging.getLogger(__name__)


class GeminiSummarizer(SummarizerInterface):
    def __init__(self, api_key: str):
        self.orchestrator = AgentOrchestrator(api_key)

    async def summarize(
        self, messages: list[TelegramMessage], topic_id: int | None = None
    ) -> ChannelDigest:
        try:
            # Prepare Structured Input
            from telebot.infrastructure.agents import StructuredInputSchema, StructuredMessage, MessageType, AgentType
            
            # ... (Structured Message Construction)
            structured_messages = []
            for m in messages:
                msg_type = MessageType.TEXT
                if "t.me" in m.link: 
                    pass 
                
                structured_messages.append(
                    StructuredMessage(
                        id=m.id,
                        role="user", 
                        author=m.author or "Unknown",
                        content=str(m.text) if m.text else "[Media/File]",
                        timestamp=str(m.date),
                        message_type=msg_type,
                        reply_to_id=m.reply_to_id,
                        forward_from=m.forward_from_author
                    )
                )
                
            topic_title_guess = f"Topic {topic_id}"
            
            # Dynamic Routing
            logger.info(f"Routing topic {topic_id}...")
            selected_agent_types = await asyncio.to_thread(self.orchestrator.route_topic, topic_title_guess, structured_messages)
            logger.info(f"Router selected agents: {selected_agent_types} for topic {topic_id}")

            agents = []
            for agent_type in selected_agent_types:
                if agent_type == AgentType.COURSE:
                    logger.info("Initializing Course Agent...")
                    agents.append(self.orchestrator.get_course_agent())
                elif agent_type == AgentType.DISCUSSION:
                    logger.info("Initializing Discussion Agent...")
                    agents.append(self.orchestrator.get_discussion_agent())
                elif agent_type == AgentType.FILE:
                    logger.info("Initializing File Agent...")
                    agents.append(self.orchestrator.get_file_agent())
                elif agent_type == AgentType.REQUEST:
                    logger.info("Initializing Request Agent...")
                    agents.append(self.orchestrator.get_request_agent())
            
            if not agents:
                logger.warning("No agents selected by Router. Defaulting to Discussion Agent.")
                agents.append(self.orchestrator.get_discussion_agent())

            input_data = StructuredInputSchema(
                messages=structured_messages,
                chat_message=f"Analyze these {len(messages)} messages from the chat."
            )

            # DEBUG: Save structured input to file
            with open("debug_structured_input.json", "w", encoding="utf-8") as f:
                f.write(input_data.model_dump_json(indent=2))

            # Accumulators for Formatter
            agg_courses = []
            agg_discussion_points = []
            agg_files = []
            agg_requests = []
            agg_announcements = []
            
            digest_date = datetime.date.today()
            channel_name = f"Topic {topic_id or 'General'}"

            # 1. Run Specialist Agents
            for agent in agents:
                response = await asyncio.to_thread(agent.run, input_data)
                
                if hasattr(response, 'courses'):
                    agg_courses.extend(response.courses)
                    agg_announcements.extend(response.announcements)
                elif hasattr(response, 'discussion_points'):
                    agg_discussion_points.extend(response.discussion_points)
                elif hasattr(response, 'files'):
                    agg_files.extend(response.files)
                    agg_announcements.extend(response.mirrors_needed)
                elif hasattr(response, 'requests'):
                    agg_requests.extend(response.requests)
                    agg_announcements.extend(response.urgent_requests)
            
            logger.info("Specialist extraction complete.")

            # 2. Run Verifier Agent
            from telebot.infrastructure.agents import FormatterInputSchema, VerifierInputSchema

            # Construct inputs for verifier
            raw_msg_str = "\n".join([f"ID:{m.id} Author:{m.author} Content:{m.content[:200]}" for m in structured_messages])

            verifier_input = VerifierInputSchema(
                original_messages=raw_msg_str,
                extracted_courses=agg_courses,
                extracted_discussion_points=agg_discussion_points,
                extracted_files=agg_files,
                extracted_requests=agg_requests,
                chat_message="Double check the links and context."
            )
            
            verifier = self.orchestrator.get_verifier_agent()
            verified_data = await asyncio.to_thread(verifier.run, verifier_input)
            
            if verified_data.corrections_made:
                print(f"DEBUG: Verifier corrections: {verified_data.corrections_made}")

            # 3. Run Formatter Agent
            formatter_input = FormatterInputSchema(
                topic_context=f"Topic ID: {topic_id} - Title: {topic_title_guess} - Date: {digest_date}",
                courses=verified_data.verified_courses,
                discussion_points=verified_data.verified_discussion_points,
                files=verified_data.verified_files,
                requests=verified_data.verified_requests,
                raw_announcements=agg_announcements, 
                chat_message="Create a comprehensive daily digest from this verified data. Use the links provided."
            )
            
            formatter = self.orchestrator.get_formatter_agent()
            report = await asyncio.to_thread(formatter.run, formatter_input)
            
            # 4. Map Report to ChannelDigest
            summaries = []
            summaries.append(f"# {report.title}\n\n{report.introduction}")
            for section in report.sections:
                summaries.append(f"## {section.title}\n\n{section.content}")
            if report.conclusion:
                summaries.append(f"### Conclusion\n\n{report.conclusion}")

            all_key_links = []
            for c in verified_data.verified_courses: all_key_links.extend(c.links)
            for dp in verified_data.verified_discussion_points: 
                for r in dp.resources:
                    if r.url: all_key_links.append(r.url) 
            for f in verified_data.verified_files: 
                if f.access_link: all_key_links.append(f.access_link)

            return ChannelDigest(
                channel_name=channel_name,
                date=digest_date,
                summaries=summaries,
                action_items=agg_announcements, 
                key_links=list(set(all_key_links)),
            )
        except Exception as e:
            logger.error(f"Error during summarization: {e}")
            return ChannelDigest(
                channel_name="Error",
                date=datetime.date.today(),
                summaries=[f"Error during agent execution: {str(e)}"],
            )
