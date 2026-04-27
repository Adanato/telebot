import pytest

from course_scout.domain.models import (
    AnnouncementItem,
    ChannelDigest,
    CourseItem,
    DiscussionItem,
    FileItem,
    RequestItem,
)
from course_scout.infrastructure.dedup import (
    DigestDeduper,
    SeenItemRepository,
    normalize_filename,
    normalize_url,
)

# ── normalize_url ──


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://www.coloso.us/courses/abc/", "https://coloso.us/courses/abc"),
        ("HTTPS://Coloso.US/Courses/abc", "https://coloso.us/Courses/abc"),
        ("https://x.com/?utm_source=tg&id=42", "https://x.com/?id=42"),
        ("https://x.com/page#section", "https://x.com/page"),
        ("http://x.com/p?b=2&a=1", "http://x.com/p?a=1&b=2"),
        ("", None),
        ("   ", None),
        ("not a url", None),
        (None, None),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


# ── normalize_filename ──


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Coloso_ChonNam_Lighting_Vol2.zip", "coloso_chonnam_lighting_vol2"),
        ("Krenz Color Term 1.RAR", "krenz_color_term_1"),
        ("Drawing Faces.part1.rar", "drawing_faces_part1"),
        ("file.7z", "file"),
        ("multi--dash__under.zip", "multi_dash_under"),
        ("", None),
        ("   ", None),
        (None, None),
        (".zip", None),
    ],
)
def test_normalize_filename(raw, expected):
    assert normalize_filename(raw) == expected


# ── SeenItemRepository ──


@pytest.fixture
def repo(tmp_path):
    return SeenItemRepository(db_path=str(tmp_path / "seen.db"))


def test_seen_link_round_trip(repo):
    url = "https://coloso.us/courses/abc"
    assert not repo.is_link_seen(url)
    repo.mark_link_seen(url, "Asian Artists", "ChonNam Lighting")
    assert repo.is_link_seen(url)


def test_seen_file_round_trip(repo):
    key = "coloso_chonnam_lighting_vol2"
    assert not repo.is_file_seen(key)
    repo.mark_file_seen(key, "2D Lounge", "ChonNam Vol2")
    assert repo.is_file_seen(key)


def test_mark_seen_is_idempotent(repo):
    url = "https://coloso.us/courses/abc"
    repo.mark_link_seen(url, "ch1", "title1")
    repo.mark_link_seen(url, "ch2", "title2")
    assert repo.stats() == {"links": 1, "files": 0}


def test_stats(repo):
    repo.mark_link_seen("https://x.com/a", "ch", "t")
    repo.mark_link_seen("https://x.com/b", "ch", "t")
    repo.mark_file_seen("file_one", "ch", "t")
    assert repo.stats() == {"links": 2, "files": 1}


# ── DigestDeduper ──


def _digest(items):
    import datetime as _dt

    return ChannelDigest(
        channel_name="test",
        date=_dt.date.today(),
        summaries=[],
        items=items,
    )


def test_first_run_keeps_everything(repo):
    items = [
        CourseItem(title="Course A", description="d", links=["https://coloso.us/a"]),
        FileItem(title="FileA.zip", description="d", links=["https://pan.baidu.com/x"]),
    ]
    deduper = DigestDeduper(channel_name="test", repo=repo)
    digest = _digest(items)
    dropped = deduper.filter(digest)
    assert dropped == 0
    assert len(digest.items) == 2


def test_second_run_drops_repeats(repo):
    deduper = DigestDeduper(channel_name="test", repo=repo)
    first = _digest(
        [
            CourseItem(title="Course A", description="d", links=["https://coloso.us/a"]),
            FileItem(title="FileA.zip", description="d", links=["https://pan.baidu.com/x"]),
        ]
    )
    deduper.filter(first)

    second = _digest(
        [
            CourseItem(title="Re-share of A", description="d", links=["https://www.coloso.us/a/"]),
            FileItem(title="FileA.ZIP", description="d", links=[]),
            CourseItem(title="New Course B", description="d", links=["https://coloso.us/b"]),
        ]
    )
    dropped = deduper.filter(second)
    assert dropped == 2
    assert len(second.items) == 1
    assert second.items[0].title == "New Course B"


def test_conversational_categories_are_never_dropped(repo):
    deduper = DigestDeduper(channel_name="test", repo=repo)
    first = _digest([CourseItem(title="C", description="d", links=["https://coloso.us/c"])])
    deduper.filter(first)

    second = _digest(
        [
            DiscussionItem(title="d1", description="d", links=["https://coloso.us/c"]),
            RequestItem(title="r1", description="d", links=["https://coloso.us/c"]),
            AnnouncementItem(title="a1", description="d", links=["https://coloso.us/c"]),
        ]
    )
    dropped = deduper.filter(second)
    assert dropped == 0
    assert len(second.items) == 3


def test_t_me_links_are_not_signals(repo):
    """An item with only t.me links has no signals → kept (can't dedup blind)."""
    deduper = DigestDeduper(channel_name="test", repo=repo)
    item = CourseItem(
        title="Some Course",
        description="d",
        links=["https://t.me/c/123/45/678"],
    )
    digest = _digest([item])
    deduper.filter(digest)
    again = _digest(
        [
            CourseItem(
                title="Same Course (re-shared)",
                description="d",
                links=["https://t.me/c/123/45/999"],
            )
        ]
    )
    dropped = deduper.filter(again)
    assert dropped == 0


def test_partial_novelty_keeps_item(repo):
    """If a course re-link adds a new external URL, keep it."""
    deduper = DigestDeduper(channel_name="test", repo=repo)
    first = _digest([CourseItem(title="C", description="d", links=["https://coloso.us/old"])])
    deduper.filter(first)

    second = _digest(
        [
            CourseItem(
                title="C (with mirror)",
                description="d",
                links=["https://coloso.us/old", "https://pan.baidu.com/mirror"],
            )
        ]
    )
    dropped = deduper.filter(second)
    assert dropped == 0
    # And the new mirror URL is now also recorded
    assert repo.is_link_seen("https://pan.baidu.com/mirror")


def test_file_dedup_by_filename_alone(repo):
    """FileItem with no links still dedups by normalized title."""
    deduper = DigestDeduper(channel_name="test", repo=repo)
    first = _digest([FileItem(title="Krenz_Course_Vol1.rar", description="d", links=[])])
    deduper.filter(first)
    second = _digest([FileItem(title="krenz course vol1.RAR", description="d", links=[])])
    dropped = deduper.filter(second)
    assert dropped == 1
