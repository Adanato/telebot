import datetime
import unittest
from unittest.mock import MagicMock, patch

from telebot.domain.models import ChannelDigest, TelegramMessage
from telebot.infrastructure.agents import (
    DigestItem,
    LinkItem,
    SummarizerOutputSchema,
    VerifierOutputSchema,
)
from telebot.infrastructure.summarization import OrchestratedSummarizer as Summarizer


class TestGeminiSummarizer(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.gemini_key = "fake_gemini"
        self.groq_key = "fake_groq"

    @patch("telebot.infrastructure.summarization.AgentOrchestrator")
    async def test_summarize_success(self, MockOrch):
        mock_orch = MockOrch.return_value
        summarizer = Summarizer(self.gemini_key, self.groq_key, provider="gemini")

        # Setup mocks for agents
        mock_summarizer_agent = MagicMock()
        mock_verifier_agent = MagicMock()
        mock_orch.get_summarizer_agent.return_value = mock_summarizer_agent
        mock_orch.get_verifier_agent.return_value = mock_verifier_agent

        # Summarizer output
        sum_out = SummarizerOutputSchema(
            executive_summary="Summary text",
            items=[
                DigestItem(
                    title="Item 1", description="Desc", category="course", links=["http://l1"]
                )
            ],
            key_links=[LinkItem(title="T1", url="http://l1")],
            action_items=["Task 1"],
        )
        mock_summarizer_agent.run.return_value = sum_out

        # Verifier output
        ver_out = VerifierOutputSchema(
            verified_summary="Verified Summary",
            verified_items=[
                DigestItem(
                    title="Item 1", description="Desc", category="course", links=["http://l1"]
                )
            ],
            verified_action_items=["Task 1"],
            verified_links=[LinkItem(title="T1", url="http://l1")],
            corrections_made=[],
        )
        mock_verifier_agent.run.return_value = ver_out

        messages = [TelegramMessage(id=1, text="msg1", date=datetime.datetime.now(), link="http://l1")]

        digest = await summarizer.summarize(messages, topic_id=123)

        self.assertIsInstance(digest, ChannelDigest)
        self.assertEqual(digest.channel_name, "Topic 123")
        self.assertIn("Verified Summary", digest.summaries[0])
        self.assertEqual(digest.key_links[0].title, "T1")

    @patch("telebot.infrastructure.summarization.AgentOrchestrator")
    async def test_summarize_error_suppression(self, MockOrch):
        mock_orch = MockOrch.return_value
        # Mock failure in orchestrator
        mock_orch.get_summarizer_agent.side_effect = Exception("AI Overload")
        summarizer = Summarizer(self.gemini_key)
        messages = [TelegramMessage(id=1, text="msg1", date=datetime.datetime.now(), link="http://l1")]

        digest = await summarizer.summarize(messages, topic_id=123)

        self.assertEqual(digest.channel_name, "Error Notice")
        self.assertTrue(any("Summarization Incomplete" in s for s in digest.summaries))

    @patch("telebot.infrastructure.summarization.AgentOrchestrator")
    async def test_summarize_all_sections(self, MockOrch):
        mock_orch = MockOrch.return_value
        summarizer = Summarizer(self.gemini_key, self.groq_key, provider="groq")

        mock_summarizer_agent = MagicMock()
        mock_verifier_agent = MagicMock()
        mock_orch.get_summarizer_agent.return_value = mock_summarizer_agent
        mock_orch.get_verifier_agent.return_value = mock_verifier_agent

        # Verifier output with ALL sections
        ver_out = VerifierOutputSchema(
            verified_summary="Overall summary",
            verified_items=[
                DigestItem(title="F1", description="File", category="file", links=["http://f"]),
                DigestItem(title="D1", description="Disc", category="discussion"),
                DigestItem(title="R1", description="Req", category="request", links=["http://r"]),
            ],
            verified_action_items=["Task 1"],
            verified_links=[],
            corrections_made=[],
        )
        mock_verifier_agent.run.return_value = ver_out
        mock_summarizer_agent.run.return_value = SummarizerOutputSchema(
            executive_summary="Draft", items=[], key_links=[], action_items=[]
        )

        messages = [TelegramMessage(id=1, text="m", date=datetime.datetime.now(), link="http://f")]
        digest = await summarizer.summarize(messages)

        full_md = digest.to_markdown()
        self.assertIn("## 📂 Files Shared", full_md)
        self.assertIn("## 🗣 Discussions", full_md)
        self.assertIn("## 🙋 Requests", full_md)
        self.assertIn("## ✅ Action Items", full_md)
