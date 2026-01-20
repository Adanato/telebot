import os
import pytest
from unittest import mock

@pytest.fixture(autouse=True)
def mock_env():
    """Ensure environment variables are set for tests."""
    with mock.patch.dict(os.environ, {
        "TG_API_ID": "123",
        "TG_API_HASH": "hash",
        "GEMINI_API_KEY": "gemini",
        "GROQ_API_KEY": "groq",
        "TELEGRAM_BOT_TOKEN": "bot",
        "TELEGRAM_CHAT_ID": "chat",
        "PHONE_NUMBER": "+1234567890",
    }):
        yield
