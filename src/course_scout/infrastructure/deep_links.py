"""Rewrite public social URLs to app-scheme URIs for instant-open on mobile.

Telegram's in-app browser opens `https://` links, and iOS Universal Links
rarely fire from inside it — so `https://instagram.com/foo` round-trips
through Chrome (or Telegram's in-app browser) before reaching the Instagram
app, which is slow and jarring. Rewriting to `instagram://user?username=foo`
makes the OS hand the link straight to the installed app.

Gracefully degrades: on desktop (or if the app isn't installed) the OS will
still fall back to the https version, but we keep both in the rendered
markdown so fallback is explicit when possible.

Called once on the assembled digest markdown before write/render —
not on individual message text, since the LLM-produced key_links section
is where most external URLs live.
"""

from __future__ import annotations

import re

# Match [display](url) inline markdown links.
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")

# Domain-specific rewrites. Each entry: (compiled_host_path_pattern, url_builder)
# url_builder(groups) -> deep-link URI (str) or None to skip.


def _instagram(m: re.Match[str]) -> str | None:
    user = m.group("user")
    if not user or user in ("p", "reel", "reels", "tv", "stories", "explore"):
        # Non-profile URLs (posts, reels, stories) — Instagram deep-linking
        # for these is finicky; skip.
        return None
    return f"instagram://user?username={user}"


def _twitter(m: re.Match[str]) -> str | None:
    user = m.group("user")
    if not user or user in ("i", "intent", "search", "home"):
        return None
    return f"twitter://user?screen_name={user}"


def _youtube_watch(m: re.Match[str]) -> str | None:
    vid = m.group("vid")
    return f"vnd.youtube://{vid}" if vid else None


def _youtube_short(m: re.Match[str]) -> str | None:
    vid = m.group("vid")
    return f"vnd.youtube://{vid}" if vid else None


_RULES: list[tuple[re.Pattern[str], callable]] = [  # type: ignore[type-arg]
    (
        re.compile(r"^https?://(?:www\.)?instagram\.com/(?P<user>[A-Za-z0-9_.]+)/?(?:\?.*)?$"),
        _instagram,
    ),
    (
        re.compile(r"^https?://(?:www\.)?(?:twitter|x)\.com/(?P<user>[A-Za-z0-9_]+)/?(?:\?.*)?$"),
        _twitter,
    ),
    (
        re.compile(r"^https?://(?:www\.)?youtube\.com/watch\?.*?v=(?P<vid>[A-Za-z0-9_-]{6,})"),
        _youtube_watch,
    ),
    (
        re.compile(r"^https?://(?:www\.)?youtu\.be/(?P<vid>[A-Za-z0-9_-]{6,})/?(?:\?.*)?$"),
        _youtube_short,
    ),
]


def _rewrite_url(url: str) -> str | None:
    """Try each rule; return deep-link URI if any match, else None."""
    for pattern, builder in _RULES:
        m = pattern.match(url)
        if m:
            return builder(m)
    return None


def deep_linkify(markdown: str) -> str:
    """Rewrite known social https:// URLs in markdown to app-scheme URIs.

    Handles both inline markdown links `[text](https://...)` and bare
    URLs on their own. Leaves all other URLs untouched.
    """

    def _sub_md(m: re.Match[str]) -> str:
        display, url = m.group(1), m.group(2)
        deep = _rewrite_url(url)
        if not deep:
            return m.group(0)
        # Preserve https fallback in plain-text parens after the deep link,
        # so desktop readers (who can't resolve `instagram://`) still have
        # something clickable.
        return f"[{display}]({deep}) ({url})"

    out = _MD_LINK.sub(_sub_md, markdown)

    # Also rewrite bare URLs on their own — but only ones not already inside
    # a markdown link (to avoid double-rewriting). Simple heuristic: only
    # match URLs preceded by whitespace/start-of-line and not by `(`.
    def _sub_bare(m: re.Match[str]) -> str:
        url = m.group(1)
        deep = _rewrite_url(url)
        return f"[{url}]({deep})" if deep else m.group(0)

    out = re.sub(r"(?<![\(\[])(https?://\S+)", _sub_bare, out)
    return out
