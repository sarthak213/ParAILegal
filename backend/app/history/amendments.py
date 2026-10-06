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
RENUMBERED, COMMENCED = "renumbered", "commenced"  # "Section 5 renumbered ..."; "1st July, 1989, vide Notifn."

# dates of the Adaptation Orders India Code cites as "the A.O. 1950" etc.
ADAPTATION_ORDERS = {"1937": date(1937, 4, 1), "1948": date(1948, 4, 1), "1950": date(1950, 1, 26),
                     "1956": date(1956, 11, 1)}

_ACT = re.compile(r"\b(?:by|vide)\s+(?:the\s+)?(?P<by>(?:Act|Ord(?:inance)?\.?|Regulation)\s+\d+\s+of\s+\d{4}|"
                  r"A\.\s?O\.\s?\d{4}|[A-Z][A-Za-z ,()&'.-]+?(?:Act|Order),?\s+\d{4}(?:\s+\(\d+\s+of\s+\d{4}\))?)",
                  re.IGNORECASE)
_WEF = re.compile(r"w\.?\s?e\.?\s?f\.?\s*(\d{1,2})\s?[-.]\s?(\d{1,2})\s?[-.]\s?(\d{4})")
_DATED = re.compile(r"\bdated,?\s*(\d{1,2})\s?[-./]\s?(\d{1,2})\s?[-./]\s?(\d{4})")
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]
_DATE_WORDS = re.compile(rf"(\d{{1,2}})\s*(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?({'|'.join(_MONTHS)}),?\s+(\d{{4}})", re.IGNORECASE)
# a commencement note: "1st July, 1989, vide Notifn. No. ...", "25-12-1983, vide S.O. ..."
_COMMENCEMENT = re.compile(rf"^(?:\d{{1,2}}\s*(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?(?:{'|'.join(_MONTHS)})|\d{{1,2}}-\d{{1,2}}-\d{{4}})\b.*\bvide\b",
                           re.IGNORECASE)
_ACTION_WORDS = [  # searched near the start of a note, in this order
    (INSERTED, re.compile(r"\b(?:ins|inserted|added)\b", re.IGNORECASE)),
    (SUBSTITUTED, re.compile(r"\b(?:subs|substituted)\b", re.IGNORECASE)),
    (OMITTED, re.compile(r"\b(?:omitted|om)\b", re.IGNORECASE)),
    (REPEALED, re.compile(r"\b(?:rep|repealed)\b", re.IGNORECASE)),
    (RENUMBERED, re.compile(r"\bre-?numbered\b", re.IGNORECASE)),
]
_LAW_YEAR = re.compile(r"\b(?:of|,)\s*(\d{4})\b")
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
    basis: str = ""       # how `effective` is known: "w.e.f.", "order" (an Adaptation Order),
                          # "notification date", "commencement"; "" when it is not
    year: int | None = None  # the amending law's year ("Act 46 of 1983" -> 1983), when known
    old_source: str = ""     # where old_text came from when the footnote lacks it (app/history/fill.py)

    def as_dict(self) -> dict:
        return asdict(self)


_NUMBERED = re.compile(r"^\s*(\d{1,2})\.\s+\S")
_AMENDING_WORDS = re.compile(r"\b(?:Ins|Subs|Omitted|Om|Rep|Added|Inserted|Substituted|ibid)\b\.?|\bby\s+(?:the\s+)?(?:Act|A\.\s?O\.)|"
                             r"w\.\s?e\.\s?f\.|\bNotifn\b|\bvide\b", re.IGNORECASE)
_PAGE_TAIL = re.compile(r"^\s*(?:IndiaCode|\d{1,4})?\s*$")


def _footnotes(page: str) -> tuple[str, dict[int, str]]:
    """(page body, {number: footnote text}) for one page.

    The footnotes are the text after the page's last blank line (India Code PDFs separate them
    with an empty line, sometimes a long run of spaces), and only if they look like footnotes:
    numbered 1, 2, 3... in order, and speaking of amendments ("Ins. by Act", "w.e.f.", "vide
    Notifn."). An arrangement-of-sections page ("32. Words referring to acts...") does not."""
    lines = page.splitlines()
    end = len(lines)
    while end and _PAGE_TAIL.match(lines[end - 1]):  # trailing blanks, the watermark, a page number
        end -= 1
    blanks = [i for i in range(end) if not lines[i].strip()]
    cut = len(lines)
    if blanks:
        block = lines[blanks[-1] + 1:end]
        numbers = [int(m.group(1)) for x in block if (m := _NUMBERED.match(x))]
        if (block and _NUMBERED.match(block[0]) and numbers == list(range(numbers[0], numbers[0] + len(numbers)))
                and _AMENDING_WORDS.search(" ".join(block))):
            cut = blanks[-1] + 1
    notes: dict[int, str] = {}
    current = None
    for line in lines[cut:end]:
        m = _NUMBERED.match(line)
        if m:
            current = int(m.group(1))
            notes[current] = line[line.index(".", m.start(1)) + 1:].strip()
        elif current is not None and line.strip():
            notes[current] += " " + line.strip()
    body = "\n".join(lines[:cut]).rstrip()
    return body, notes


