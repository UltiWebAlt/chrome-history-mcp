"""Readable, grouped output for history search tool responses."""
import html
import re
from datetime import datetime
from urllib.parse import quote, urlsplit


def _text(value):
    # Keep history/page content from creating additional Markdown structure.
    text = html.escape(" ".join(str(value).split()), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~\-])", r"\\\1", text)


def _visited(value):
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is not None:
            date = date.astimezone()
            return date.strftime("%b %d, %Y at %H:%M %Z")
    except (ValueError, TypeError, AttributeError):
        pass
    return value


def render_history_results(result):
    blocks = [_text(result["summary"])]
    for number, page in enumerate(result["pages"], 1):
        url = page["url"]
        title = _text(page.get("title") or url)
        if urlsplit(url).scheme.lower() in {"http", "https"}:
            target = quote(url, safe="/:?#[]@!$&'*+,;=%")
            heading = f"### {number}. [{title}](<{target}>)"
        else:
            heading = f"### {number}. {title} — {_text(url)}"
        group = [heading, f"Visited: {_text(_visited(page['last_visited_at']))}"]
        if page.get("excerpt"):
            group.append("> " + _text(page["excerpt"]))
        if page.get("match_note"):
            group.append(_text(page["match_note"]))
        blocks.append("\n\n".join(group))
    if result.get("notice"):
        blocks.append(_text(result["notice"]))
    return "\n\n".join(blocks)
