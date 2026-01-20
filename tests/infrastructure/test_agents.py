import unittest
from unittest.mock import MagicMock, patch
from telebot.infrastructure.agents import AgentOrchestrator, AIAgent, GeminiModel, GroqModel, RateLimiter, SummarizerOutputSchema

class TestAgentOrchestrator(unittest.TestCase):
    def setUp(self):
        self.gemini_key = "fake_gemini"
        self.groq_key = "fake_groq"
        # Patch providers
        with patch("telebot.infrastructure.agents.GeminiProvider"), \
             patch("telebot.infrastructure.agents.GroqProvider"):
            self.orch = AgentOrchestrator(self.gemini_key, self.groq_key, preferred_provider="gemini")

    def test_get_agents_gemini(self):
        summarizer = self.orch.get_summarizer_agent()
        self.assertIsInstance(summarizer, AIAgent)
        self.assertEqual(summarizer.model, GeminiModel.FLASH_3_PREVIEW)
        
    def test_get_agents_groq(self):
        with patch("telebot.infrastructure.agents.GeminiProvider"), \
             patch("telebot.infrastructure.agents.GroqProvider"):
            orch_groq = AgentOrchestrator(self.gemini_key, self.groq_key, preferred_provider="groq")
            summarizer = orch_groq.get_summarizer_agent()
            self.assertEqual(summarizer.model, GroqModel.LLAMA_3_70B)
            self.assertEqual(summarizer.rate_limiter.rpm, 30)

class TestAIAgent(unittest.TestCase):
    def setUp(self):
        self.mock_provider = MagicMock()
        self.rate_limiter = MagicMock(spec=RateLimiter)
        self.agent = AIAgent(
            self.mock_provider,
            "test-model",
            "System Prompt",
            SummarizerOutputSchema,
            self.rate_limiter
        )

    def test_run_success(self):
        mock_output = SummarizerOutputSchema(
            executive_summary="Summary",
            items=[],
            key_links=[],
            action_items=[]
        )
        self.mock_provider.generate_structured.return_value = mock_output

        input_data = MagicMock()
        input_data.model_dump_json.return_value = "{}"
        
        result = self.agent.run(input_data)
        
        self.assertEqual(result, mock_output)
        self.rate_limiter.acquire.assert_called_once()
        self.mock_provider.generate_structured.assert_called_once()

    @patch("time.sleep")
    def test_run_rate_limit_retry(self, mock_sleep):
        mock_output = SummarizerOutputSchema(
            executive_summary="Summary",
            items=[],
            key_links=[],
            action_items=[]
        )
        
        self.mock_provider.generate_structured.side_effect = [
            Exception("429 Rate Limit Exceeded"),
            mock_output
        ]

        input_data = MagicMock()
        input_data.model_dump_json.return_value = "{}"
        
        result = self.agent.run(input_data)
        
        self.assertEqual(result, mock_output)
        self.assertEqual(self.mock_provider.generate_structured.call_count, 2)
        mock_sleep.assert_called_with(65.0)
