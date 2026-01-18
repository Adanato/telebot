from typing import List, Optional
from pydantic import BaseModel, Field
from atomic_agents.agents.base_chat_agent import BaseChatAgent, BaseChatAgentConfig, BaseChatAgentInputSchema
import instructor
import tenacity
from google import genai
import google.api_core.exceptions
import logging

logger = logging.getLogger(__name__)

from enum import Enum

# --- Schemas ---

class MessageType(str, Enum):
    TEXT = "text"
    LINK = "link"
    FILE = "file"
    SHARE = "share"

class AgentType(str, Enum):
    COURSE = "course"
    DISCUSSION = "discussion"
    FILE = "file"
    REQUEST = "request"
    UNKNOWN = "discussion" # Default fallback

class RouterOutputSchema(BaseModel):
    agent_types: List[AgentType] = Field(..., description="The list of best suited agents for this topic")
    reasoning: str = Field(..., description="Why these agents were selected")

class RouterInputSchema(BaseChatAgentInputSchema):
    topic_title: str = Field(..., description="The title of the telegram topic")
    recent_messages: str = Field(..., description="A sample of recent messages to help identify context")
    chat_message: str = Field(default="Route this topic", description="Instruction")

class StructuredMessage(BaseModel):
    id: int = Field(..., description="Unique message ID")
    role: str = Field("user", description="Role of sender")
    author: Optional[str] = Field(None, description="Username/Name of sender")
    content: str = Field(..., description="Message text content")
    timestamp: str = Field(..., description="ISO formatted timestamp")
    message_type: MessageType = Field(MessageType.TEXT, description="Type of message")
    reply_to_id: Optional[int] = Field(None, description="ID of message being replied to")
    forward_from: Optional[str] = Field(None, description="Original author if forwarded")

class StructuredInputSchema(BaseChatAgentInputSchema):
    messages: List[StructuredMessage] = Field(..., description="List of messages to process")
    chat_message: str = Field(default="Process these messages", description="Instruction")

class CourseInfo(BaseModel):
    title: str = Field(..., description="The title of the course")
    author: Optional[str] = Field(None, description="The instructor or creator")
    platform: Optional[str] = Field(None, description="Where the course is hosted e.g. Coloso, Wingfox")
    links: List[str] = Field(default_factory=list, description="Direct or info links")
    description: Optional[str] = Field(None, description="Brief gist of the course")

class CourseExtractionSchema(BaseModel):
    """Schema for extracting course information and announcements."""
    courses: List[CourseInfo]
    announcements: List[str]

class Resource(BaseModel):
    name: str = Field(..., description="Name of the tool, resource, or link")
    url: Optional[str] = Field(None, description="URL if available")

class DiscussionPoint(BaseModel):
    theme: str = Field(..., description="The main theme of a discussion thread")
    summary: str = Field(..., description="A concise summary of what was discussed")
    mentions: List[str] = Field(default_factory=list, description="Artists or users mentioned")
    resources: List[Resource] = Field(default_factory=list, description="Links or names of tools shared")

class DiscussionExtractionSchema(BaseModel):
    """Schema for extracting key discussion points from a chat."""
    discussion_points: List[DiscussionPoint]

class FileItem(BaseModel):
    filename: str = Field(..., description="Name or description of the file")
    host: Optional[str] = Field(None, description="The file host e.g. Mega, Google Drive, Baidu")
    size: Optional[str] = Field(None, description="File size if mentioned")
    access_link: Optional[str] = Field(None, description="Link to access or request the file")

class FileExtractionSchema(BaseModel):
    """Schema for inventorying shared files."""
    files: List[FileItem]
    mirrors_needed: List[str] = Field(default_factory=list, description="Files explicitly needing mirrors")

class RequestItem(BaseModel):
    subject: str = Field(..., description="What is being requested e.g. a course, a book, a brush")
    requester: Optional[str] = Field(None, description="Who made the request")
    status: str = Field("Pending", description="Status of the request e.g. Pending, Filled, Discussion")
    context: Optional[str] = Field(None, description="Any additional context provided for the request")

class RequestExtractionSchema(BaseModel):
    """Schema for tracking community requests."""
    requests: List[RequestItem]
    urgent_requests: List[str] = Field(default_factory=list, description="Requests that seem high priority or time-sensitive")

class FormatterInputSchema(BaseChatAgentInputSchema):
    """Aggregated input for the Formatter Agent."""
    topic_context: str = Field(..., description="Topic title and date")
    courses: List[CourseInfo] = Field(default_factory=list)
    discussion_points: List[DiscussionPoint] = Field(default_factory=list)
    files: List[FileItem] = Field(default_factory=list)
    requests: List[RequestItem] = Field(default_factory=list)
    raw_announcements: List[str] = Field(default_factory=list)
    chat_message: str = Field(default="Format this data into a cohesive report", description="Instruction")

