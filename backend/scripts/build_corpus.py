"""Build the statute corpus from an India Code harvest (harvester-kit output).

    python scripts/build_corpus.py <harvest records.jsonl> [--old-codes DIR] [--out data/statutes]

Each principal central Act becomes one JSONL of section chunks, cut from the official text:

  1. The "ARRANGEMENT OF SECTIONS" gives the ordered section numbers, titles and chapters.
  2. Each section is located in the body in that order, so a section ends exactly where the
     next one begins (no bleed of one section into another).
  3. Footnotes, page numbers and footnote markers are removed.

The text comes from each Act's English PDF (India Code's own text extraction stops at 100,000
characters); extractions are cached under data/pdf_text. Amendment, appropriation and repeal
Acts are skipped: India Code text is already consolidated. Repealed Acts are kept and marked.
Long sections are split at sub-section boundaries into parts that share the section number.

The Indian Penal Code, the Code of Criminal Procedure and the Indian Evidence Act are no longer
on India Code; --old-codes points at their official India Code PDFs (ipc_1860.pdf, crpc_1973.pdf,
iea_1872.pdf). Their sections and those of the BNS, BNSS and BSA are linked both ways
("corresponds_to": BNS 103 <-> IPC 302) from app.search.legal_data.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import re
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path

# Acts whose short codes the search already understands (citations, IPC/CrPC/IEA mapping)
KNOWN_CODES = {
    "the bharatiya nyaya sanhita, 2023": "bns",
    "the bharatiya nagarik suraksha sanhita, 2023": "bnss",
    "the bharatiya sakshya adhiniyam, 2023": "bsa",
    "the indian penal code": "ipc",
    "the code of criminal procedure, 1973": "crpc",
    "the indian evidence act, 1872": "iea",
}
OLD_CODE_FILES = {  # replaced in 2024; official India Code PDFs, kept for cross-reference
    "ipc_1860.pdf": {"title": "The Indian Penal Code", "year": "1860", "act_number": "45",
                     "replaced_by": "The Bharatiya Nyaya Sanhita, 2023"},
    "crpc_1973.pdf": {"title": "The Code of Criminal Procedure, 1973", "year": "1974", "act_number": "2",
                      "replaced_by": "The Bharatiya Nagarik Suraksha Sanhita, 2023"},
    "iea_1872.pdf": {"title": "The Indian Evidence Act, 1872", "year": "1872", "act_number": "1",
                     "replaced_by": "The Bharatiya Sakshya Adhiniyam, 2023"},
}
# Acts whose content lives in the Acts they amend (India Code text is consolidated); the
# titles are as India Code spells them, misspellings included ("Amendent", "Amnendnent")
SKIP_TITLE = re.compile(r"amendment|\bam(?!usement)[a-z]{3,8}nt\b|\(\s*amend[a-z]*\b|appropriation|"
                        r"repealing and amending|\(no\.\s*\d|vote on account|"
                        r"^the finance(?: \(no\. \d\))? act, \d{4}", re.IGNORECASE)
TEXT_CAP = 100_000  # DSpace's text extractor stops here: such files are cut short
MAX_WORDS = 400   # sections longer than this are split into parts
PART_WORDS = 300

_ROMAN = r"[IVXLC]+[A-Z]?"
_TOC_ENTRY = re.compile(r"^(\d{1,3}[A-Z]{0,3})\.\s*(.*)$")
_CHAPTER = re.compile(rf"^(CHAPTER|PART)\s+({_ROMAN})\b\.?\s*(.*)$")


# ── Text cleaning ──────────────────────────────────────────────────────


# A footnote: "1. Subs. by Act 7 of 2017, s. 3, for …", "2. Ins. by …", "3. The words … omitted by …"
_FOOTNOTE = re.compile(
    r"^\d{1,3}\.\s*(?:Subs|Ins|Omitted|Omit|Rep|Vide|w\.\s?e\.\s?f|Added|Renumbered|Re-numbered|Came|Comes|"
    r"Brought|Inserted|Substituted|Amended|Extended|Declared|Original|Struck|The\s|This\s|These\s|Now\s|"
    r"See\s|For\s|In\s|Certain|Section|Sections|Ss?\.|Cl\.|Clause|Sub-|Words|Figure|Letter|Item|Entry|Para|"
    r"Sch|Repealed|Ord|A\.\s?O|Reg\.|Act\s|Applicable|Notification|Enforced|\d)",
    re.IGNORECASE)


# The first footnote of a page in unmistakable amendment wording; used after a short blank line.
_FIRST_FOOTNOTE = re.compile(
    r"^1\.\s*(?:Subs\.|Ins\.|Omitted|Rep\.|Vide|w\.\s?e\.\s?f|Added|Renumbered|Re-numbered|The\s+words|"
    r"The\s+original|Certain\s+words|Came\s+into|Brought\s+into|This\s+Act\s+has|The\s+Act\s+has|"
    r"\d{1,2}(?:st|nd|rd|th)\s+(?:day|[A-Z][a-z]+)|[A-Z][a-z]+\s+\d{1,2},\s*\d{4}|Section|Sections|Ss?\.\s?\d|"
    r"Cl\.|Sub-s|Clause|Words|Figures?|Letters?|For\s+(?:the|\")|The\s+[“\"])",
    re.IGNORECASE)


def _starts_footnotes(lines: list[str], i: int, long_rule: bool) -> bool:
    """Is the blank line at lines[i] the separator above a page's footnotes? Only if the next
    text line is a footnote; a blank line mid-page (e.g. after '* * *') is not."""
    for nxt in lines[i + 1:i + 4]:
        if nxt.strip():
            s = nxt.strip()
            if re.search(r"\.\s?[—–―]", s):  # "1. Short title.—" is a section, not a footnote
                return False
            s = re.sub(r"\s+\.", ".", s)  # "1. Subs . by" as the PDF spaces it
            if long_rule:  # the printed footnote rule: any numbered note under it is a footnote
                return bool(_FOOTNOTE.match(s) or re.match(r"\d{1,3}\s?\.\s*\S", s))
            return bool(_FIRST_FOOTNOTE.match(s))
    return False


_FOOTNOTE_WORDING = re.compile(
    r"\b(?:ins|subs|sub|omitted|added|rep|renumbered|inserted|substituted|amended)\s?\.?\s+(?:by|ibid)\b|"
    r"\bw\.\s?e\.\s?f\b|\bibid\b|\bvide\b|\bNotifn\b|\bA\.\s?O\.\s?\d{4}",
    re.IGNORECASE)


def _drop_page_footnotes(page: list[str], expect: int = 0) -> tuple[list[str], int]:
    """A page's footnotes are its last lines, numbered 1, 2, 3 … in order, most of them in
    amendment wording ("Ins. by Act 7 of 2017", "(w.e.f. 1-6-2014)"). Drop that run.
    Some prints number footnotes through the whole Act; `expect` is the number after the last
    footnote of the previous page. Returns the page and the last footnote number dropped."""
    numbered = [(i, int(m.group(1))) for i, line in enumerate(page)
                # "1. Subs. by", "1 Subs. by", "1.Art 338B ins. by", "19. Substituted for"
                if (m := re.match(r"\s*(\d{1,3})(?:\s?\.\s*|\s+)(?=[A-Z“\"])", line))
                and not re.search(r"\.\s?[—–―⎯]", line)]
    for start, n in reversed(numbered):
        # the run starts at 1 (or at 2 after a blank line, when the PDF lost footnote 1), or
        # where the previous page's footnotes left off
        if not (n == 1 or (n == 2 and start > 0 and not page[start - 1].strip())
                or (expect > 1 and expect <= n <= expect + 2)):
            continue
        run = [(i, k) for i, k in numbered if i >= start]
        if any(b - a not in (0, 1) for (_, a), (_, b) in zip(run, run[1:])):
            continue  # not a 1, 2, 3 … sequence (a repeat allowed: "2. … 2. …"): a list in the body
        blocks = [" ".join(page[i:j]) for (i, _), (j, _) in zip(run, [*run[1:], (len(page), 0)], strict=True)]
        worded = [bool(_FOOTNOTE_WORDING.search(b) or re.search(
            r"\bomitted\b|\bw\.\s?e\.\s?f|\brepealed in its application|\bextended to\b|\bhas been extended|"
            r"\bcame into force|\bbrought into force|\bSee now\b|\bA\.\s?O\.", b, re.I)) for b in blocks]
        # footnotes sit together: a few continuation lines between entries (more for a long
        # note, such as the list of States an Act was extended to), no heading among them
        gaps_ok = all(j - i <= (15 if w else 8) for (i, _), (j, _), w in zip(run, run[1:], worded))
        if not gaps_ok or any(re.search(r"\.\s?[—–―⎯]", line) or re.match(rf"\s*(?:CHAPTER|PART)\s+{_ROMAN}\b", line)
                              for line in page[start:]):
            continue
        if worded[0] and sum(worded) * 2 >= len(run):
            return page[:start], run[-1][1]
        break
    return page, 0


def _header_key(line: str) -> str:
    """'326 THE CONSTITUTION OF INDIA (Eighth Schedule)' → 'THE CONSTITUTION OF INDIA'."""
    return re.sub(r"\s+", " ", re.sub(r"\(.*?\)|\d+", "", line)).strip(" .-—").upper()


def _drop_running_headers(pages: list[list[str]]) -> list[list[str]]:
    """A line at the top of many pages ("THE CONSTITUTION OF INDIA (Part III.—…)", with a page
    number) is a running header, not text."""
    tops = Counter()
    for page in pages:
        nonblank = [l for l in page if l.strip()]
        for line in set(nonblank[:4] + nonblank[-4:]):  # headers and footers
            if 4 <= len(_header_key(line)) <= 80:
                tops[_header_key(line)] += 1
    headers = {k for k, n in tops.items() if n >= max(3, len(pages) // 4)}
    if not headers:
        return pages
    out = []
    for page in pages:
        # anywhere on the page, a line that is nothing but a header, and the "(Part I.—…)" line
        # that may carry on from it
        kept: list[str] = []
        after_header = False
        for line in page:
            if _header_key(line) in headers:
                after_header = True
                continue
            if after_header and re.fullmatch(r"\s*\(.*\)\s*", line):
                continue
            after_header = after_header and not line.strip()
            kept.append(line)
        out.append(kept)
    return out


def lines_of(text: str) -> list[str]:
    """PDF text → logical lines, with footnote blocks and page numbers removed."""
    out: list[str] = []
    in_footnotes = False
    # India Code stamps an "IndiaCode" watermark at the end of every page of a downloaded PDF:
    # it marks the page break, and so the end of that page's footnotes
    text = re.sub(r"[ \t]*\bIndiaCode\b[ \t]*", "\n\f\n", text)
    # symbol-font glyphs (Private Use Area) are footnote asterisks: "1[370." printed "**1[370."
    text = re.sub(r"[-]", "*", text)
    if "\f" in text:  # PDF text, pages known: drop running headers and each page's footnote run
        pages, last = [], 0
        for page in text.split("\f"):
            kept, dropped_to = _drop_page_footnotes(page.split("\n"), expect=last + 1 if last else 0)
            pages.append(kept)
            last = dropped_to or last  # footnotes numbered through the Act continue from here
        text = "\n\f\n".join("\n".join(page) for page in _drop_running_headers(pages))
    raw_lines = text.replace("\r", "").split("\n")
    for i, raw in enumerate(raw_lines):
        if not raw.strip() and "\f" not in raw:
            # a blank line: footnotes follow if a footnote is next (a long blank rule is the
            # separator in India Code's text extraction; in PDF text it is a short blank line)
            if not in_footnotes and _starts_footnotes(raw_lines, i, long_rule=len(raw) >= 20):
                in_footnotes = True
            continue
        line = raw.strip()
        # a page break or page number ends the page, and its footnotes
        if "\f" in raw or re.fullmatch(r"\d{1,4}", line):
            in_footnotes = False
            continue
        # "1. Subs. by Act …" opens a page's footnotes even without a blank line, and even when
        # the PDF glued it onto the end of the last body line
        fn = None if in_footnotes else (
            re.search(r"(?:^|\s)1\s?\.\s*(?:Subs|Ins|Rep)\s?\.?\s*(?:by|,|ibid)", line)
            # or any numbered line in a footnote's own wording ("2. The words … omitted by Act 23 of
            # 1965", "6. 16th March, 1949, see Notifn. …"), never a section heading (".—")
            or (re.match(r"\d{1,3}\s?\.\s", line) and not re.search(r"\.\s?[—–―]", line)
                and re.search(r"\b(?:by|of)\s+(?:the\s+)?(?:Act|Ord|Reg)\.?\s+\d+\s+of\s+\d{4}|\bibid\b|"
                              r"w\.\s?e\.\s?f\.|\bvide\s+Notifn|\bNotifn\.\s*No|omitted\s+by|"
                              r"\bsee\s+Gazette", line)
                and re.match(r"\d{1,3}\s?\.\s*(?:Subs|Ins|Rep|Omitted|The\s|\d{1,2}(?:st|nd|rd|th)\s|"
                             r"[A-Z][a-z]+\s+\d{1,2},|Added|Renumbered|Came|Brought|Vide|Certain|Cl\.|"
                             r"Clause|Sub-s|Section|Ss?\.\s?\d|For\s|This\s|Now\s|See\s|In\s)", line)))
        if fn:
            if fn.start() > 0:
                out.append(re.sub(r"\s+", " ", line[:fn.start()]).strip())
            in_footnotes = True
        # body text resuming ends footnotes too, should a page end go unnoticed: a section or
        # article heading ("12. Definition.—"), a numbered clause ("(2) …"), a chapter or part
        if in_footnotes and (re.match(r"\[?\d{1,3}[A-Z]{0,3}\s?\.\s*[^.]{3,200}?\.\s?[—–―⎯]", line)
                             or re.match(r"\(\d{1,3}[A-Z]?\)\s+[A-Z]", line)
                             or re.match(rf"(?:CHAPTER|PART)\s+{_ROMAN}\b", line)):
            in_footnotes = False
        if in_footnotes or not line:
            continue
        out.append(re.sub(r"\s+", " ", line))
    return out


_NUMBERED_WORDS = re.compile(r"(?:sections?|sub-sections?|articles?|rules?|clauses?|items?|forms?|schedules?|"
                             r"orders?|regulations?|paragraphs?|chapters?|parts?|entry|entries|no|rs|act)$",
                             re.IGNORECASE)


def _drop_footnote_digit(m: re.Match) -> str:
    """'date1 as' → 'date as', but 'section159' (a missing space in the PDF) → 'section 159'."""
    word, digits = m.group(1), m.group(2)
    return f"{word} {digits}" if _NUMBERED_WORDS.search(word) else word


def clean_body(text: str) -> str:
    text = re.sub(r"(?<![\w(])\d{1,3}\s?\[", "[", text)          # amendment footnote markers: 3[and
    text = re.sub(r"\b([A-Za-z]+|\])(\d{1,3})(?=[\s,.;:)]|$)", _drop_footnote_digit, text)
    text = re.sub(r"(?<=\s)\d{1,3}\*", "*", text)
    text = re.sub(r"[―–]+|——|––", "—", text)
    text = re.sub(r"(?<=[a-z])- (?=[a-z])", "-", text)              # "cross- examined" (line break)
    text = re.sub(r"(?<=[a-z]) -(?=[a-z])", "-", text)              # "twenty -sixth", "ill -will"
    text = re.sub(r"\s+([,.;:”])", r"\1", text)
    return re.sub(r"[ \t]+", " ", text).strip()


# ── Arrangement of sections ────────────────────────────────────────────


def parse_arrangement(lines: list[str]) -> tuple[list[dict], int]:
    """[{number, title, chapter, chapter_title}] from the table of contents, and the body start."""
    start = next((i for i, l in enumerate(lines) if "ARRANGEMENT OF SECTIONS" in l.upper()), None)
    if start is None:
        # no heading: the arrangement is the last list starting "1." before the enacting formula
        # (a "LIST OF AMENDING ACTS", also numbered from 1, may come before it)
        formula = next((i for i, l in enumerate(lines) if re.search(r"\benacted\b", l, re.I)), None)
        # (a list of Acts — "1. The … (Amendment) Act, 2009" — is the amending-Acts list, not it)
        ones = [i for i in range(formula or 0) if re.match(r"1\.\s", lines[i])
                and not re.search(r"\.\s?[—–―]", lines[i]) and not re.search(r"\(\d+ of \d{4}\)", lines[i])
                and not re.search(r"\b(?:Act|Code)\b[,.]?\s*(?:\(|\d{4}|$)", lines[i] + " " + lines[min(i + 1, len(lines) - 1)])]
        if not ones:
            return [], 0
        start = ones[-1] - 1
    # the enacting formula: "BE it enacted by Parliament…", "It is hereby enacted as follows:—"
    # ("promulgate" is the formula of a Regulation; an Act may also mention a promulgated Ordinance
    # in its Statement of Objects, so it is only a fallback)
    body = None
    # failing a formula, the body starts at section 1's heading: "1. Short title….—(1) …"
    for formula in (r"\benacted\b", r"^(?:\d{1,3}\s?)?\[?1\s?\.\s*\S.{0,200}?\.\s?[—–―]", r"\bordained\b",
                    r"\bpromulgate"):
        body = next((i for i in range(start, len(lines)) if re.search(formula, lines[i], re.I)), None)
        if body is not None:
            break
    if body is None:  # fall back to the second "1." line: the first is in the arrangement
        ones = [i for i in range(start, len(lines)) if re.match(r"1\.\s", lines[i])]
        if len(ones) < 2:
            return [], 0
        body = ones[1]
    return toc_entries(lines, start, body), body


def toc_entries(lines: list[str], start: int, body: int, group: str = "CHAPTER",
                bracketed: bool = False) -> list[dict]:
    """Entries of an arrangement between lines start and body, grouped under CHAPTER (or PART)
    headings. bracketed: also keep omitted entries printed in brackets ("[2A. … —Omitted.]")."""
    entry = re.compile(r"^\[?(\d{1,3}[A-Z]{0,3})\.\s*(.*?)\]?$") if bracketed else _TOC_ENTRY
    entries: list[dict] = []
    chapter = chapter_title = ""
    i = start + 1
    while i < body:
        line = lines[i]
        m = _CHAPTER.match(line)
        if m and m.group(1) == group:
            chapter = f"{group.title()} {m.group(2)}"
            title = m.group(3).strip()
            if not title and i + 1 < body and lines[i + 1].isupper() and not entry.match(lines[i + 1]):
                i += 1
                title = lines[i]
            chapter_title = title.title()
        elif (e := entry.match(line)):
            entries.append({"number": e.group(1), "title": e.group(2).strip(), "chapter": chapter,
                            "chapter_title": chapter_title})
        elif entries and not entries[-1]["title"].endswith((".", "]")) and not line.isupper() \
                and line not in ("SECTIONS", "SECTIONS.", "ARTICLES", "ARTICLES."):
            entries[-1]["title"] += " " + line  # title wrapped onto the next line
        i += 1
    # numbers must increase; anything else is a footnote or a stray line
    seen: set[str] = set()
    return [e for e in entries if not (e["number"] in seen or seen.add(e["number"]))]


_PART_LABEL = re.compile(r"^(?:[A-Z]{1,3}\.\s?[—–-]|\d{1,2}\.\s?[—–-]|(?:CHAPTER|PART|SUB-PART)\b)")


# "[S. 98 of the 1961 Act]" after a heading: the section of the Act this one replaced
_OLD_SECTION_NOTE = re.compile(r"\s*\[\s*Ss?\.\s*([^\]]+?)\s+of\s+the\s+(\d{4})\s+Act\s*\]\s*$")


def _heading_above(lines: list[str], k: int) -> list[str]:
    """The heading printed on the line(s) just above line k, or [] if those are body text.
    A heading is short, starts with a capital and is not a clause; it may wrap over up to three
    lines, end with an amendment bracket or a note of the old section it replaces, and (rarely)
    lack its final full stop."""
    def clean(s: str) -> str:
        s = _OLD_SECTION_NOTE.sub(".", s.strip())  # the note is read separately
        return re.sub(r"^\d{1,3}\s?\[|\]$", "", s).strip()  # amendment marks: "18[Deduction …]"

    def plausible(s: str, first: bool) -> bool:
        s = clean(s)
        return (bool(s) and 3 <= len(s) <= 160 and not _PART_LABEL.match(s) and not s.isupper()
                and (s[0].isupper() or s[0] in "“\"‘" or not first) and not re.match(r"[(\d\[]", s)
                and not re.search(r"[;:—]\s*$|\bshall\b|\bmeans\b", s))

    def boundary(s: str) -> bool:  # the line before a heading: end of the last section, a part
        s = s.strip()           # label, or a chapter title in capitals
        return (not s or s.endswith((".", ";", ":", "]", "—")) or bool(_PART_LABEL.match(s))
                or (s.isupper() and len(s) > 3))

    for n in (3, 2, 1):  # the longest wrap that fits
        if k < n + 1 and not (k == n):
            continue
        block = lines[k - n:k]
        final = clean(block[-1])
        if not (plausible(block[0], first=True) and all(plausible(b, first=False) for b in block[1:])):
            continue
        if any(clean(b).endswith((".", ";", ":", "—")) for b in block[:-1]):
            continue  # a full stop inside means these are not one heading
        # a wrapped heading must start right after a boundary; a one-line heading needs no check
        if n > 1 and not boundary(lines[k - n - 1] if k - n - 1 >= 0 else ""):
            continue
        if final.endswith(".") or (n == 1 and len(final) <= 120):
            return [b.strip() for b in block]
    return []


def headings_in_body(lines: list[str]) -> tuple[list[dict], int]:
    """For an Act printed without an arrangement: its section headings ("12. Title.—") in
    order after the enacting formula, numbers rising."""
    body = next((i for i, l in enumerate(lines) if re.search(r"\benacted\b|\bWHEREAS\b", l, re.I)), 0)
    entries: list[dict] = []
    last = 0
    for line in lines[body:]:
        m = re.match(r"^\**(?:(?:\d{1,3}\*?\s?)?\[\s?)*(\d{1,3})([A-Z]{0,2})\s?\.\s*([^.—]{3,200}?)\s?\.\s?[—–―⎯-]", line)
        if m and int(m.group(1)) in (last, last + 1, last + 2):
            entries.append({"number": m.group(1) + m.group(2), "title": m.group(3).strip() + ".",
                            "chapter": "", "chapter_title": ""})
            last = int(m.group(1))
    if len(entries) >= 3:
        return entries, body
    # the newer print: each heading on its own line ABOVE its number ("Definitions." then
    # "2. In this Act, …"); numbers must rise, and the line above must look like a heading
    entries, last = [], 0
    body_lines = lines[body:]
    for k, line in enumerate(body_lines):
        m = re.match(r"^\**(?:(?:\d{1,3}\*?\s?)?\[\s?)*(\d{1,4})([A-Z]{0,2})\s?\.\s+(?=(?:\d{1,3}\s?)?\[?\s?\(1\)|[A-Z“\"]|\d{1,3}\s?\[\s?[A-Z(])", line)
        if not m or k == 0:
            continue
        digits = m.group(1)
        if int(digits) - last > 20 and len(digits) > 1 and 0 < int(digits[1:]) - last <= 20:
            digits = digits[1:]  # a footnote digit fused to the number: "5207." is note 5 + s. 207
        num = int(digits)
        if not (0 < num - last <= 20 or (m.group(2) and num == last)):
            continue
        # the old-section note may sit on its own line: heading / "[Ss. 115B … of the 1961 Act]" / number
        note_line = bool(_OLD_SECTION_NOTE.fullmatch(" " + body_lines[k - 1].strip()))
        heads = _heading_above(body_lines, k - 1 if note_line else k)
        if heads:
            title = " ".join(heads) + (" " + body_lines[k - 1].strip() if note_line else "")
            note = _OLD_SECTION_NOTE.search(title)
            entries.append({"number": digits + m.group(2), "title": _OLD_SECTION_NOTE.sub(".", title).replace("..", "."),
                            "chapter": "", "chapter_title": "", "above": len(heads) + note_line,
                            "old_sections": re.findall(r"\d+[A-Z]*", note.group(1)) if note else [],
                            "old_year": note.group(2) if note else None})
            last = num
    if len(entries) >= 3:
        return entries, body
    # a Gazette print: "1. (1) This Act may be called …", headings in the margin (lost); take
    # the numbered lines that run 1, 2, 3 … in strict order, so a list inside a section cannot
    entries, last = [], 0
    for line in lines[body:]:
        m = re.match(r"^\**(?:(?:\d{1,3}\*?\s?)?\[\s?)*(\d{1,3})([A-Z]{0,2})\s?\.\s+(?=(?:\d{1,3}\s?)?\[?\s?\(1\)|[A-Z“\"]|\d{1,3}\s?\[\s?[A-Z(])", line)
        if m and (int(m.group(1)) == last + 1 or (m.group(2) and int(m.group(1)) == last)):
            entries.append({"number": m.group(1) + m.group(2), "title": "", "chapter": "", "chapter_title": ""})
            last = int(m.group(1))
    return entries, body


def _roman(n: int) -> str:
    out = ""
    for value, sym in ((100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:
            out += sym
            n -= value
    return out


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def cut_sections(lines: list[str], entries: list[dict], body: int) -> tuple[list[dict], str]:
    """Locate each entry's start in the body; a section runs to the next one's start."""
    text = "\n".join(lines[body:])

    def spellings(number: str) -> list[str]:
        """'38I' is also printed '38-I'; old Acts number sections in Roman numerals ('X.')."""
        out = [re.escape(number)]
        m = re.fullmatch(r"(\d+)([A-Z]+)", number)
        if m:
            out.append(rf"{m.group(1)}\s?-\s?{m.group(2)}")
        if number.isdigit() and int(number) < 400:
            out.append(_roman(int(number)))
        return out

    def candidates(number: str, loose: bool = False) -> list[tuple[int, int]]:
        # "29A.", "[29A.", "2[29A.", "2 [29A.", "1[ 2[139.", "*30D." at the start of a line:
        # (start, end of number). Loose (only between two anchored sections): a footnote digit
        # fused to the number ("1145." is footnote 1 + section 145) and a missing full stop.
        prefix = r"(?m)^\**(?:(?:\d{1,3}\*?\s?)?\[\s?)*\*?"
        found = []
        for num in spellings(number):
            if loose:
                pattern = rf"{prefix}(?:\d{{1,2}}(?={num}(?:\s?\.|\s+[“\"A-Z])))?{num}(?:\s?\.|\s+(?=[“\"A-Z]))\s*"
            else:
                pattern = rf"{prefix}{num}\s?\.\s*"
            found += [(m.start(), m.end()) for m in re.finditer(pattern, text)]
        return sorted(set(found))

    def titled(e: dict, start: int, end: int) -> bool:
        if e.get("above"):  # the heading is printed on the line(s) above the number
            return _key(e["title"])[-24:] in _key(text[max(0, start - 400):start])[-160:]
        head = _key(e["title"])[:8]
        after = _key(text[end:end + 120])
        return not head or after.startswith(head) or head in after[:30]

    # Pass 1: the largest set of sections whose number AND heading match that is in document
    # order (a longest increasing subsequence), so one stray match cannot derail the rest.
    pairs = sorted(((k, s) for k, e in enumerate(entries) for s, end in candidates(e["number"]) if titled(e, s, end)),
                   key=lambda p: (p[0], -p[1]))
    found: dict[int, int] = {}
    if pairs:
        tails: list[int] = []      # tails[n]: index in pairs of the smallest end of a run of n+1
        prev = [-1] * len(pairs)
        for idx, (_, s) in enumerate(pairs):
            n = bisect.bisect_left([pairs[t][1] for t in tails], s)
            prev[idx] = tails[n - 1] if n else -1
            if n == len(tails):
                tails.append(idx)
            else:
                tails[n] = idx
        idx = tails[-1]
        while idx != -1:
            found[pairs[idx][0]] = pairs[idx][1]
            idx = prev[idx]

    def gap(k: int) -> tuple[int, int]:
        lo = max((v for j, v in found.items() if j < k), default=-1)
        hi = min((v for j, v in found.items() if j > k), default=len(text))
        return lo, hi

    # Pass 2: a section whose heading did not match takes the first number match between its anchors.
    for k, e in enumerate(entries):
        if k in found:
            continue
        lo, hi = gap(k)
        hit = next((s for s, _ in candidates(e["number"]) if lo < s < hi), None)
        if hit is None:
            hit = next((s for s, _ in candidates(e["number"], loose=True) if lo < s < hi), None)
        if hit is not None:
            found[k] = hit
    # Pass 3: a section whose number the PDF garbled ("37ZA" for 378ZA) is found by its full
    # heading at the start of a numbered line between its anchors.
    for k, e in enumerate(entries):
        if k in found or len(_key(e["title"])) < 12:
            continue
        lo, hi = gap(k)
        for m in re.finditer(r"(?m)^\**(?:(?:\d{1,3}\*?\s?)?\[\s?)*\d{1,3}[A-Z-]{0,4}\s?\.\s*", text[lo + 1:hi]):
            end = lo + 1 + m.end()
            if _key(text[end:end + 200]).startswith(_key(e["title"])[:40]):
                found[k] = lo + 1 + m.start()
                break
    starts = sorted(((v, entries[k]) for k, v in found.items()), key=lambda t: t[0])
    sections = []
    for (s, e), nxt in zip(starts, [*starts[1:], (None, None)], strict=True):
        end = nxt[0] if nxt[0] is not None else len(text)
        raw = text[s:end]
        if nxt[1] is not None and nxt[1].get("above"):  # the next heading sits above its number
            raw = "\n".join(raw.rstrip("\n").split("\n")[:-nxt[1]["above"]])
        sections.append({**e, "raw": raw})
    tail = ""
    if sections:  # schedules follow the last section
        last = sections[-1]
        m = re.search(r"(?m)^(?:THE\s+)?(?:\w+\s+)?SCHEDULE\b", last["raw"])
        if m:
            tail = last["raw"][m.start():]
            last["raw"] = last["raw"][:m.start()]
    return sections, tail


def section_text(raw: str, number: str, title: str) -> str:
    """Body of a section without its number and title prefix."""
    body = " ".join(raw.split("\n"))
    # the number as printed: "29A.", "2 [29A.", "1145." (footnote 1 + 145), "*30D.", "38-I.", "X.",
    # or garbled ("37ZA." for 378ZA): whatever cut_sections matched at the start of the line
    body = re.sub(r"^\**(?:(?:\d{1,3}\*?\s?)?\[\s?)*\*?(?:\d{1,5}(?:\s?-\s?)?[A-Z]{0,4}|[IVXLC]{1,7})(?:\s?\.|\s+)\s*",
                  "", body)
    if title.strip():
        title_first = re.escape(title.strip("[“” .").split()[0])
        body = re.sub(rf"^[“\"]?(?={title_first})", "", body)
    # strip the marginal title ("Definitions.—") when present
    t = re.escape(title.strip("[“” .").split()[0]) if title.strip("[“” .") else None
    if t and re.match(t, body):
        stripped = re.sub(rf"^{t}.{{0,300}}?\.\s*[”\"]?\s*[—–―-]+\s*", "", body, count=1)
        if stripped == body:  # a heading printed without its dash: "Advisory Committee. (1) …"
            whole = r"\s*".join(re.escape(w) for w in title.strip("[“” .").split())
            stripped = re.sub(rf"^{whole}\s?\.?\s*", "", body, count=1)
        body = stripped
    # trailing chapter/part headings belong to the next section (a heading has no full stop
    # after it; "PART II" mentioned mid-text is followed by more sentences)
    body = re.sub(rf"\s+(?:CHAPTER|PART)\s+{_ROMAN}\b[^.]{{0,250}}$", "", body)
    return clean_body(body)


def split_parts(text: str) -> list[str]:
    words = text.split()
    if len(words) <= MAX_WORDS:
        return [text]
    # break before "(2)", "(3)" ... sub-sections; pack into ~PART_WORDS pieces
    pieces = re.split(r"(?=\s\(\d{1,3}[A-Z]?\)\s)", text)
    parts: list[str] = []
    cur = ""
    for p in pieces:
        if cur and len((cur + p).split()) > PART_WORDS:
            parts.append(cur.strip())
            cur = p
        else:
            cur += p
    if cur.strip():
        parts.append(cur.strip())
    # a single sub-section can still be huge: fall back to fixed windows
    out: list[str] = []
    for p in parts:
        w = p.split()
        out.extend(" ".join(w[i:i + PART_WORDS]) for i in range(0, len(w), PART_WORDS))
    return out


# ── Acts ───────────────────────────────────────────────────────────────


def slug(title: str) -> str:
    t = re.sub(r"^the\s+", "", title.lower().rstrip(". "))
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")


def build_act(meta: dict, text: str) -> tuple[list[dict], dict]:
    title = re.sub(r"\s+", " ", meta["title"]).strip().rstrip(".")
    code = KNOWN_CODES.get(title.lower(), slug(title))
    status = "repealed" if str(meta.get("repealed")) == "True" or meta.get("replaced_by") else "in force"
    lines = lines_of(text)
    if meta.get("until"):  # a booklet that carries Rules after the Act: keep the Act only
        end = next((i for i, l in enumerate(lines) if re.search(meta["until"], l)), len(lines))
        lines = lines[:end]
    # manifest "sections": false — a scan whose section numbers OCR garbled: kept as whole text
    entries, body = parse_arrangement(lines) if meta.get("sections", True) else ([], 0)
    listed = bool(entries)
    if meta.get("sections", True) is False:
        entries = []
    if not entries and re.search(r"\)?\s*AMENDMENT\s+ACT,?\s*[T\d]", " ".join(lines[:60]).upper()):
        # a Gazette print of an amending Act filed under the principal Act's name
        return [], {"title": title, "code": code, "toc": 0, "found": 0, "chunks": 0, "truncated": False,
                    "from_pdf": bool(meta.get("_from_pdf")), "missing": [], "kept": 0,
                    "headings_from": "none", "repealed_placeholders": 0, "skipped": "amending Act",
                    "source": meta.get("source") or "indiacode.gov.in"}
    if not entries and meta.get("sections", True):  # no arrangement (some very old Acts):
        entries, body = headings_in_body(lines)         # read the headings off the body
    sections, schedules = cut_sections(lines, entries, body) if entries else ([], "")
    start = next((i for i, l in enumerate(lines) if re.search(r"\benacted\b|\bWHEREAS\b", l, re.I)), 0)
    whole = "\n".join(lines[start:])
    cut_words = sum(len(s["raw"].split()) for s in sections)
    if not listed and cut_words < 0.4 * len(whole.split()):
        sections = []  # headings read off the body caught too little: the whole text is safer
    if not sections:  # no headings at all (a few Acts of the 1830s-50s): keep the whole text
        if len(whole.split()) > 20:
            sections = [{"number": "FULL", "title": "Full text", "chapter": "", "chapter_title": "",
                         "raw": whole}]
    # a section the arrangement lists as repealed or omitted may have no text at all
    got = {s["number"] for s in sections}
    for e in entries:
        if e["number"] not in got and re.search(r"repealed|omitted", e["title"], re.IGNORECASE):
            sections.append({**e, "raw": f"{e['number']}. [{e['title'].strip('[]. ')}.]", "_empty": True})
    order = {x["number"]: k for k, x in enumerate(entries)}
    sections.sort(key=lambda s: order.get(s["number"], -1))
    records = []
    for s in sections:
        body_text = section_text(s["raw"], s["number"], s["title"])
        # (a Gazette print keeps its headings in the margin, lost in extraction)
        sec_title = clean_body(s["title"]).strip("[] ") or f"Section {s['number']}"
        parts = split_parts(body_text)
        for n, part in enumerate(parts, 1):
            label = f"part {n}/{len(parts)}" if len(parts) > 1 else ""
            records.append({
                "chunk_id": f"{code}::{s['number']}::{n}",
                "act_code": code.upper() if code in KNOWN_CODES.values() else code,
                "act_title": title,
                "source_type": code,
                "source": meta.get("source") or "indiacode.gov.in",  # archived URL for a replaced Act
                "section_number": s["number"],
                "section_title": sec_title,
                "chapter": s["chapter"],
                "chapter_title": s["chapter_title"],
                "label": label,
                "hierarchy": f"Section {s['number']}" + (f" > {label}" if label else ""),
                "chunk_type": "section",
                "text": part,
                "full_text": body_text if len(parts) == 1 else part,  # parts rejoin by section
                "citation": f"Section {s['number']} — {sec_title}\n"
                            f"{s['chapter']}{', ' if s['chapter'] else ''}{title}",
                "token_count": len(part.split()),
                "needs_split": False,
                "year": meta.get("year"),
                "act_number": meta.get("act_number"),
                "ministry": meta.get("ministry"),
                "handle": meta.get("handle"),
                "status": status,
                "replaced_by": meta.get("replaced_by"),
                # "[S. 98 of the 1961 Act]" printed with the heading: linked in main()
                "_old_sections": s.get("old_sections") or [],
                "_old_year": s.get("old_year"),
            })
    if schedules.strip():
        sched = clean_body(" ".join(schedules.split("\n")))
        for n, part in enumerate(split_parts(sched), 1):
            records.append({
                "chunk_id": f"{code}::SCHEDULE::{n}", "act_code": code.upper() if code in KNOWN_CODES.values() else code,
                "act_title": title, "source_type": code, "source": meta.get("source") or "indiacode.gov.in",
                "section_number": "SCHEDULE", "section_title": "Schedule", "chapter": "", "chapter_title": "",
                "label": f"part {n}", "hierarchy": "Schedule", "chunk_type": "schedule", "text": part,
                "full_text": part, "citation": f"Schedule — {title}", "token_count": len(part.split()),
                "needs_split": False, "year": meta.get("year"), "act_number": meta.get("act_number"),
                "ministry": meta.get("ministry"), "handle": meta.get("handle"),
                "status": status, "replaced_by": meta.get("replaced_by"),
            })
    truncated = not meta.get("_from_pdf") and TEXT_CAP - 100 <= len(text) <= TEXT_CAP
    for r in records:
        r["truncated_source"] = truncated
    got = {x["number"] for x in sections}
    # share of the source's words that reached the corpus (the rest: arrangement, footnotes,
    # page furniture); a low share means text was lost
    kept = sum(len(r["text"].split()) for r in records) / max(1, len(text.split()))
    report = {"title": title, "code": code, "toc": len(entries), "found": len(sections),
              "chunks": len(records), "truncated": truncated, "from_pdf": bool(meta.get("_from_pdf")),
              "missing": [e["number"] for e in entries if e["number"] not in got], "kept": round(kept, 3),
              "headings_from": "arrangement" if listed else ("body" if entries else "none"),
              "repealed_placeholders": sum(1 for s in sections if s.get("_empty")),
              "source": meta.get("source") or "indiacode.gov.in"}
    return records, report


# ── Repeals ────────────────────────────────────────────────────────────

_REPEAL = re.compile(r"[Tt]he\s+((?:[A-Z][\w’'()&,.-]*|of|and|the|for|in|on|to)(?:\s+(?:[A-Z][\w’'()&,.-]*|of|and|the|for|in|on|to)){0,20}?,\s*\d{4})"
                     r"\s*\(\s*\d+\s+of\s+\d{4}\s*\)(?=(?P<rest>[^.]{0,300}))")  # rest not consumed: chains


def detect_repeals(built: list[tuple[dict, list[dict]]]) -> None:
    """Read each Act's repeal section ("The Mahatma Gandhi … Act, 2005 (42 of 2005) … shall stand
    repealed") and link the repealed Act to it. "is hereby repealed" takes effect at once: the
    old Act is marked repealed. "shall stand repealed" from a date to be notified only links
    the two: the old Act stays in force until that date, which the text cannot tell us."""
    def key(title: str) -> str:  # year kept: the Companies Act, 1956 is not the Companies Act, 2013
        return re.sub(r"[^a-z0-9]", "", re.sub(r"^the\s+", "", title.strip().lower()))

    by_key = {key(records[0]["act_title"]): records for _, records in built}
    for _, records in built:
        new_title = records[0]["act_title"]
        for r in records:
            if not re.search(r"\brepeal", r["section_title"], re.IGNORECASE):
                continue
            for m in _REPEAL.finditer(r["full_text"]):
                rest = m.group("rest")
                # the Act is the subject of the repeal: "… (42 of 1965) is hereby repealed", "… and the
                # Indian Wireless Telegraphy Act, 1933 (17 of 1933) are hereby repealed", "…, together
                # with all rules … made thereunder, shall stand repealed"
                if not re.match(r"\s*(?:(?:,|and\b|together with|along with)[^.;]{0,300}?)?\b(?:is|are|shall)\s+"
                                r"(?:hereby\s+)?(?:stand\s+)?repealed", rest):
                    continue
                # a partial repeal ("sections 81, 82 and 94 of the Indian Trusts Act, 1882 … are
                # hereby repealed", "so much of the …") leaves the rest of the Act in force
                # (only a reference governing the Act itself counts: "Sections 3 to 33 of the …",
                # "so much of the …"; not "Subject to … this section, the enactments namely, the …")
                before = r["full_text"][max(0, m.start() - 60):m.start()]
                if re.search(r"(?:\b(?:sections?|ss?\.|clauses?|parts?|chapters?|schedules?|rules?|paragraphs?|"
                             r"articles?)\s+[\w(),\s-]{0,40}?\s+of|\bso much of|\bprovisions of)\s*$",
                             before, re.IGNORECASE) or re.search(r"\bin so far\b|\bso far as\b|\bin its application\b", rest):
                    continue
                # "So much of any law in force in Goa … as corresponds to the Code of Civil Procedure"
                sentence = r["full_text"][max(0, m.start() - 250):m.start()].split(".")[-1]
                if re.search(r"\bso much of\b|\bcorresponds? to\s*$|\blaws? in force in\b", sentence, re.IGNORECASE):
                    continue
                old = by_key.get(key(m.group(1)))
                if not old or old[0]["act_title"] == new_title or old[0].get("replaced_by"):
                    continue
                immediate = bool(re.search(r"\b(?:is|are)\s+hereby\s+repealed", rest))
                for o in old:
                    o["replaced_by"] = new_title
                    if immediate:
                        o["status"] = "repealed"
                    else:
                        o["repeal_pending"] = True  # repealed from a date the Government notifies


# ── Authority ──────────────────────────────────────────────────────────


def _title_key(title: str) -> str:
    """'The Indian Penal Code' / 'Indian Penal Code, 1860 (45 of 1860)' -> 'indianpenalcode'."""
    t = re.sub(r"^the\s+", "", title.strip().lower())
    t = re.sub(r",?\s*\d{4}.*$", "", t)
    return re.sub(r"[^a-z]", "", t)


# States, Union territories, former provinces and cities that name the territory of a local Act
PLACES = ["andaman", "nicobar", "andhra", "arunachal", "assam", "bihar", "chhattisgarh", "goa", "gujarat",
          "haryana", "himachal", "jharkhand", "karnataka", "mysore", "kerala", "travancore", "cochin",
          "madhya pradesh", "maharashtra", "manipur", "meghalaya", "mizoram", "nagaland", "odisha", "orissa",
          "punjab", "panjab", "rajasthan", "ajmer", "sikkim", "tamil nadu", "madras", "telangana", "tripura",
          "uttar pradesh", "uttarakhand", "west bengal", "bengal", "chandigarh", "dadra", "nagar haveli",
          "daman", "diu", "delhi", "jammu", "kashmir", "ladakh", "lakshadweep", "puducherry", "pondicherry",
          "bombay", "calcutta", "oudh", "central provinces", "berar", "hyderabad", "coorg", "bhopal",
          "vindhya", "kutch", "saurashtra", "patiala", "north-eastern", "north eastern", "northern india",
          "shillong", "sind", "straits settlement", "agra", "chutia nagpur", "chota nagpur", "dehra dun",
          "kolkata", "mumbai", "chennai", "hill areas"]
_PLACE_RE = re.compile(r"\b(" + "|".join(re.escape(p) for p in sorted(PLACES, key=len, reverse=True)) + r")\b",
                       re.IGNORECASE)


def act_scope(records: list[dict]) -> tuple[str, list[str]]:
    """'national' when the Act extends to the whole of India; 'regional' when its extent clause
    names particular territories, or (with no extent clause) its title names a place. Returns
    the scope and the place names, so a query that names the place still finds the Act."""
    s1 = " ".join(r["text"] for r in records if r["section_number"] in ("1", "FULL"))[:2000]
    title = records[0]["act_title"]
    if re.search(r"extends?\s+to\s+(?:the\s+)?\W*whole\s+of\s+India", s1, re.IGNORECASE):
        return "national", []
    extent = re.search(r"\bextends?\s+(?:only\s+)?to\s+([^.;]{0,200})", s1, re.IGNORECASE)
    places = sorted({p.lower() for p in _PLACE_RE.findall(title + " " + (extent.group(1) if extent else ""))})
    if extent and (places or re.search(r"\b(?:States?|Union territor|territories)\b", extent.group(1))):
        return "regional", places
    if places:
        return "regional", places
    return "national", []


def act_authority(built: list[tuple[dict, list[dict]]]) -> dict[str, float]:
    """How much the rest of the statute book relies on each Act: the number of other Acts that
    cite it, on a log scale in [0, 1]. A replaced Act's citations count for its replacement
    (references to the Indian Penal Code are now read as the Bharatiya Nyaya Sanhita)."""
    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[‘’'“”\"]", "", s.lower())).strip()

    # an Act is cited by its full short title, year included: "the code of criminal procedure, 1973"
    needles = {records[0]["act_title"]: norm(re.sub(r"^the\s+", "", records[0]["act_title"], flags=re.I))
               for _, records in built}
    texts = {records[0]["act_title"]: norm(" ".join(r["text"] for r in records)) for _, records in built}
    counts = {t: sum(1 for citing, text in texts.items() if citing != t and needle in text)
              for t, needle in needles.items()}
    for _, records in built:  # credit the replacement with what cited the old Act
        new = records[0].get("replaced_by")
        if new:
            match = next((t for t in counts if _title_key(t) == _title_key(new)), None)
            if match:
                counts[match] += counts[records[0]["act_title"]]
    top = max(counts.values()) or 1
    return {t: round(math.log1p(n) / math.log1p(top), 3) for t, n in counts.items()}


# ── Old <-> new code links ─────────────────────────────────────────────


def correspondence() -> dict[str, list[str]]:
    """'BNS 103' -> ['IPC 302'] and 'IPC 302' -> ['BNS 103'], from the search's tables."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.search.legal_data import OLD_CODES, SPLIT_TARGETS

    links: dict[str, list[str]] = {}

    def add(a: str, b: str) -> None:
        links.setdefault(a, [])
        if b not in links[a]:
            links[a].append(b)

    for old, (new, table) in OLD_CODES.items():
        for old_num, new_num in table.items():
            add(f"{old} {old_num}", f"{new} {new_num}")
            add(f"{new} {new_num}", f"{old} {old_num}")
    for old_ref, targets in SPLIT_TARGETS.items():
        for t in targets:
            add(old_ref, t)
            add(t, old_ref)
    return links


