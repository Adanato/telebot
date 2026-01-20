"""
Simplified 3-Agent Synchronous Architecture for Telebot.

Agents:
- SummarizerAgent: Takes raw messages, produces a structured digest.
- VerifierAgent: Cross-references summary with original messages.
"""

from typing import List, Optional, Type
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from enum import Enum
import logging
import time
import os

from telebot.domain.services import AIProvider
from telebot.infrastructure.providers.gemini_provider import GeminiProvider
from telebot.infrastructure.providers.groq_provider import GroqProvider

logger = logging.getLogger(__name__)

class GeminiModel(str, Enum):
    """Verified model identifiers for the native google-genai SDK."""
    FLASH_3_PREVIEW = "gemini-3-flash-preview"
    PRO_3_PREVIEW = "gemini-3-pro-preview"
    FLASH_2_0 = "gemini-2.0-flash"
    FLASH_2_0_EXP = "gemini-2.0-flash-exp"
    FLASH_1_5_LATEST = "gemini-flash-latest"
    PRO_1_5_LATEST = "gemini-pro-latest"
    FLASH_LITE_2_0 = "gemini-2.0-flash-lite"

class GroqModel(str, Enum):
    """Verified model identifiers for the Groq SDK."""
    GPT_OSS_120B = "openai/gpt-oss-120b"
    LLAMA_3_3_70B = "llama-3.3-70b-versatile"
    LLAMA_3_1_8B = "llama-3.1-8b-instant"
    GPT_OSS_20B = "openai/gpt-oss-20b"
    QWEN_3_32B = "qwen/qwen3-32b"

# --- Schemas ---

class StructuredMessage(BaseModel):
    """A single message from the chat."""
    id: int = Field(..., description="Unique message ID")
    author: Optional[str] = Field(None, description="Username/Name of sender")
    content: str = Field(..., description="Message text content")
    timestamp: str = Field(..., description="ISO formatted timestamp")
    link: Optional[str] = Field(None, description="Direct link to the message")
    reply_to_id: Optional[int] = Field(None, description="ID of message being replied to")
    forward_from: Optional[str] = Field(None, description="Original author if forwarded")


class SummarizerInputSchema(BaseModel):
    """Input for the Summarizer Agent."""
    messages: List[StructuredMessage] = Field(..., description="List of messages to process")
    topic_context: str = Field(..., description="Topic ID and title for context")
    chat_message: str = Field(default="Summarize these messages into a digest.", description="Instruction")


from telebot.domain.models import DigestItem, LinkItem, TelegramMessage


class SummarizerOutputSchema(BaseModel):
    """Output from the Summarizer Agent."""
    executive_summary: str = Field(..., description="A 2-3 sentence overview of the chat activity")
    items: List[DigestItem] = Field(default_factory=list, description="Extracted items from the chat")
    key_links: list[LinkItem] = Field(default_factory=list, description="All important URLs mentioned")
    action_items: List[str] = Field(default_factory=list, description="Potential tasks identified")


class VerifierInputSchema(BaseModel):
    """Input for the Verifier Agent."""
    original_messages: str = Field(..., description="The raw messages from the chat (ID, Author, Content)")
    summarizer_output: SummarizerOutputSchema = Field(..., description="The output from the Summarizer")
    chat_message: str = Field(default="Verify this summary against the raw messages.", description="Instruction")


class VerifierOutputSchema(BaseModel):
    """Output from the Verifier Agent."""
    verified_summary: str = Field(..., description="The verified executive summary")
    verified_items: list[DigestItem] = Field(default_factory=list, description="Items confirmed to exist in source")
    verified_links: list[LinkItem] = Field(default_factory=list, description="Links confirmed to exist in source")
    verified_action_items: list[str] = Field(default_factory=list, description="Action items confirmed")
    corrections_made: list[str] = Field(default_factory=list, description="Log of what was fixed or removed")

# --- Synchronous Rate Limiter ---

