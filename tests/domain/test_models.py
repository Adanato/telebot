from datetime import datetime
from telebot.domain.models import TelegramMessage, ChannelDigest

def test_telegram_message_creation():
    msg = TelegramMessage(
        id=1,
        text="Hello world",
        date=datetime.now(),
        author="Test User",
        link="https://t.me/c/123/1"
    )
    assert msg.id == 1
    assert msg.text == "Hello world"
    assert msg.author == "Test User"

def test_telegram_message_optional_fields():
    msg = TelegramMessage(
        id=2,
        text=None,
        date=datetime.now(),
        link="https://t.me/c/123/2",
        reply_to_id=1
    )
    assert msg.text is None
    assert msg.reply_to_id == 1
    assert msg.local_media_path is None

def test_channel_digest_creation():
    digest = ChannelDigest(
        channel_name="Test Channel",
        date=datetime.now().date(),
        summaries=["Summary 1", "Summary 2"],
        action_items=["Action 1"],
        key_links=["http://example.com"]
    )
    assert digest.channel_name == "Test Channel"
    assert len(digest.summaries) == 2
    assert len(digest.action_items) == 1
    assert len(digest.key_links) == 1