class ReportSection(BaseModel):
    title: str = Field(..., description="Section title e.g. 'Daily Highlights', 'New Courses'")
    content: str = Field(..., description="Markdown formatted content for this section")
    
class ReportOutputSchema(BaseModel):
    """Final unstructured report schema."""
    title: str = Field(..., description="Title of the report")
    introduction: str = Field(..., description="Brief intro or executive summary")
    sections: List[ReportSection] = Field(default_factory=list)
    conclusion: Optional[str] = Field(None, description="Closing remarks or summary")

class VerifierInputSchema(BaseChatAgentInputSchema):
    """Input for the Verifier Agent: Raw messages + Extracted Data."""
    original_messages: str = Field(..., description="The raw messages from the chat (ID, Author, Content)")
    extracted_courses: List[CourseInfo] = Field(default_factory=list)
    extracted_discussion_points: List[DiscussionPoint] = Field(default_factory=list)
    extracted_files: List[FileItem] = Field(default_factory=list)
    extracted_requests: List[RequestItem] = Field(default_factory=list)
    chat_message: str = Field(default="Verify this extracted data against the raw messages.", description="Instruction")

class VerifierOutputSchema(BaseModel):
    """Verified data stream."""
    verified_courses: List[CourseInfo] = Field(default_factory=list)
    verified_discussion_points: List[DiscussionPoint] = Field(default_factory=list)
    verified_files: List[FileItem] = Field(default_factory=list)
    verified_requests: List[RequestItem] = Field(default_factory=list)
    corrections_made: List[str] = Field(default_factory=list, description="Log of what was fixed or removed")

# --- Agents ---

# ... (create_agent)

# ... (GeminiChatAgent)

# ... (AgentOrchestrator)

    def get_verifier_agent(self):
        return create_agent(
            self.client,
            self.model,
            "You are a Fact-Checking Verifier. "
            "Your job is to cross-reference extracted data with the RAW chat messages. "
            "1. Remove items that are NOT in the raw messages (hallucinations). "
            "2. Ensure links are correct and attributed to the real author. "
            "3. Fix any 'N/A' or missing fields if the info exists in the text. "
            "Return ONLY the verified and corrected data.",
            VerifierOutputSchema,
            VerifierInputSchema
        )

# ... (create_agent function)

# ... (GeminiChatAgent class)

# ... (AgentOrchestrator class existing methods)

    def get_formatter_agent(self):
        return create_agent(
            self.client,
            self.model,
            "You are a tailored Report Generator. "
            "Your job is to take raw data extracted from a chat (courses, discussions, files) "
            "and weave it into a professional, readable Daily Digest in Markdown format. "
            "Use sections like '🎨 New Courses', '🗣️ Trending Discussions', '📂 Shared Resources'. "
            "Be concise but engaging. Use emojis where appropriate to make it feel alive.",
            ReportOutputSchema,
            FormatterInputSchema
        )

from telebot.infrastructure.rate_limiter import RateLimiter

def create_agent(client, model_name: str, system_prompt: str, output_schema: type[BaseModel], input_schema: type[BaseModel] | None = None, rate_limiter: RateLimiter | None = None):
    config = BaseChatAgentConfig(
        client=client,
        model=model_name,
        system_prompt_generator=None,
        output_schema=output_schema,
        input_schema=input_schema,
    )
    # Using a custom class to fix compatibility with instructor/google.genai
    agent = GeminiChatAgent(config)
    agent.system_prompt_generator.system_prompt = system_prompt
    agent.rate_limiter = rate_limiter # Inject rate limiter
    return agent

class GeminiChatAgent(BaseChatAgent):
    rate_limiter: Optional[RateLimiter] = None # Type hint

    @tenacity.retry(
        wait=tenacity.wait_exponential(multiplier=1, min=4, max=60),
        stop=tenacity.stop_after_attempt(5),
        retry=tenacity.retry_if_exception_type((
            google.api_core.exceptions.ResourceExhausted,
            google.api_core.exceptions.ServiceUnavailable,
        )),
        before_sleep=lambda retry_state: logger.warning(f"Rate limit exceeded (attempt {retry_state.attempt_number}). Retrying in {retry_state.next_action.sleep}s...")
    )
    def get_response(self, response_model=None):
        if response_model is None:
            response_model = self.output_schema

        # Rate Limiting
        if self.rate_limiter:
            self.rate_limiter.acquire()

        # Convert atomic_agents Message objects to dicts for instructor compatibility
        history = [m.model_dump(exclude_none=True) for m in self.memory.get_history()]
        
        # Ensure system prompt is also a dict
        messages = [{'role': 'system', 'content': self.get_system_prompt()}] + history
        
        logger.info(f"Agent {self.model} starting request...")
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            response_model=response_model
        )
        logger.info(f"Agent {self.model} request completed.")
        return response

