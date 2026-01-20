import datetime

from telebot.domain.models import ChannelDigest


def test_repro():
    # Case 1: normal
    digest = ChannelDigest(
        channel_name="Test",
        date=datetime.date.today(),
        summaries=["Section 1 content"],
        key_links=["[Link](http://example.com)"],
    )
    print("--- Case 1 (Normal) ---")
    print(digest.to_markdown())

    # Case 2: AI includes Key Links in summary
    digest2 = ChannelDigest(
        channel_name="Test",
        date=datetime.date.today(),
        summaries=["## Key Links\nAlready here"],
        key_links=["[Link](http://example.com)"],
    )
    print("\n--- Case 2 (Nested Key Links) ---")
    print(digest2.to_markdown())


if __name__ == "__main__":
    test_repro()
