"""Earlier wordings the footnotes do not give, read from archived copies of the Act.

"Subs. by Act 4 of 2005, s. 2, for section 4" says what replaced section 4, not what it said. An India
Code copy of the Act saved before 2005 (scripts/fetch_archived_acts.py) still has the old section.
For each such substitution:

  1. choose the copy: the latest one made before the change, i.e. one whose footnotes do not yet cite
     the amending Act, and whose newest amending Act is older than it
  2. find the place: the words just before and just after the bracketed span in today's text are
     looked for in that copy's section; what lies between them there is the earlier wording
     (a span that is the whole section: the copy's whole section)
  3. check it: the earlier wording must differ from today's span; otherwise the copy already had the
     change and the result is discarded

The result records where it came from ("archived India Code copy saved 2009-04-10"), since it is
a consolidated text, not the Gazette.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.history.amendments import SUBSTITUTED, Amendment, _plain

_ACT_CITED = re.compile(r"\bAct\s+(?:No\.\s*)?(\d{1,3})\s+of\s+(\d{4})", re.IGNORECASE)
_NAMED_ACT_YEAR = re.compile(r"\b[A-Z][A-Za-z()&,' -]{3,120}?Act,?\s+(\d{4})")
# a footnote line in an archived copy: "1.   Subs. by Act 3 of 1951 ...", "1  Substituted  by the ..."
_NOTE_LINE = re.compile(r"^\s*\d{1,3}\.?\s+(?:Subs|Ins|Omitted|Om|Rep|Added|Substituted|Inserted|Repealed|The\s+words|"
                        r"Certain|Clause|Sub-?section|Section|Proviso|Explanation|Ibid|See|Now|For|Vide|Renumbered|"
                        r"Came\s+into|w\.\s?e\.\s?f)\b", re.IGNORECASE)
_RULE = re.compile(r"^\s*-{8,}\s*$")
_PAGE_NO = re.compile(r"^\s*(?:\d+\.\d+|\d{1,4}|Page \d+.*)\s*$")
_MARKER = re.compile(r"\d{1,2}\*\[|\d{1,2}\*|\d{1,2}\[")


@dataclass
class Copy:
    """An archived copy of an Act."""
    saved: str            # capture date, ISO
    text: str             # body without footnotes, markers or brackets; one space between words
    cites: set[str]       # amending laws its footnotes name: "Act 4 of 2005", "1970" (named Acts by year)
    newest: int           # the year of the newest amending law it cites (0 if none)


def read_copy(raw: str, saved: str) -> Copy:
    lines, notes, in_notes = [], [], False
    for line in raw.replace("\r", "").splitlines():
        if _RULE.match(line):
            in_notes = True  # footnotes follow a rule, until the text resumes after a blank line
            continue
        if _NOTE_LINE.match(line):
            in_notes = True
        elif in_notes and not line.strip():
            in_notes = False if notes and not notes[-1].rstrip().endswith((",", "-")) else in_notes
            continue
        if in_notes:
            notes.append(line)
            continue
        if _PAGE_NO.match(line):
            continue
        lines.append(line)
    body = _plain(_MARKER.sub(" ", "\n".join(lines)).replace("[", " ").replace("]", " "))
    note_text = " ".join(notes)
    cites = {f"Act {int(n)} of {y}" for n, y in _ACT_CITED.findall(note_text)}
    years = [int(y) for _, y in _ACT_CITED.findall(note_text)] + [int(y) for y in _NAMED_ACT_YEAR.findall(note_text)]
    cites |= {y for y in _NAMED_ACT_YEAR.findall(note_text)}
    return Copy(saved, body, cites, max((y for y in years if y <= int(saved[:4])), default=0))


def _canon_by(by: str) -> str:
    m = _ACT_CITED.search(by or "")
    return f"Act {int(m.group(1))} of {m.group(2)}" if m else ""


def predates(copy: Copy, a: Amendment) -> bool:
    """The copy's text is older than the change: it does not cite the amending law, and its newest
    amending law is from an earlier year. The date the copy was saved says little: the old site
    served a 1995 consolidation of the Income-tax Act until 2017."""
    by = _canon_by(a.by)
    year = a.year or (int(a.effective[:4]) if a.effective else None)
    if not by or not year or by in copy.cites:
        return False
    return bool(copy.newest) and year > copy.newest


# ── Finding the earlier words ────────────────────────────────────────────────────────────────

_WORD = re.compile(r"[A-Za-z0-9]+")


def _tokens(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0).lower(), m.start(), m.end()) for m in _WORD.finditer(text)]


def _find(seq: list[str], words: list[str], start: int = 0, stop: int | None = None) -> list[int]:
    stop = len(words) if stop is None else min(stop, len(words))
    n = len(seq)
    return [i for i in range(start, stop - n + 1) if words[i:i + n] == seq]


def _opening(text: str, n: int = 7) -> list[str]:
    return [t[0] for t in _tokens(_plain(text))][:n]


def _section_range(cwords: list[str], section_text: str, next_text: str | None,
                   section: str = "") -> tuple[int, int]:
    """Token range of a section in the copy: from where its opening words stand (today's) to where
    the next section's do; the section's number must stand just before them (a renumbered or
    rewritten section's opening may be another's). The whole copy when that cannot be told."""
    starts = _find(_opening(section_text), cwords)
    if len(starts) != 1 or (section and section.lower() not in cwords[max(0, starts[0] - 25):starts[0]]):
        return 0, len(cwords)
    lo = starts[0]
    ends = _find(_opening(next_text), cwords, lo + 1) if next_text else []
    return lo, ends[0] if ends else len(cwords)


_LABEL = re.compile(r"^\s*(\((?:\d{1,3}[A-Z]{0,2}|[a-z]{1,2}|[ivx]{1,5})\)|Explanation\b|Provided\b)")


def _structural(span: str, after: str, region: str, today: str) -> str | None:
    """A whole clause, sub-section, Explanation or proviso replaced: in the copy's section, the
    passage that opens with the same label, up to the label that follows the span today.
    Only when that label stands once in the copy's section; an Explanation or proviso has no
    number, so also only when today's section has just the one ("for the third proviso" in a
    copy with one proviso means the copy is the wrong place)."""
    m = _LABEL.match(span)
    if not m:
        return None
    label = m.group(1)
    if label in ("Explanation", "Provided"):
        pattern = re.compile(rf"(?<![\w(]){label}\b")
        if len(pattern.findall(today)) != 1:
            return None
    else:
        pattern = re.compile(rf"(?:^|\s){re.escape(label)}\s")
    hits = list(pattern.finditer(region))
    if len(hits) != 1:
        return None
    start = hits[0].start()
    nxt = _LABEL.match(after)
    if nxt:
        follow = re.compile(rf"(?:^|\s){re.escape(nxt.group(1))}" + (r"\b" if nxt.group(1)[0] != "(" else r"\s"))
        end_m = follow.search(region, hits[0].end())
        end = end_m.start() if end_m else None
    else:
        end = len(region) if not after.strip() else None  # the span ends the section
    if end is None or end - start > 6 * len(span) + 600:
        return None
    return region[start:end].strip(" ,;:")


def earlier_words(current_section: str, a: Amendment, copy: Copy, next_section: str | None = None,
                  context: int = 8) -> str | None:
    """The words the span replaced, as the copy has them; None when they cannot be placed for sure."""
    span = _plain(a.new_text)
    now = _plain(current_section)
    if not span or not now:
        return None
    ctoks = _tokens(copy.text)
    cwords = [t[0] for t in ctoks]
    span_words = [t[0] for t in _tokens(span)]
    now_tokens = _tokens(now)
    now_words = [t[0] for t in now_tokens]
    at = _find(span_words[:min(12, len(span_words))], now_words)
    if len(at) != 1:
        return None  # the span is not in today's section, or not once
    i, j = at[0], min(at[0] + len(span_words), len(now_words))
    lo, hi = _section_range(cwords, current_section, next_section, a.section)

    def accept(old: str) -> str | None:
        old = _tidy(old)
        if not old or old[0] in "),;.:" or [t[0] for t in _tokens(old)] == span_words:
            return None  # the copy already has today's words: it is not older than the change
        if _units(old) - _units(span):
            return None  # it runs into a sub-section or clause the span does not have: misplaced
        return old

    for k in range(context, 3, -1):  # the longest unambiguous anchors on both sides, four words at least
        before = now_words[max(0, i - k):i]
        after = now_words[j:j + k]
        if not before or not after:
            continue
        starts = _find(before, cwords, lo, hi)
        if len(starts) != 1:
            continue
        s = starts[0] + len(before)
        limit = s + 3 * len(span_words) + 80
        ends = _find(after, cwords, s, limit + len(after))
        if not ends:
            continue
        e = ends[0]
        if e <= s:
            return None  # nothing between: the change inserted words, not replaced them
        # from the end of the words before to the start of the words after: "(ii)" keeps its bracket
        return accept(copy.text[ctoks[s - 1][2]:ctoks[e][1]].strip(" ,;:"))
    if (lo, hi) == (0, len(cwords)):
        return None  # without the section's place in the copy, a label could be anyone's
    region = copy.text[ctoks[lo][1]:ctoks[hi - 1][2] if hi < len(ctoks) else len(copy.text)]
    after_text = now[now_tokens[j][1]:] if j < len(now_tokens) else ""
    return accept(_structural(span, after_text, region, now) or "")


_STRAY_NOTE = re.compile(r"(?<=[a-z,]) \d{1,2} (?=[a-z])")  # "specified 1 therein": a footnote number


def _tidy(old: str) -> str:
    """Without footnote numbers left in the words, or the next clause's opening bracket."""
    old = _STRAY_NOTE.sub(" ", old)
    old = re.sub(r"\s*\(\s*$", "", old).strip(" ,;:")
    return re.sub(r"\s+([,;.])", r"\1", old)