def parse_note(note: str, previous_by: str = "") -> tuple[str, str, str | None, str | None]:
    """(action, by, effective ISO date, old words) from a footnote; `dating` says how sure the date is."""
    action = OTHER
    if _COMMENCEMENT.match(note):
        action = COMMENCED
    else:
        head = note[:120]  # "The proviso ins. by ...", "The words ... omitted by ..."
        hits = [(m.start(), a) for a, pat in _ACTION_WORDS if (m := pat.search(head))]
        if hits:
            action = min(hits)[1]
    m = _ACT.search(note)
    by = " ".join(m.group("by").split()) if m else ""
    if not by and re.search(r"\bibid\b", note):
        by = previous_by  # "Subs. by s. 51, ibid." refers to the Act named before
    by = re.sub(r"^A\.\s?O\.\s?", "A.O. ", by)
    effective = dating(note, by, action)[0]
    old = None
    if action == SUBSTITUTED:
        o = _OLD_WORDS.search(note)
        old = o.group("old").strip() if o else None
    elif action == OMITTED:
        o = _OMITTED_WORDS.search(note)
        old = o.group("old").strip() if o else None
    return action, by, effective, old


def _iso(d: int, m: int, y: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def dating(note: str, by: str, action: str = "") -> tuple[str | None, str, int | None]:
    """(effective ISO date, how it is known, year of the amending law).

    "w.e.f." gives the date itself; an Adaptation Order has a known date; a commencement note
    names its date in words or figures; a notification ("G.S.R. 726(E), dated 8-10-2008") gives
    the date it was issued, an approximation kept apart as "notification date". Otherwise only
    the amending Act's year is known ("Ins. by Act 46 of 1983, s. 2.")."""
    year = None
    if y := _LAW_YEAR.search(by or ""):
        year = int(y.group(1))
    if w := _WEF.search(note):
        return _iso(int(w.group(1)), int(w.group(2)), int(w.group(3))), "w.e.f.", year
    if by.startswith("A.O."):
        order = by.split()[-1]
        if order in ADAPTATION_ORDERS:
            return ADAPTATION_ORDERS[order].isoformat(), "order", year or int(order)
    if action == COMMENCED:
        if w := _DATE_WORDS.match(note):
            return _iso(int(w.group(1)), _MONTHS.index(w.group(2).lower()) + 1, int(w.group(3))), "commencement", year
        if w := re.match(r"(\d{1,2})-(\d{1,2})-(\d{4})", note):
            return _iso(int(w.group(1)), int(w.group(2)), int(w.group(3))), "commencement", year
    if w := _DATED.search(note):
        return _iso(int(w.group(1)), int(w.group(2)), int(w.group(3))), "notification date", year
    return None, "", year


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
            _, basis, year = dating(note, by, action)
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
                out.append(Amendment(act, sec, n, page_no, action, by, effective, new_text, old, note, basis, year))
        heads = list(_HEADING.finditer(body))
        if heads:
            section = heads[-1].group(1)
        offset += len(body) + 1  # the newline joining pages
    return out


_WORDS = re.compile(r"[a-z0-9]+")


def reattribute(amendments: list[Amendment], sections: dict[str, str]) -> int:
    """Move each amendment whose words are not in the section it was filed under, but are in
    exactly one other section, to that section; the count moved.

    `extract` files a footnote under the last section heading it saw, and India Code's layout
    defeats that often (a long definitions section, a heading the PDF breaks, an inserted
    "24A" read as part of "24"). The section texts settle it. sections: section -> its text."""
    norm = {s: " ".join(_WORDS.findall(_plain(t).lower())) for s, t in sections.items()}
    moved = 0
    for a in amendments:
        span = " ".join(_WORDS.findall(_plain(a.new_text).lower())[:12])  # its opening words
        if len(span) < 15 or span in norm.get(a.section, ""):
            continue
        where = [s for s, t in norm.items() if span in t]
        if len(where) == 1:
            a.section = where[0]
            moved += 1
    return moved


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
    undated: list[dict] = field(default_factory=list)      # changes whose timing against `on` is unclear
    in_force_from: str | None = None  # the provision's commencement, if it came after `on`


def _after(a: Amendment, on: str) -> bool | None:
    """Did the change take effect after `on`? None when that cannot be told: no date and the
    amending law's year is unknown or is `on`'s own year."""
    if a.effective:
        return a.effective > on
    if a.year:
        on_year = int(on[:4])
        return None if a.year == on_year else a.year > on_year
    return None


def text_as_on(current: str, amendments: list[Amendment], on: str) -> PointInTime:
    """The section's text on date `on` (ISO), rolling back each amendment that took effect after it.

    current: the section's text today (corpus, markers already removed). An inserted passage is
    removed; a substitution whose old words the footnote quotes is reversed; anything else is
    listed as unknown, so the caller can say exactly what it could not reconstruct. A change with
    no date is placed by its amending law's year ("Act 46 of 1983") when that is not `on`'s year."""
    # compared without brackets, footnote markers or spacing: the corpus keeps India Code's
    # brackets around amended words, and its text is lightly repaired (split words rejoined)
    text, result = _plain(current), PointInTime(current, True)
    changes = [a for a in amendments if a.action not in (COMMENCED, RENUMBERED)]
    for a in amendments:
        if a.action == COMMENCED and a.effective and a.effective > on:
            result.in_force_from = a.effective
    order = lambda a: a.effective or f"{a.year or 0:04d}-12-31"  # noqa: E731
    later = sorted((a for a in changes if _after(a, on)), key=order, reverse=True)
    for a in later:
        info = {"action": a.action, "by": a.by, "effective": a.effective or (str(a.year) if a.year else None),
                "basis": a.basis or ("year of the amending law" if a.year else ""), "note": a.note,
                **({"old_source": a.old_source} if a.old_source else {})}
        span = _plain(a.new_text)
        if a.action == INSERTED and _opens_section(a):
            return PointInTime(None, True, [*result.undone, info], result.unknown, result.undated, result.in_force_from)
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
    result.undated = [{"action": a.action, "by": a.by, "note": a.note} for a in changes if _after(a, on) is None]
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
