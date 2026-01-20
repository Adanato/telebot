import logging

from google import genai
from google.genai import types

from telebot.domain.services import AIProvider

logger = logging.getLogger(__name__)


class GeminiProvider(AIProvider):
    def __init__(self, api_key: str):
        """Initialize with Gemini API key."""
        self.client = genai.Client(api_key=api_key)

    def generate_structured(
        self, model_id: str, system_prompt: str, input_data: str, output_schema: type
    ) -> any:
        """Generate structured output using the google-genai SDK."""
        response = self.client.models.generate_content(
            model=model_id,
            contents=[f"System: {system_prompt}", f"Input Data: {input_data}"],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=output_schema,
            ),
        )

        if hasattr(response, "parsed") and response.parsed:
            return response.parsed

        # Fallback to model_validate_json if .parsed is not populated
        return output_schema.model_validate_json(response.text)
