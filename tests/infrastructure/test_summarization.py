import datetime
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from course_scout.domain.models import ChannelDigest, DigestItem, LinkItem, TelegramMessage
from course_scout.infrastructure.agents import SummarizerOutputSchema
from course_scout.infrastructure.summarization import OrchestratedSummarizer as Summarizer


class TestSummarizer(unittest.IsolatedAsyncioTestCase):
    @patch("course_scout.infrastructure.summarization.AgentOrchestrator")
    async def test_summarize_success(self, MockOrch):
        mock_orch = MockOrch.return_value
        summarizer = Summarizer()

        mock_summarizer_agent = MagicMock()
        mock_orch.get_summarizer_agent.return_value = mock_summarizer_agent

        sum_out = SummarizerOutputSchema(
            items=[
                DigestItem(
                    title="Item 1", description="Desc", category="course", links=["http://l1"]
                )
            ],
            key_links=[LinkItem(title="T1", url="http://l1")],
            action_items=["Task 1"],
        )
        mock_summarizer_agent.run = AsyncMock(return_value=sum_out)

        messages = [TelegramMessage(id=1, text="msg1", date=datetime.datetime.now(), link="http://l1")]
        digest = await summarizer.summarize(messages, topic_id=123)

        self.assertIsInstance(digest, ChannelDigest)
        self.assertEqual(digest.channel_name, "Topic 123")
        self.assertEqual(digest.key_links[0].title, "T1")

    @patch("course_scout.infrastructure.summarization.AgentOrchestrator")
    async def test_summarize_error_suppression(self, MockOrch):
        mock_orch = MockOrch.return_value
        mock_orch.get_summarizer_agent.side_effect = Exception("AI Overload")
        summarizer = Summarizer()
        messages = [TelegramMessage(id=1, text="msg1", date=datetime.datetime.now(), link="http://l1")]

        digest = await summarizer.summarize(messages, topic_id=123)

        self.assertEqual(digest.channel_name, "Error Notice")
        self.assertTrue(any("Summarization Incomplete" in s for s in digest.summaries))

    @patch("course_scout.infrastructure.summarization.AgentOrchestrator")
    async def test_summarize_all_sections(self, MockOrch):
        mock_orch = MockOrch.return_value
        summarizer = Summarizer()

        mock_summarizer_agent = MagicMock()
        mock_orch.get_summarizer_agent.return_value = mock_summarizer_agent

        sum_out = SummarizerOutputSchema(
            items=[
                DigestItem(title="F1", description="File", category="file", links=["http://f"]),
                DigestItem(title="D1", description="Disc", category="discussion"),
                DigestItem(title="R1", description="Req", category="request", links=["http://r"]),
            ],
            key_links=[],
            action_items=["Task 1"],
        )
        mock_summarizer_agent.run = AsyncMock(return_value=sum_out)

        messages = [TelegramMessage(id=1, text="m", date=datetime.datetime.now(), link="http://f")]
        digest = await summarizer.summarize(messages)

        full_md = digest.to_markdown()
        self.assertIn("## 📂 Files Shared", full_md)
        self.assertIn("## 🗣 Discussions", full_md)
        self.assertIn("## 🙋 Requests", full_md)
        self.assertIn("## ✅ Action Items", full_md)
