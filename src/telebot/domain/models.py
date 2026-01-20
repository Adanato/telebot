import datetime

from pydantic import BaseModel, Field


class DigestItem(BaseModel):
    """A single item in the digest (course, file, discussion, etc.)."""
    title: str = Field(..., description="Title or name of the item")
    description: str = Field(..., description="Brief description")
    category: str = Field(..., description="Category: 'course', 'file', 'discussion', 'request', 'announcement'")
    links: list[str] = Field(default_factory=list, description="Related URLs")
    author: str | None = Field(None, description="Author or source if known")


class LinkItem(BaseModel):
    title: str
    url: str


class TelegramMessage(BaseModel):
    id: int
    text: str | None  # Text can be empty if it's just media
    date: datetime.datetime
    author: str | None = None
    link: str
    reply_to_id: int | None = None
    forward_from_chat: str | None = None
    forward_from_author: str | None = None
    local_media_path: str | None = None


class ChannelDigest(BaseModel):
    channel_name: str
    date: datetime.date
    summaries: list[str]  # Used for the executive summary
    items: list[DigestItem] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    key_links: list[LinkItem] = Field(default_factory=list)

    def to_markdown(self) -> str:
        """Standardized markdown representation of the digest."""
        md = f"# Daily Digest: {self.channel_name}\n"
        md += f"**Date**: {self.date}\n\n"
        
        # 1. Executive Summary
        if self.summaries:
            if not self.summaries[0].startswith("##"):
                md += "## 📝 Executive Summary\n\n"
            for s in self.summaries:
                md += f"{s}\n\n"
            
        # 2. Categorized Items
        categories = {
            "course": "## 🎓 Courses & Tutorials",
            "file": "## 📂 Files Shared",
            "discussion": "## 🗣 Discussions",
            "request": "## 🙋 Requests",
            "announcement": "## 📢 Announcements"
        }
        
        for cat_key, cat_title in categories.items():
            cat_items = [i for i in self.items if i.category == cat_key]
            if cat_items:
                md += f"{cat_title}\n\n"
                for item in cat_items:
                    links_str = ""
                    if item.links:
                        links_str = " (" + ", ".join(item.links) + ")"
                    md += f"- **{item.title}**{links_str}: {item.description}\n"
                md += "\n"
                
        # 3. Action Items
        if self.action_items:
            md += "## ✅ Action Items\n\n"
            for item in self.action_items:
                md += f"- [ ] {item}\n"
            md += "\n"
            
        # 4. Key Links
        if self.key_links:
            md += "## 🔗 Key Links\n\n"
            for link in self.key_links:
                md += f"- [{link.title}]({link.url})\n"
            md += "\n"
        
        return md.strip()
