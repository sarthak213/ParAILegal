"""Amendment history of a section, from India Code's own footnotes, and its text on a past date.

India Code marks every amended passage in an Act's text with a footnote number and brackets,
`2[198A. Prosecution of offences ...]`, and puts the history at the foot of the page:

    2. Ins. by Act 46 of 1983, s. 5 (w.e.f. 25-12-1983).
    1. Subs. by Act 5 of 2009, s. 18, for "fifteen years of age" (w.e.f. 31-12-2009)
    3. The words "or transportation" omitted by Act 26 of 1955, s. 117 (w.e.f. 1-1-1956).

Footnote numbers restart on each page (pages are separated by form feeds in the cached PDF
text, data/pdf_text). Each footnote becomes an Amendment: what was done, by which Act, from
when, the words it put in (the bracketed span) and, where the footnote gives them, the words
it replaced. From those, `text_as_on` rebuilds a section as it read on a past date, and says
plainly which changes it could not undo (a footnote that does not quote the old words:
"Subs. ... for section 4").
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date

INSERTED, SUBSTITUTED, OMITTED, REPEALED, OTHER = "inserted", "substituted", "omitted", "repealed", "other"

# dates of the Adaptation Orders India Code cites as "the A.O. 1950" etc.
ADAPTATION_ORDERS = {"1937": date(1937, 4, 1), "1948": date(1948, 4, 1), "1950": date(1950, 1, 26),
                     "1956": date(1956, 11, 1)}

_FOOTNOTE_START = re.compile(r"^\s*(\d{1,2})\.\s+(?=(?:Ins|Subs|Omitted|Om\.|Rep|The|Added|Certain|Words|Cl|Clause|"
                             r"Section|Sub-section|Explanation|Illustration|Proviso|Renumbered|Now|Original|Inserted|"
                             r"Substituted|Brought|Amended|Former)\b)")
_SEPARATOR = re.compile(r"^\s{20,}$|^\s*_{5,}\s*$")
_ACT = re.compile(r"\b(?:by|vide)\s+(?:the\s+)?(?P<by>(?:Act|Ord(?:inance)?\.?|Regulation)\s+\d+\s+of\s+\d{4}|"
                  r"A\.\s?O\.\s?\d{4}|[A-Z][A-Za-z ,()&'.-]+?(?:Act|Order),?\s+\d{4}(?:\s+\(\d+\s+of\s+\d{4}\))?)",
                  re.IGNORECASE)
_WEF = re.compile(r"w\.?\s?e\.?\s?f\.?\s*(\d{1,2})[-.](\d{1,2})[-.](\d{4})")
_OLD_WORDS = re.compile(r"\bfor\s+(?:the\s+(?:words?|figures?|letters?|brackets?)[^“\"]*?)?[“\"](?P<old>[^”\"]+)[”\"]")
_OMITTED_WORDS = re.compile(r"^(?:The|Certain)\s+(?:words?|figures?|letters?)[^“\"]*?[“\"](?P<old>[^”\"]+)[”\"][^.]*?\bomitted",
                            re.IGNORECASE)


@dataclass
class Amendment:
    act: str              # the amended Act ("IPC")
    section: str          # its section ("498A"); "" for a chapter heading or preamble
    n: int                # footnote number on its page
    page: int
    action: str           # inserted / substituted / omitted / repealed / other
    by: str               # the amending law: "Act 46 of 1983", "A.O. 1950"
    effective: str | None  # ISO date it took effect, if the footnote or a known Order gives it
    new_text: str         # the bracketed words now in the Act ("" for an omission)
    old_text: str | None  # the words it replaced, if the footnote quotes them
    note: str             # the footnote as printed

    def as_dict(self) -> dict:
        return asdict(self)


def _footnotes(page: str) -> tuple[str, dict[int, str]]:
    """(page body, {number: footnote text}) for one page."""
    lines = page.splitlines()
    # footnotes sit below a blank separator line; a page without one (an arrangement-of-sections
    # page: "33. Words referring to acts...") has none, however much it looks like footnotes
    seps = [i for i, x in enumerate(lines) if _SEPARATOR.match(x)]
    cut = len(lines)
    if seps and any(_FOOTNOTE_START.match(x) for x in lines[seps[-1] + 1:]):
        cut = seps[-1] + 1
    notes: dict[int, str] = {}
    current = None
    for line in lines[cut:]:
        m = _FOOTNOTE_START.match(line)
        if m:
            current = int(m.group(1))
            notes[current] = line[m.end():].strip()
        elif current is not None and line.strip():
            notes[current] += " " + line.strip()
    body = "\n".join(x for x in lines[:cut] if not _SEPARATOR.match(x))
    return body, notes


def parse_note(note: str, previous_by: str = "") -> tuple[str, str, str | None, str | None]:
    """(action, by, effective ISO date, old words) from a footnote."""
    low = note.lower()
    if low.startswith(("ins", "added", "inserted")):
        action = INSERTED
    elif low.startswith(("subs", "substituted")):
        action = SUBSTITUTED
    elif low.startswith("rep"):
        action = REPEALED
    elif "omitted" in low[:200] or low.startswith("om."):
        action = OMITTED
    else:
        action = OTHER
    m = _ACT.search(note)
    by = " ".join(m.group("by").split()) if m else ""
    if not by and re.search(r"\bibid\b", note):
        by = previous_by  # "Subs. by s. 51, ibid." refers to the Act named before
    by = re.sub(r"^A\.\s?O\.\s?", "A.O. ", by)
    effective = None
    w = _WEF.search(note)
    if w:
        d, mth, y = map(int, w.groups())
        try:
            effective = date(y, mth, d).isoformat()
        except ValueError:
            effective = None
    elif by.startswith("A.O."):
        year = by.split()[-1]
        effective = ADAPTATION_ORDERS[year].isoformat() if year in ADAPTATION_ORDERS else None
    old = None
    if action == SUBSTITUTED:
        o = _OLD_WORDS.search(note)
        old = o.group("old").strip() if o else None
    elif action == OMITTED:
        o = _OMITTED_WORDS.search(note)
        old = o.group("old").strip() if o else None
    return action, by, effective, old


_MARK = re.compile(r"(?<![\w\]])(\d{1,2})\[")
_HEADING = re.compile(r"^\s*(\d{1,3}[A-Z]{0,3})\.\s?(?=[A-Z“\"(\[])", re.M)
# section headings inside an amended span ("1[CHAPTER XXA ... 498A. Husband ...]")
_HEADING_IN_SPAN = re.compile(r"(?:^|\s)(\d{1,3}[A-Z]{0,3})\.\s?(?=[A-Z][a-z])")


def _span(text: str, start: int) -> str:
    """The bracketed words from text[start] (just after "n[") to the matching "]"."""
    depth = 1
    for i in range(start, min(len(text), start + 20000)):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                return text[start:i]
    return text[start:start + 2000]


def extract(act: str, raw: str) -> list[Amendment]:
    """Every amendment footnote of an Act's raw PDF text (pages separated by form feeds)."""
    out: list[Amendment] = []
    section = ""
    previous_by = ""
    pages = raw.split("\f")
    bodies = [_footnotes(p) for p in pages]
    joined = "\n".join(b for b, _ in bodies)  # spans may run across pages; a page starts on a new line
    offset = 0
    seen: set[tuple] = set()
    for page_no, (body, notes) in enumerate(bodies, 1):
        marks: dict[int, list[re.Match]] = {}
        for m in _MARK.finditer(body):  # one number may mark several identical changes on a page
            marks.setdefault(int(m.group(1)), []).append(m)
        for n, note in sorted(notes.items()):
            action, by, effective, old = parse_note(note, previous_by)
            previous_by = by or previous_by
            named = _sections_in_note(note)  # "for sections 375, 376, 376A ... and 376D"
            places: list[tuple[str, str]] = []  # (section, new text)
            for m in marks.get(n, []):
                new_text = re.sub(r"\s+", " ", re.sub(_MARK, "", _span(joined, offset + m.end()))).strip()
                inside = [h.group(1) for h in _HEADING_IN_SPAN.finditer(new_text)]
                if named:
                    places += [(s, new_text) for s in named]
                elif inside and (new_text.startswith(inside[0] + ".") or new_text.upper().startswith("CHAPTER")):
                    # the marker opens whole sections ("2[198A. ...]", "1[CHAPTER XXA ... 498A. ...]"):
                    # the amendment is each section's own history
                    places += [(s, new_text) for s in dict.fromkeys(inside)]
                else:
                    places.append((_section_before(joined, offset + m.start()), new_text))
            if not places:
                where = named or [_section_before(joined, offset + len(body))]
                places = [(s, "") for s in where]
            for sec, new_text in places:
                # a footnote naming its sections is one change however many pages it runs across
                key = (sec, by, effective, note[:80], "" if named else new_text[:80])
                if key in seen:
                    continue  # India Code repeats a footnote on each page a long change runs across
                seen.add(key)
                out.append(Amendment(act, sec, n, page_no, action, by, effective, new_text, old, note))
        heads = list(_HEADING.finditer(body))
        if heads:
            section = heads[-1].group(1)
        offset += len(body) + 1  # the newline joining pages
    return out