_UNIT = re.compile(r"(?:^|\s)\((\d{1,3}[A-Z]?|[a-z]{1,2}|[ivx]{1,5})\)\s")


def _units(text: str) -> set[str]:
    """Sub-section and clause labels opening a passage, after the first: "(2)", "(b)", "(iii)"."""
    found = _UNIT.findall(" " + text + " ")
    return set(found[1:]) if _LABEL.match(text) else set(found)


@dataclass
class Filled:
    act: str
    section: str
    by: str
    note: str
    old_text: str
    source: str


def fill(amendments: list[Amendment], sections: dict[str, str], copies: list[Copy]) -> list[Filled]:
    """Earlier wordings for the substitutions that lack them. sections: today's text of each section."""
    out = []
    by_age = sorted(copies, key=lambda c: c.saved, reverse=True)
    order = list(sections)  # document order
    following = {sec: sections[order[k + 1]] for k, sec in enumerate(order[:-1])}
    for a in amendments:
        if a.action != SUBSTITUTED or a.old_text is not None or a.section not in sections:
            continue
        for copy in (c for c in by_age if predates(c, a)):
            old = earlier_words(sections[a.section], a, copy, following.get(a.section))
            if old:
                out.append(Filled(a.act, a.section, a.by, a.note, old,
                                  f"archived India Code copy saved {copy.saved}"))
                break
    return out
