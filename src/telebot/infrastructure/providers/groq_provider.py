from groq import Groq
from telebot.domain.services import AIProvider
import logging
import json

logger = logging.getLogger(__name__)

class GroqProvider(AIProvider):
    def __init__(self, api_key: str):
        self.client = Groq(api_key=api_key)

    def generate_structured(
        self, 
        model_id: str, 
        system_prompt: str, 
        input_data: str, 
        output_schema: type
    ) -> any:
        """Generate structured output using Groq's JSON mode."""
        # Groq doesn't natively support Pydantic response_model like Gemini yet
        # unless using 'instructor', but we'll implement standard JSON mode for portability.
        # We append schema instructions to the prompt to ensure adherence.
        
        full_system_prompt = f"{system_prompt}\n\nReturn ONLY valid JSON matching this schema: {output_schema.model_json_schema()}"
        
        response = self.client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": full_system_prompt},
                {"role": "user", "content": input_data}
            ],
            response_format={"type": "json_object"}
        )
        
        content = response.choices[0].message.content
        return output_schema.model_validate_json(content)
