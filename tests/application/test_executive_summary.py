"""Tests for the executive_summary module."""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from course_scout.application.executive_summary import generate_executive_summary
from course_scout.domain.models import ChannelDigest, FileItem


def _digest(name: str, items: list) -> ChannelDigest:
    return ChannelDigest(
        channel_name=name,
        date=datetime.now().date(),
        summaries=["s"],
        items=items,
        key_links=[],
    )


class TestExecutiveSummary(unittest.IsolatedAsyncioTestCase):
    @patch("claude_agent_sdk.query")
    async def test_generates_summary_with_top5(self, mock_query):
        """Happy path: query returns text, summary is wrapped with header."""
        # Build the assistant text-block message structure that the prompt loop expects
        from claude_agent_sdk import AssistantMessage, TextBlock

        msg = AssistantMessage(
            content=[TextBlock(text="## Top 5 Finds\n\n1. ...")],
            model="claude-haiku-4-5",
            parent_tool_use_id=None,
        )

        async def _aiter(*args, **kwargs):
            yield msg

        mock_query.return_value = _aiter()

        digest_a = _digest(
            "Topic A",
            [FileItem(title="course-x", description="desc", links=["http://x"])],
        )
        result = await generate_executive_summary([("Topic A", digest_a)], "2026-05-02")

        self.assertIn("## Executive Summary", result)
        self.assertIn("## Top 5 Finds", result)
        mock_query.assert_called_once()

    @patch("claude_agent_sdk.query")
    async def test_handles_empty_results(self, mock_query):
        """Zero-item input still produces a summary header (with '(none)' block)."""
        from claude_agent_sdk import AssistantMessage, TextBlock

        msg = AssistantMessage(
            content=[TextBlock(text="No relevant items today.")],
            model="claude-haiku-4-5",
            parent_tool_use_id=None,
        )

        async def _aiter(*args, **kwargs):
            yield msg

        mock_query.return_value = _aiter()

        result = await generate_executive_summary([], "2026-05-02")
        self.assertIn("## Executive Summary", result)
        self.assertIn("No relevant items today.", result)

    @patch("claude_agent_sdk.query")
    async def test_query_with_no_text_returns_failure_message(self, mock_query):
        """If the LLM returns no AssistantMessage, the helper falls back gracefully."""

        async def _empty(*args, **kwargs):
            return
            yield  # noqa — make this an async generator

        mock_query.return_value = _empty()

        digest_a = _digest("Topic A", [])
        result = await generate_executive_summary([("Topic A", digest_a)], "2026-05-02")

        self.assertIn("Summary generation failed", result)
