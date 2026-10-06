"""The BNSS First Schedule, Part I: how each offence under the Bharatiya Nyaya Sanhita is
classified (punishment, cognizable or not, bailable or not, which court tries it).

Read from the statute text in the corpus, not written by hand or by a model: these are the facts
an advocate checks first, and the official schedule is the source. Each entry in the text looks
like

    103(1) Murder. Death or imprisonment for life and fine. Cognizable. Non-bailable. Court of Session.

An offence with several rows ("If committed by a public servant", "Ditto") keeps one entry per
row under its section. The schedule's own note applies: its second and third columns indicate
the substance of a section; they are not its definition or its punishment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Every row has exactly one "Bailable"/"Non-bailable" (450 in Part I), so rows are found by it:
# the cognizable column is the nearest "Cognizable..."/"Non-cognizable..." before it (sometimes a
# whole sentence: "Cognizable if information ... is given ..."), the court column follows it.
# Abetment and attempt rows defer to the main offence: "According as offence abetted is bailable
# or non-bailable"
_BAIL = re.compile(r"\b(?P<bail>Bailable|Non-bailable|According as [^.]*?bailable or non-bailable)\b\.?")
_COG = re.compile(r"\b(?:Cognizable|Non-cognizable|According as [^.]*?cognizable or non-cognizable)\b")
_COURT = re.compile(
    r"\s*(?P<court>Court of Sessions?|(?:Any|Chief Judicial|Metropolitan)\s+Magistrate|"
    r"Magistrate\s+(?:of\s+the|f\s+the)\s+first\s*class|(?:The\s+)?Court\s+(?:by|in)\s+which[^.]*)\.?",
    re.IGNORECASE)
# a row that starts with a section number: "103(1) Murder.", "318(4) Cheating ...", "64 Rape."
_SECTION = re.compile(r"^\s*(?P<sec>\d{1,3}[A-Z]?)(?P<sub>(?:\(\d+\))?(?:\([a-z]\))?)\s+(?P<rest>.*)$", re.DOTALL)
_COLUMN_NUMBERS = re.compile(r"\b1 2 3 4 5 6\b")
_PART_II = re.compile(r"\bII\.?\s*[—-]*\s*CLASSIFICATION OF OFFENCES AGAINST OTHER LAWS", re.IGNORECASE)


@dataclass(frozen=True)
class Classification:
    section: str      # "103"
    sub: str          # "(1)", or ""
    offence: str      # "Murder."
    punishment: str   # "Death or imprisonment for life and fine."
    cognizable: str   # the column as written: "Cognizable", "Non-cognizable", "Cognizable if ...",
                      # "According as offence abetted is cognizable or non-cognizable"
    bailable: str     # "Bailable", "Non-bailable", or "According as ..."
    court: str        # "Court of Session"; "" where the schedule leaves it to the row above

    def as_dict(self) -> dict:
        return {"section": self.section + self.sub, "offence": self.offence, "punishment": self.punishment,
                "cognizable": self.cognizable, "bailable": self.bailable, "court": self.court}


def _split_offence(text: str) -> tuple[str, str]:
    """'Murder. Death or imprisonment for life and fine.' -> ('Murder.', 'Death or ...').
    The punishment column starts at the first sentence naming a sentence or a fine."""
    m = re.search(r"(?<=\.)\s+(?=(?:Death|Imprisonment|Simple imprisonment|Fine|Rigorous|The same|"
                  r"Imprisonment for life|Community service|Same as|One[- ]half|On first conviction)\b)", text)
    if not m:
        return text.strip(), ""
    return text[:m.start()].strip(), text[m.end():].strip()


def parse(text: str) -> list[Classification]:
    """Part I rows from the First Schedule's text."""
    text = _COLUMN_NUMBERS.sub(" ", text)
    part_two = _PART_II.search(text)
    if part_two:
        text = text[:part_two.start()]
    # the column headings ("... Bailable or Non-bailable By what Court triable") come right before
    # the first row; their own "Bailable" would otherwise be read as one
    heading = text.find("By what Court triable")
    if heading >= 0:
        text = text[heading + len("By what Court triable"):]
    rows: list[Classification] = []
    start, section, sub = 0, "", ""
    for m in _BAIL.finditer(text):
        segment = text[start:m.start()]
        cogs = list(_COG.finditer(segment))
        court = _COURT.match(text, m.end())
        start = court.end() if court else m.end()
        if not cogs:
            continue  # the explanatory notes before the first row
        cog = " ".join(segment[cogs[-1].start():].split()).rstrip(". ")
        entry = " ".join(segment[:cogs[-1].start()].split())
        head = _SECTION.match(entry)
        if head:
            section, sub, entry = head["sec"], head["sub"], head["rest"]
        elif not section:
            continue
        offence, punishment = _split_offence(entry)
        court_name = " ".join(court["court"].split()) if court else ""
        court_name = re.sub(r"(?i)magistrate f the firstclass|magistrate of the firstclass",
                            "Magistrate of the first class", court_name)
        rows.append(Classification(section, sub, offence, punishment, cog, " ".join(m["bail"].split()),
                                   court_name))
    return rows


class Schedule:
    """Classification by BNS section number."""

    def __init__(self, rows: list[Classification]) -> None:
        self.rows = rows
        self.by_section: dict[str, list[Classification]] = {}
        for r in rows:
            self.by_section.setdefault(r.section, []).append(r)

    @classmethod
    def from_engine(cls, engine) -> Schedule:
        parts = engine.provision("BNSS SCHEDULE")
        return cls(parse(" ".join(p.get("text") or "" for p in parts)))

    def classify(self, section: str) -> list[Classification]:
        """All rows for a BNS section ("103" -> its (1) and (2) rows)."""
        return self.by_section.get(re.sub(r"\(.*$", "", section).upper(), [])
