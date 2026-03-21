import unittest
from unittest.mock import patch

from course_scout.infrastructure.agents import SummarizerOutputSchema
from course_scout.infrastructure.providers.claude_provider import ClaudeProvider


class TestClaudeProvider(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.provider = ClaudeProvider()

    @patch("course_scout.infrastructure.providers.claude_provider.query")
    async def test_generate_structured_via_tool_output(self, mock_query):
        """Test structured output extracted from ToolUseBlock."""
        from claude_agent_sdk import AssistantMessage, ResultMessage, ToolUseBlock

        tool_block = ToolUseBlock(
            id="tool_1",
            name="StructuredOutput",
            input={"items": [], "key_links": [], "action_items": []},
        )
        assistant_msg = AssistantMessage(
            content=[tool_block], model="claude-sonnet-4-6"
        )
        result_msg = ResultMessage(
            subtype="result",
            duration_ms=1000,
            duration_api_ms=900,
            is_error=False,
            num_turns=1,
            session_id="test",
        )

        async def mock_query_gen(**kwargs):
            yield assistant_msg
            yield result_msg

        mock_query.side_effect = lambda **kwargs: mock_query_gen(**kwargs)

        result = await self.provider.generate_structured(
            model_id="claude-sonnet-4-6",
            system_prompt="test",
            input_data="{}",
            output_schema=SummarizerOutputSchema,
        )

        self.assertIsInstance(result, SummarizerOutputSchema)
        mock_query.assert_called_once()

    @patch("course_scout.infrastructure.providers.claude_provider.query")
    async def test_generate_structured_no_output_raises(self, mock_query):
        """Test RuntimeError raised when no output received."""
        from claude_agent_sdk import ResultMessage

        result_msg = ResultMessage(
            subtype="result",
            duration_ms=100,
            duration_api_ms=90,
            is_error=False,
            num_turns=1,
            session_id="test",
        )

        async def mock_query_gen(**kwargs):
            yield result_msg

        mock_query.side_effect = lambda **kwargs: mock_query_gen(**kwargs)

        with self.assertRaises(RuntimeError):
            await self.provider.generate_structured(
                model_id="claude-sonnet-4-6",
                system_prompt="test",
                input_data="{}",
                output_schema=SummarizerOutputSchema,
            )