class RateLimiter:
    """
    Synchronous Rate Limiter for AI API calls.
    Enforces a maximum number of requests per minute (RPM).
    """
    def __init__(self, rpm: int = 10):
        self.rpm = rpm
        self.interval = 60.0 / rpm
        self.last_request_time = 0.0

    def acquire(self):
        """Wait if necessary to comply with the rate limit."""
        current_time = time.time()
        elapsed = current_time - self.last_request_time
        
        if elapsed < self.interval:
            wait_time = self.interval - elapsed
            logger.debug(f"Rate limit: waiting {wait_time:.2f}s")
            time.sleep(wait_time)
        
        self.last_request_time = time.time()


# --- Generic AI Agent ---

class AIAgent:
    """Provider-agnostic agent that uses an AIProvider for execution."""
    
    def __init__(self, provider: AIProvider, model: str, system_prompt: str, output_schema: Type[BaseModel], rate_limiter: RateLimiter):
        self.provider = provider
        self.model = model
        self.system_prompt = system_prompt
        self.output_schema = output_schema
        self.rate_limiter = rate_limiter

    def run(self, input_data: BaseModel) -> BaseModel:
        """Execute the agent using the injected provider."""
        retries = 0
        max_retries = 5
        
        while retries < max_retries:
            try:
                self.rate_limiter.acquire()
                logger.info(f"Agent {self.model} starting request (Attempt {retries + 1})...")
                
                logger.debug(f"Agent {self.model} input data: {input_data.model_dump_json()}")
                
                result = self.provider.generate_structured(
                    model_id=self.model,
                    system_prompt=self.system_prompt,
                    input_data=input_data.model_dump_json(),
                    output_schema=self.output_schema
                )
                
                logger.debug(f"Agent {self.model} raw result: {result}")
                logger.info(f"Agent {self.model} request completed.")
                return result
                
            except Exception as e:
                error_str = str(e).upper()
                # Handle Rate Limits (429) across providers
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str or "RATE_LIMIT" in error_str:
                    retries += 1
                    wait_time = 65.0
                    logger.warning(f"Rate limit hit. Sleeping {wait_time}s before retry {retries}/{max_retries}...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Unexpected error in agent {self.model}: {e}")
                    raise e
                    
        raise Exception("Max retries exceeded for Rate Limit")


# --- Agent Orchestrator ---

class AgentOrchestrator:
    """Manages interchangeable AI providers and agents."""
    
    def __init__(self, gemini_key: str, groq_key: Optional[str] = None, preferred_provider: str = "gemini"):
        self.providers = {
            "gemini": GeminiProvider(gemini_key)
        }
        if groq_key:
            self.providers["groq"] = GroqProvider(groq_key)
            
        self.preferred_provider = preferred_provider if preferred_provider in self.providers else "gemini"
        logger.debug(f"Orchestrator initialized with provider: {self.preferred_provider}")
        
        # Configure models and rate limits based on provider
        if self.preferred_provider == "groq":
            self.model = GroqModel.GPT_OSS_120B
            self.rate_limiter = RateLimiter(rpm=30) # Groq is generous
        else:
            self.model = GeminiModel.FLASH_3_PREVIEW
            self.rate_limiter = RateLimiter(rpm=2)

    def _get_agent(self, system_prompt: str, output_schema: Type[BaseModel]) -> AIAgent:
        return AIAgent(
            self.providers[self.preferred_provider],
            self.model,
            system_prompt,
            output_schema,
            self.rate_limiter
        )

    def get_summarizer_agent(self) -> AIAgent:
        return self._get_agent(
            """You are a Telegram Chat Summarizer. Analyze messages and extract:
1. Executive summary (2-3 sentences)
2. Digest items (courses, files, discussions, requests, announcements)
3. Key links
4. Action items

CRITICAL: Every item and link MUST be anchored in a specific message ID from the input. 
Do not hallucinate links that are not present.
Return as valid JSON matching the schema.""",
            SummarizerOutputSchema
        )

    def get_verifier_agent(self) -> AIAgent:
        return self._get_agent(
            """You are a Fact-Checking Verifier. 
Cross-reference the summary with raw messages to ensure:
1. Contextual accuracy (did this person really say this?)
2. Logical consistency (do the action items match the discussion?)
3. No hallucinated claims.

NOTE: Technical link existence is checked by a programmatic parser, so focus your energy on CONTEXT and CORRECTNESS of the summarization.
Return ONLY verified data as valid JSON matching the schema.""",
            VerifierOutputSchema
        )