def word_rejoiner(texts) -> Callable[[str], str]:
    """Corpus-wide repair of words a PDF split in two ("wil l", "h ouse"): join two fragments
    when the whole word is common in the corpus and one fragment barely occurs on its own."""
    texts = list(texts)
    counts = Counter(w.lower() for t in texts for w in re.findall(r"[A-Za-z]+", t))
    hyphenated = Counter(w.lower() for t in texts for w in re.findall(r"[A-Za-z]+-[A-Za-z]+", t))

    def drop_hyphen(m: re.Match) -> str:
        """A word broken across a line ("pro-vident", "develop-ment"): the hyphen goes when the
        whole word is commoner in the corpus than the hyphenated form ("co-operative" stays)."""
        a, b = m.group(1), m.group(2)
        joined = counts[(a + b).lower()]
        return a + b if joined >= 3 and joined > hyphenated[f"{a}-{b}".lower()] else m.group(0)

    def drop_space(m: re.Match) -> str:
        a, b = m.group(1), m.group(2)
        split = counts[(a + b).lower()] >= 3 and min(counts[a.lower()], counts[b.lower()]) <= 2
        return a if split else m.group(0)

    def add_space(m: re.Match) -> str:
        """The opposite fault, a lost space: "MadhyaPradesh", "theIndian" -> two words, when both
        halves are common words, each commoner than the glued form."""
        a, b = m.group(1), m.group(2)
        joined = counts[(a + b).lower()]
        glued = min(counts[a.lower()], counts[b.lower()]) >= max(3, joined + 1)
        return f"{a} {b}" if glued else m.group(0)

    def rejoin(text: str) -> str:
        if not text:
            return text
        text = re.sub(r"\b([A-Za-z]*[a-z])([A-Z][a-z]+)\b", add_space, text)
        text = re.sub(r"\b([A-Za-z]+)-([a-z]+)\b", drop_hyphen, text)
        # the second word is only looked at, so it can pair with the word after it too
        return re.sub(r"\b([A-Za-z]+) (?=([A-Za-z]+)\b)", drop_space, text)

    return rejoin


