"""Executive summary generation: rank top 5 items across all topics.

Stage 3 of the pipeline. The LLM sees EVERY item from every topic (with
category + priority + status hints) and preference-ranks the top-5 by
Adam's interests. Distinct from the per-topic summarizer because it
operates on the cross-topic union.
"""

from __future__ import annotations

from typing import Any


async def generate_executive_summary(
    all_results: list[tuple[str, Any]],
    date_str: str,
) -> str:
    """Generate a personalized executive summary across all topic digests."""
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        TextBlock,
        query,
    )

    flat = [(item, name) for name, result in all_results for item in result.items]

    def _fmt(item: Any, topic: str) -> str:
        cat = item.category.upper()
        status = getattr(item, "status", None)
        priority = item.priority or ""
        links = f" | Links: {', '.join(item.links[:2])}" if item.links else ""
        meta = " · ".join(filter(None, [priority, status]))
        meta_str = f" ({meta})" if meta else ""
        return f"- [{cat}]{meta_str} {item.title} | topic={topic}: {item.description[:200]}{links}"

    items_block = "\n".join(_fmt(i, t) for i, t in flat) if flat else "(none)"

    prompt = f"""Today's ({date_str}) scan results from art community Telegram channels.
Pick the 5 items most relevant to Adam and write a summary.

Each item has: [CATEGORY] (PRIORITY · STATUS) title | topic=source: description

CATEGORY semantics:
- [FILE] = downloadable file/archive/link shared in-chat
- [COURSE] = course recommendation, review, or shared course
- [DISCUSSION] = technique discussion, debate, tool comparison
- [REQUEST] = someone asking for a resource
- [ANNOUNCEMENT] = community news

PRIORITY is a routing hint (HIGH/MEDIUM/LOW) — derived deterministically from
category + fulfillment. Use it as background, not as a hard ranker. A MEDIUM
item on Adam's focus area beats a HIGH item that's off-topic.

=== ALL ITEMS ===
{items_block}

Adam's interests (use these for preference ranking):
- 2D illustration, character design, concept art
- Anatomy, figure drawing, gesture
- Color theory, lighting, rendering
- Asian artists, anime/manga styles, webtoon
- Courses: Coloso, Schoolism, CGMA, Domestika, Proko
De-prioritize: 3D, game dev, photography, UI/UX, motion graphics (unless directly applicable).

FORMAT (use this exact structure):

## Top 5 Finds

1. [TAG] **Exact item name** — one line on why it matters to Adam. *Topic: {{source}}*
2. [TAG] **Exact item name** — one line on why it matters to Adam. *Topic: {{source}}*
3. [TAG] **Exact item name** — one line on why it matters to Adam. *Topic: {{source}}*
4. [TAG] **Exact item name** — one line on why it matters to Adam. *Topic: {{source}}*
5. [TAG] **Exact item name** — one line on why it matters to Adam. *Topic: {{source}}*

Pick based on Adam-relevance, not category alone. [REQUEST] items can make top-5
if the content genuinely interests him (e.g., a requested Krenz course).

## Summary

1-2 paragraphs covering the remaining items. Flag time-sensitive items
(expiring links, group buys closing, new course drops, approaching deadlines)."""

    options = ClaudeAgentOptions(
        model="claude-haiku-4-5",
        system_prompt="You write concise executive summaries for daily art community digests.",
        max_turns=1,
        permission_mode="bypassPermissions",
        effort="low",
        thinking={"type": "disabled"},
    )

    last_text = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    last_text = block.text

    if last_text:
        return f"## Executive Summary\n\n{last_text}"
    return "## Executive Summary\n\n*Summary generation failed.*"