_NAMED = re.compile(r"\bfor\s+(?:sections?|ss\.)\s+((?:\d{1,3}[A-Z]{0,3}(?:\s*,\s*|\s+and\s+|\s+to\s+)?)+)")


def _sections_in_note(note: str) -> list[str]:
    """Sections a footnote names as substituted wholesale ("for sections 375, 376 and 376A")."""
    m = _NAMED.search(note)
    return re.findall(r"\d{1,3}[A-Z]{0,3}", m.group(1)) if m else []


def _section_before(text: str, pos: int) -> str:
    heads = list(_HEADING.finditer(text, 0, pos))
    return heads[-1].group(1) if heads else ""


# ── A section on a past date ───────────────────────────────────────────

@dataclass
class PointInTime:
    text: str | None                  # None when the section did not exist yet
    complete: bool                    # False when some later change could not be undone
    undone: list[dict] = field(default_factory=list)       # changes rolled back
    unknown: list[dict] = field(default_factory=list)      # changes whose earlier wording is not known
    undated: list[dict] = field(default_factory=list)      # changes with no known date: not applied


def text_as_on(current: str, amendments: list[Amendment], on: str) -> PointInTime:
    """The section's text on date `on` (ISO), rolling back each amendment that took effect after it.

    current: the section's text today (corpus, markers already removed). An inserted passage is
    removed; a substitution whose old words the footnote quotes is reversed; anything else is
    listed as unknown, so the caller can say exactly what it could not reconstruct."""
    # compared without brackets, footnote markers or spacing: the corpus keeps India Code's
    # brackets around amended words, and its text is lightly repaired (split words rejoined)
    text, result = _plain(current), PointInTime(current, True)
    later = sorted((a for a in amendments if a.effective and a.effective > on), key=lambda a: a.effective, reverse=True)
    for a in later:
        info = {"action": a.action, "by": a.by, "effective": a.effective, "note": a.note}
        span = _plain(a.new_text)
        if a.action == INSERTED and _opens_section(a):
            return PointInTime(None, True, [*result.undone, info], result.unknown, result.undated)
        # the bracketed span starts with the section's own number when it is the whole section
        body = re.sub(rf"^{re.escape(a.section)}\.\s?[^—]*?—\s*", "", span) if a.section else span
        if a.action == INSERTED and body and body in text:
            text = text.replace(body, "", 1)
            result.undone.append(info)
        elif a.action == SUBSTITUTED and a.old_text is not None and body and body in text:
            text = text.replace(body, _plain(a.old_text), 1)
            result.undone.append(info)
        elif a.action == OMITTED and a.old_text:
            result.unknown.append({**info, "omitted_words": a.old_text})  # where they stood is not recorded
        else:
            result.unknown.append(info)
    result.undated = [{"action": a.action, "by": a.by, "note": a.note} for a in amendments if not a.effective]
    result.text = " ".join(text.split())
    result.complete = not result.unknown and not result.undated
    return result


def _plain(text: str) -> str:
    """Text without India Code's brackets and footnote markers, spacing collapsed."""
    return " ".join(re.sub(r"\d{1,2}\[|[\[\]]", "", text or "").split())


def _opens_section(a: Amendment) -> bool:
    """The amendment brought the whole section in: its span starts with the section ("2[198A. ...")
    or with a chapter that contains it ("1[CHAPTER XXA ... 498A. ...")."""
    if not a.section:
        return False
    span = _plain(a.new_text)
    heading = re.compile(rf"(?:^|\s){re.escape(a.section)}\.\s?(?=[A-Z])")
    return bool(heading.match(span) or (span.upper().startswith("CHAPTER") and heading.search(span)))