class AgentOrchestrator:
    def __init__(self, api_key: str):
        self.client = instructor.from_genai(
            client=genai.Client(api_key=api_key),
            mode=instructor.Mode.GENAI_STRUCTURED_OUTPUTS,
        )
        self.model = "gemini-3-flash-preview"
        self.rate_limiter = RateLimiter(rpm=10) # Shared singleton, conservative 10 RPM

    def get_course_agent(self):
        return create_agent(
            self.client,
            self.model,
            "You are an expert curator for digital courses. Extract all mentioned courses with their details.",
            CourseExtractionSchema,
            StructuredInputSchema,
            rate_limiter=self.rate_limiter
        )

    def get_discussion_agent(self):
        return create_agent(
            self.client,
            self.model,
            "You are a community manager. Identify the main threads of discussion and key resources shared.",
            DiscussionExtractionSchema,
            StructuredInputSchema,
            rate_limiter=self.rate_limiter
        )

    def get_file_agent(self):
        return create_agent(
            self.client,
            self.model,
            "You are a file archivist. Create a precise inventory of all files shared and their hosting status.",
            FileExtractionSchema,
            StructuredInputSchema,
            rate_limiter=self.rate_limiter
        )

    def get_request_agent(self):
        return create_agent(
            self.client,
            self.model,
            "You are a ticket manager. Extract all requests made by users and their current status.",
            RequestExtractionSchema,
            StructuredInputSchema,
            rate_limiter=self.rate_limiter
        )

    def get_verifier_agent(self):
        return create_agent(
            self.client,
            self.model,
            "You are a Fact-Checking Verifier. "
            "Your job is to cross-reference extracted data with the RAW chat messages. "
            "1. Remove items that are NOT in the raw messages (hallucinations). "
            "2. Ensure links are correct and attributed to the real author. "
            "3. Fix any 'N/A' or missing fields if the info exists in the text. "
            "Return ONLY the verified and corrected data.",
            VerifierOutputSchema,
            VerifierInputSchema,
            rate_limiter=self.rate_limiter
        )

    def get_formatter_agent(self):
        return create_agent(
            self.client,
            self.model,
            "You are a tailored Report Generator. "
            "Your job is to take raw data extracted from a chat (courses, discussions, files) "
            "and weave it into a professional, readable Daily Digest in Markdown format. "
            "Use sections like '🎨 New Courses', '🗣️ Trending Discussions', '📂 Shared Resources'. "
            "Be concise but engaging. Use emojis where appropriate to make it feel alive.",
            ReportOutputSchema,
            FormatterInputSchema,
            rate_limiter=self.rate_limiter
        )

    def get_router_agent(self):
        return create_agent(
            self.client,
            self.model,
            "You are a Master Dispatcher. Analyze the topic title and messages to select the best worker agent(s). \n"
            "You can select MULTIPLE agents if the topic covers multiple categories.\n"
            "Rules:\n"
            "- 'Course Agent': For topics about digital courses, tutorials, or learning materials (e.g. 'Coloso', 'Wingfox').\n"
            "- 'Discussion Agent': For general chat, artist discussions, Q&A, or topics not fitting other categories.\n"
            "- 'File Agent': For topics dedicated to sharing specific files, assets, or huge dumps.\n"
            "- 'Request Agent': For topics specifically for asking/requesting items (e.g. 'Book Request', 'Brush Request').",
            RouterOutputSchema,
            RouterInputSchema,
            rate_limiter=self.rate_limiter
        )

    def route_topic(self, topic_title: str, messages: List[StructuredMessage]) -> List[AgentType]:
        router = self.get_router_agent()
        
        # Create a sample context string
        sample_msgs = "\n".join([f"{m.author}: {m.content[:100]}" for m in messages[:5]])
        
        input_data = RouterInputSchema(
            topic_title=topic_title,
            recent_messages=sample_msgs or "No recent messages"
        )
        
        try:
            response = router.run(input_data)
            return response.agent_types
        except Exception as e:
            print(f"Routing failed: {e}. Defaulting to DISCUSSION.")
            return [AgentType.DISCUSSION]