# ── Sources ────────────────────────────────────────────────────────────


def pdf_to_text(pdf: Path, cache: Path, key: str) -> str:
    from pdf_text import pdf_text  # scripts/pdf_text.py

    cached = cache / f"{key}.txt"
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    text = pdf_text(pdf)
    cache.mkdir(parents=True, exist_ok=True)
    cached.write_text(text, encoding="utf-8")
    return text


def blob_to_text(store: Path, sha256: str, cache: Path) -> str:
    import gzip
    import tempfile

    if (cache / f"{sha256}.txt").exists():
        return (cache / f"{sha256}.txt").read_text(encoding="utf-8")
    blob = store / "blobs" / sha256[:2] / sha256[2:4] / f"{sha256}.gz"
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(gzip.decompress(blob.read_bytes()))
    try:
        return pdf_to_text(Path(tmp.name), cache, sha256)
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def _extract_safely(store: Path, sha256: str, cache: Path) -> str | None:
    """Worker: cache one PDF's text; an error message instead of raising."""
    try:
        blob_to_text(store, sha256, cache)
    except Exception as exc:
        return f"{sha256}: {exc}"
    return None


def latin_share(text: str) -> float:
    letters = [c for c in text[:20000] if c.isalpha()]
    return sum(c.isascii() for c in letters) / max(1, len(letters))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("records", type=Path, nargs="+", help="harvest records.jsonl file(s)")
    ap.add_argument("--store", type=Path, help="harvest raw store (default: <data-dir>/india-code/store)")
    ap.add_argument("--old-codes", type=Path, nargs="+",
                    help="folders of official PDFs not on India Code, each with a manifest.json")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "statutes")
    ap.add_argument("--cache", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "pdf_text")
    ap.add_argument("--only", nargs="*", help="act codes or title substrings to build (default: all)")
    ap.add_argument("--extract-only", action="store_true", help="only extract and cache the PDF text")
    ap.add_argument("--workers", type=int, default=2, help="processes extracting PDF text (default 2)")
    ap.add_argument("--section-links", type=Path, nargs="*", default=[],
                    help='official old->new section tables: {"old_act", "new_act", "sections": [[old, new], …]}')
    args = ap.parse_args()
    store = args.store or args.records[0].resolve().parents[2] / "store"

    acts: dict[str, dict] = {}
    texts: dict[str, str] = {}
    pdfs: dict[str, list[dict]] = {}
    for path in args.records:
        with path.open(encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                d = r["data"]
                if r["kind"] == "act":
                    acts[d["uuid"]] = d
                elif r["kind"] != "file":
                    continue
                elif d.get("script") == "latin" and d.get("text"):
                    if len(d["text"]) > len(texts.get(d["item"], "")):
                        texts[d["item"]] = d["text"]
                elif "pdf" in (d.get("content_type") or "") and d.get("sha256") \
                        and not re.match(r"h", d.get("name") or "", re.IGNORECASE):  # H…pdf: Hindi text
                    if all(p["sha256"] != d["sha256"] for p in pdfs.get(d["item"], [])):
                        pdfs.setdefault(d["item"], []).append(d)

    # one record per principal Act: newest last_modified wins among duplicates
    chosen: dict[str, dict] = {}
    for uuid, meta in acts.items():
        title = re.sub(r"\s+", " ", meta.get("title") or "").strip().rstrip(".")
        if not title or (uuid not in texts and uuid not in pdfs) or SKIP_TITLE.search(title):
            continue
        key = title.lower()
        if key not in chosen or (meta.get("last_modified") or "") > (chosen[key].get("last_modified") or ""):
            chosen[key] = meta
    if args.only:
        wanted = [w.lower() for w in args.only]
        chosen = {k: v for k, v in chosen.items()
                  if any(w == KNOWN_CODES.get(k, slug(k)) or w in k for w in wanted)}

    def text_of(meta: dict) -> str:
        """The English PDF's text (the longest Latin-script one); India Code's text if none."""
        best = ""
        for d in pdfs.get(meta["uuid"], []):
            try:
                t = blob_to_text(store, d["sha256"], args.cache)
            except Exception as exc:  # a broken PDF falls back to the text extraction
                print(f"  ! {meta['title']}: {d.get('name')}: {exc}")
                continue
            if latin_share(t) > 0.9 and len(t) > len(best):
                best = t
        if best:
            meta["_from_pdf"] = True
            return best
        return texts.get(meta["uuid"], "")

    # extract every PDF not yet in the cache, in parallel
    todo = sorted({d["sha256"] for meta in chosen.values() for d in pdfs.get(meta["uuid"], [])
                   if not (args.cache / f"{d['sha256']}.txt").exists()})
    if todo:
        from concurrent.futures import ProcessPoolExecutor

        print(f"  extracting {len(todo)} PDFs with {args.workers} workers", flush=True)
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(_extract_safely, store, sha, args.cache) for sha in todo]
            for n, fut in enumerate(futures, 1):
                if err := fut.result():
                    print(f"  ! {err}")
                if n % 100 == 0:
                    print(f"  extracted {n}/{len(todo)}", flush=True)

    jobs: list[tuple[dict, str]] = []
    for n, key in enumerate(sorted(chosen), 1):
        jobs.append((chosen[key], text_of(chosen[key])))
        if n % 50 == 0:
            print(f"  text ready for {n}/{len(chosen)} acts", flush=True)
    for folder in args.old_codes or []:
        # each folder's manifest.json names its PDFs: Acts India Code no longer has (from
        # scripts/fetch_replaced_statutes.py) or never had (the Income-tax Act, 2025); a folder
        # without one is the three old criminal codes
        manifest_path = folder / "manifest.json"
        old = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else OLD_CODE_FILES
        have = {re.sub(r"\s+", " ", m.get("title") or "").strip().rstrip(".").lower() for m, _ in jobs}
        for name, meta in old.items():
            pdf = folder / name
            if not pdf.exists():
                print(f"  ! missing {pdf}")
                continue
            if meta["title"].lower() in have:  # still on India Code: that copy is newer
                continue
            jobs.append(({**meta, "_from_pdf": True}, pdf_to_text(pdf, args.cache, pdf.stem)))
    if args.extract_only:
        print(f"text ready for {len(jobs)} acts (cache: {args.cache})")
        return 0

    links = correspondence()
    args.out.mkdir(parents=True, exist_ok=True)
    reports = []
    total = Counter()
    built: list[tuple[dict, list[dict]]] = []
    history: dict[str, list] = {}  # act code -> its amendment history, from the raw text's footnotes
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.history.amendments import extract as extract_amendments, reattribute

    for meta, text in jobs:
        records, rep = build_act(meta, text)
        reports.append(rep)
        if not records:
            total["acts_without_sections"] += 1
            continue
        built.append((rep, records))
        # the footnotes the section text drops record every amendment: kept beside the corpus
        amendments = extract_amendments(records[0]["act_code"], text) if meta.get("_from_pdf") else []
        texts: dict[str, str] = {}
        for r in records:
            texts[r["section_number"]] = texts.get(r["section_number"], "") + " " + (r.get("text") or "")
        rep["amendments_refiled"] = reattribute(amendments, texts)
        history[rep["code"]] = amendments
        rep["amendments"] = len(amendments)
        rep["amendments_dated"] = sum(bool(a.effective) for a in amendments)
    rejoin = word_rejoiner(r["text"] for _, records in built for r in records)
    authority = act_authority(built)
    for _, records in built:
        for r in records:
            r["authority"] = authority[records[0]["act_title"]]
    for _, records in built:
        scope, places = act_scope(records)
        for r in records:
            r["scope"], r["places"] = scope, places
    detect_repeals(built)
    # the reverse link, Act to Act: "The Code on Wages, 2019" replaces "The Payment of Wages Act, 1936", …
    replaces: dict[str, list[str]] = {}
    for _, records in built:
        if records[0].get("replaced_by"):
            replaces.setdefault(records[0]["replaced_by"].lower().rstrip("."), []).append(records[0]["act_title"])
    # text quality: an OCR'd scan (a few old Gazette prints) has symbols and digits inside its
    # words ("a^ent", "for^rrn"); such Acts are flagged for readers
    junk = re.compile(r"[A-Za-z][\^<>{}|~\\@#$%][A-Za-z]|[a-z]\d[a-z]")
    for rep, records in built:
        tokens = [t for r in records for t in r["text"].split()]
        per_k = 1000 * sum(1 for t in tokens if junk.search(t)) / max(1, len(tokens))
        rep["ocr_noise_per_1000_words"] = round(per_k, 2)
        for r in records:
            r["text_quality"] = "ocr" if per_k > 1 else "clean"
    # notes printed in a new Act ("[S. 98 of the 1961 Act]") link its sections to the Act it
    # replaced, both ways (the Income-tax Act, 2025 <-> the Income-tax Act, 1961)
    code_of = {records[0]["act_title"]: records[0]["act_code"] for _, records in built}

    def link(a: str, b: str) -> None:
        for x, y in ((a, b), (b, a)):
            links.setdefault(x, [])
            if y not in links[x]:
                links[x].append(y)

    # correspondence tables: official ones (the Income Tax Department's 1961 -> 2025 table) go
    # into corresponds_to; derived ones (scripts/derive_section_links.py, for the Labour Codes)
    # into derived_links with their score, never mixed with the official links
    derived: dict[str, list[dict]] = {}
    for table_path in args.section_links:
        data = json.loads(table_path.read_text(encoding="utf-8"))
        for table in data.get("tables", [data]):
            old_code, new_code = code_of.get(table["old_act"]), code_of.get(table["new_act"])
            if not old_code or not new_code:
                print(f"  ! {table_path.name}: {table['old_act']} or {table['new_act']} not in the corpus")
                continue
            for row in table["sections"]:
                a, b = f"{old_code} {row[0]}", f"{new_code} {row[1]}"
                if table.get("derived"):
                    for x, y in ((a, b), (b, a)):
                        derived.setdefault(x, [])
                        if all(d["ref"] != y for d in derived[x]):
                            derived[x].append({"ref": y, "score": row[2]})
                else:
                    link(a, b)
    for _, records in built:
        for r in records:
            if not r.get("_old_sections"):
                continue
            old_title = next((t for t in replaces.get(r["act_title"].lower().rstrip("."), [])
                              if r["_old_year"] and r["_old_year"] in t), None)
            if old_title:
                for old in r["_old_sections"]:
                    a, b = f"{r['act_code']} {r['section_number']}", f"{code_of[old_title]} {old}"
                    links.setdefault(a, [])
                    links.setdefault(b, [])
                    links[a] += [b] if b not in links[a] else []
                    links[b] += [a] if a not in links[b] else []
    for rep, records in built:
        for r in records:
            r.pop("_old_sections", None)
            r.pop("_old_year", None)
            r["corresponds_to"] = links.get(f"{r['act_code']} {r['section_number']}", [])
            r["derived_links"] = derived.get(f"{r['act_code']} {r['section_number']}", [])
            r["replaces"] = replaces.get(r["act_title"].lower().rstrip("."), [])
            for key in ("text", "full_text", "section_title"):
                r[key] = rejoin(r[key])
        with (args.out / f"{rep['code']}.jsonl").open("w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        total["acts"] += 1
        total["chunks"] += len(records)
        total["sections"] += rep["found"]
    # amendment history beside the corpus: data/amendments/<code>.jsonl (app/history/amendments.py)
    amend_dir = args.out.parent / "amendments"
    amend_dir.mkdir(parents=True, exist_ok=True)
    for code, amendments in history.items():
        with (amend_dir / f"{code}.jsonl").open("w", encoding="utf-8") as f:
            for a in amendments:
                f.write(json.dumps(a.as_dict(), ensure_ascii=False) + "\n")
    total["amendments"] = sum(len(v) for v in history.values())
    (args.out / "_build_report.json").write_text(json.dumps(reports, indent=1, ensure_ascii=False), encoding="utf-8")
    low = [r for r in reports if r["toc"] and r["found"] < 0.9 * r["toc"] and not r["truncated"]]
    cut = [r["title"] for r in reports if r["truncated"]]
    print(f"{total['acts']} acts, {total['sections']} sections, {total['chunks']} chunks -> {args.out}; "
          f"{total['amendments']} amendments -> {amend_dir} "
          f"({sum(r.get('amendments_refiled', 0) for r in reports)} refiled under the section whose text has them)")
    print(f"{total['acts_without_sections']} acts had no arrangement of sections; "
          f"{len(low)} acts matched under 90% of their listed sections")
    print(f"{len(cut)} acts still rely on a text extraction cut short at {TEXT_CAP:,} characters")
    return 0


if __name__ == "__main__":
    sys.exit(main())
