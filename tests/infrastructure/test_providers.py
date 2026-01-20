import unittest
from unittest.mock import MagicMock, patch

from telebot.infrastructure.providers.gemini_provider import GeminiProvider
from telebot.infrastructure.providers.groq_provider import GroqProvider
from telebot.infrastructure.agents import SummarizerOutputSchema


class TestProviders(unittest.TestCase):
    @patch("telebot.infrastructure.providers.gemini_provider.genai.Client")
    def test_gemini_provider_init(self, MockClient):
        provider = GeminiProvider("fake_key")
        MockClient.assert_called_once_with(api_key="fake_key")

    @patch("telebot.infrastructure.providers.gemini_provider.genai.Client")
    def test_gemini_generate_structured(self, MockClient):
        mock_client_inst = MockClient.return_value
        mock_response = MagicMock()
        mock_response.parsed = SummarizerOutputSchema(items=[], key_links=[], action_items=[])
        mock_client_inst.models.generate_content.return_value = mock_response

        provider = GeminiProvider("fake_key")
        result = provider.generate_structured(
            model_id="gemini-pro",
            system_prompt="prompt",
            input_data="{}",
            output_schema=SummarizerOutputSchema,
        )

        self.assertEqual(result, mock_response.parsed)
        mock_client_inst.models.generate_content.assert_called_once()

    @patch("telebot.infrastructure.providers.groq_provider.Groq")
    def test_groq_provider_init(self, MockGroq):
        provider = GroqProvider("fake_key")
        MockGroq.assert_called_once_with(api_key="fake_key")

    @patch("telebot.infrastructure.providers.groq_provider.Groq")
    def test_groq_generate_structured(self, MockGroq):
        mock_client_inst = MockGroq.return_value
        mock_output_obj = MagicMock()
        mock_output_obj.choices[0].message.content = '{"items":[], "key_links":[], "action_items":[]}'
        mock_client_inst.chat.completions.create.return_value = mock_output_obj

        provider = GroqProvider("fake_key")
        result = provider.generate_structured(
            model_id="llama3",
            system_prompt="prompt",
            input_data="{}",
            output_schema=SummarizerOutputSchema,
        )

        self.assertIsInstance(result, SummarizerOutputSchema)
        mock_client_inst.chat.completions.create.assert_called_once()
