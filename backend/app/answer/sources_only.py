"""The answer when no answer model is available: the matching provisions, in order.

Written entirely by code, so it is instant, needs no model and cannot misstate the law.
It is also the fallback when the answer model is not installed or fails to start.
"""

from __future__ import annotations

DISCLAIMER = (
    "⚖ This is a research tool. Verify all provisions against the official "
    "Gazette. This is not legal advice."
)

_EXCERPT_WORDS = 45


def status_note(source: dict) -> str:
    """'Repealed; replaced by …', 'Omitted', 'Applies only in …', or '' for law in force."""
    status = (source.get("status") or "").lower()
    replaced_by = source.get("replaced_by")
    if status == "repealed":
        return f"Repealed; replaced by {replaced_by}" if replaced_by else "Repealed"
    if status == "omitted":
        return "Omitted from the Constitution"
    if source.get("scope") == "regional" and source.get("places"):
        return "Applies only in " + ", ".join(source["places"])
    return ""


def heading(source: dict) -> str:
    """'Section 103 — Punishment for murder, Bharatiya Nyaya Sanhita, 2023'."""
    first = (source.get("citation") or source.get("hierarchy") or "").split("\n")[0].strip().rstrip(".")
    title = source.get("act_title") or source.get("document_title") or ""
    if title and title not in first:
        return f"{first}, {title}" if first else title
    return first or title or "Source"


def excerpt(text: str, words: int = _EXCERPT_WORDS) -> str:
    parts = (text or "").split()
    return " ".join(parts[:words]) + (" …" if len(parts) > words else "")


DEFAULT_LEAD = (
    "**No answer model is set up, so here are the provisions that best match your "
    "question.** Open a source card to read the full text."
)


def sources_only_answer(sources: list[dict], lead: str | None = None) -> str:
    """The provisions as a numbered list under a lead line (app/answer/pipeline.py picks it)."""
    if not sources:
        return (lead or "No relevant provisions were found for this question. "
                "Try rephrasing it, or name the Act or section.") + "\n\n" + DISCLAIMER
    lines = [lead or DEFAULT_LEAD, ""]
    for n, s in enumerate(sources, start=1):
        note = status_note(s)
        lines.append(f"{n}. **{heading(s)}**" + (f" — *{note}*" if note else ""))
        lines.append(f"   > {excerpt(s.get('text', ''))}")
    return "\n".join(lines) + "\n\n" + DISCLAIMER
