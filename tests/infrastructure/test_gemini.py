import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from telebot.infrastructure.gemini import GeminiSummarizer
from telebot.domain.models import TelegramMessage
from telebot.infrastructure.agents import (
    AgentType, CourseInfo, ReportOutputSchema, ReportSection, 
    VerifierOutputSchema, CourseExtractionSchema
)

class TestGeminiSummarizer(unittest.IsolatedAsyncioTestCase):
    @patch('telebot.infrastructure.gemini.AgentOrchestrator')
    @patch('telebot.infrastructure.gemini.asyncio.to_thread')
    async def test_summarize_flow(self, mock_to_thread, MockOrchestrator):
        mock_to_thread.side_effect = lambda f, *args: f(*args)

        mock_orch = MockOrchestrator.return_value
        
        # Mock agents
        mock_course_agent = MagicMock()
        mock_verifier = MagicMock()
        mock_formatter = MagicMock()
        
        mock_orch.route_topic.return_value = [AgentType.COURSE]
        mock_orch.get_course_agent.return_value = mock_course_agent
        mock_orch.get_verifier_agent.return_value = mock_verifier
        mock_orch.get_formatter_agent.return_value = mock_formatter
        
        # Setup returns with REAL Models
        # Course Agent Return
        mock_course_data = CourseExtractionSchema(
            courses=[CourseInfo(title="C1", links=["http://l1.com"])],
            announcements=["A1"]
        )
        mock_course_agent.run.return_value = mock_course_data
        
        # Verifier Return
        mock_verified_data = VerifierOutputSchema(
            verified_courses=[CourseInfo(title="C1", links=["http://l1.com"])],
            verified_discussion_points=[],
            verified_files=[],
            verified_requests=[],
            corrections_made=[],
        )
        mock_verifier.run.return_value = mock_verified_data
        
        # Formatter Return
        mock_report = ReportOutputSchema(
            title="Digest Title",
            introduction="Intro",
            sections=[ReportSection(title="S1", content="C1")],
            conclusion="Conc",
        )
        mock_formatter.run.return_value = mock_report
        
        # Execute
        summarizer = GeminiSummarizer(api_key="fake")
        messages = [TelegramMessage(id=1, text="msg", date="2023-01-01", link="link")]
        
        digest = await summarizer.summarize(messages, topic_id=123)
        
        # Verify
        mock_orch.route_topic.assert_called_once()
        mock_course_agent.run.assert_called_once()
        mock_verifier.run.assert_called_once()
        mock_formatter.run.assert_called_once()
        
        self.assertEqual(digest.channel_name, "Topic 123")
        self.assertIn("Digest Title", digest.summaries[0])
        self.assertIn("http://l1.com", digest.key_links)

    @patch('telebot.infrastructure.gemini.AgentOrchestrator')
    @patch('telebot.infrastructure.gemini.asyncio.to_thread')
    async def test_summarize_error_handling(self, mock_to_thread, MockOrchestrator):
        mock_to_thread.side_effect = lambda f, *args: f(*args)
        
        mock_orch = MockOrchestrator.return_value
        mock_orch.route_topic.side_effect = Exception("API Error")
        
        summarizer = GeminiSummarizer(api_key="fake")
        messages = [TelegramMessage(id=1, text="msg", date="2023-01-01", link="link")]
        
        digest = await summarizer.summarize(messages, topic_id=123)
        
        self.assertEqual(digest.channel_name, "Error")
        self.assertIn("API Error", digest.summaries[0])
