import unittest
from unittest.mock import MagicMock, patch
from telebot.infrastructure.agents import AgentOrchestrator, GeminiChatAgent
import google.api_core.exceptions

class TestAgentOrchestrator(unittest.TestCase):
    def setUp(self):
        self.api_key = "fake_key"
        # Patch everything that happens in __init__
        with patch("telebot.infrastructure.agents.instructor.from_genai") as mock_from_genai:
            self.mock_client = mock_from_genai.return_value
            self.orch = AgentOrchestrator(self.api_key)

    def test_get_agents(self):
        # We need to patch create_agent because it does Pydantic validation on the real class
        # which might fail with our mocks. Or we mock the config.
        with patch("telebot.infrastructure.agents.create_agent") as mock_create:
            mock_create.return_value = MagicMock(spec=GeminiChatAgent)
            
            agent = self.orch.get_course_agent()
            self.assertIsNotNone(agent)
            
            agent = self.orch.get_router_agent()
            self.assertIsNotNone(agent)

    def test_routing_failure_fallback(self):
        # Mock router agent run to fail
        mock_router = MagicMock()
        mock_router.run.side_effect = Exception("API Error")
        
        with patch.object(self.orch, "get_router_agent", return_value=mock_router):
            types = self.orch.route_topic("Title", [])
            # Should fallback to DISCUSSION (value 'discussion')
            self.assertEqual(len(types), 1)
            self.assertEqual(types[0].value, "discussion")

class TestGeminiChatAgent(unittest.TestCase):
    def test_retry_logic(self):
        # We need to mock the internal state of GeminiChatAgent to avoid BaseChatAgent init issues
        # Or just mock the client.
        mock_client = MagicMock()
        # Mock chat.completions.create
        mock_client.chat.completions.create.side_effect = [
            google.api_core.exceptions.ResourceExhausted("Rate Limit"),
            MagicMock(content="Success")
        ]
        
        # Create a mock agent without calling __init__ of BaseChatAgent if possible, 
        # or just mock the required fields.
        agent = MagicMock(spec=GeminiChatAgent)
        agent.client = mock_client
        agent.model = "gemini"
        agent.output_schema = MagicMock()
        agent.memory = MagicMock()
        agent.memory.get_history.return_value = []
        agent.get_system_prompt.return_value = "Prompt"
        agent.rate_limiter = None
        
        # Use the actual method but on the mock object
        with patch("tenacity.nap.time.sleep"):
            # We call the unbound method with the mock instance
            response = GeminiChatAgent.get_response(agent)
            self.assertIsNotNone(response)
            self.assertEqual(mock_client.chat.completions.create.call_count, 2)
