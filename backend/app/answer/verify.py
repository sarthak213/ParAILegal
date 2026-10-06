"""Verifier: checks a generated answer against the evidence it was given.

The safeguard against invented law. It runs on the finished answer in about a millisecond:

  1. source numbers: every [n] must name one of the n sources given; others are removed
  2. provisions: every "Section 139", "Article 22", "Schedule VII" the answer names must appear
     in the sources (as a source itself, or mentioned in a source's text); others are flagged
  3. attribution: a provision named in a sentence must be in a source that sentence cites
     ("Section 142 ... [2]" where only source 1 mentions section 142 is flagged)
  4. numbers: every figure of two or more digits (years, amounts, periods) must appear in the
     sources; others are flagged
  5. uncited sentences: a sentence that states law but cites no source is counted

It cannot catch a wrong statement that names no provision or figure (a presumption described in
words and pinned on the wrong source); the evidence cut and the prompt rules work on that.

It flags; it never silently rewrites what the model said (apart from removing [n] that point
nowhere), so the reader sees what failed. Text written by code (caveat, legal aid line,
disclaimer) is not checked.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from app.answer.evidence import Evidence
from app.answer.prompts import CAVEAT_LEAD, LEGAL_AID
from app.answer.sources_only import DISCLAIMER

_CITE = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")
# "Section 138", "Sections 41 and 41A", "s. 35(1)", "Article 21", "Articles 14, 19 and 21"
_KIND = r"(?P<kind>sections?|secs?\.|ss?\.|u/s|articles?|arts?\.|schedules?)"
_NUM = r"(?:\d+[A-Z]{0,2}|[IVXL]+)\b(?:\(\w+\))*"
_MENTION = re.compile(rf"\b{_KIND}\s*(?P<nums>{_NUM}(?:\s*(?:,|and|or|to|&)\s*{_NUM})*)", re.IGNORECASE)
_ONE_NUM = re.compile(r"\d+[A-Z]{0,2}\b|\b[IVXL]+\b")
_FIGURE = re.compile(r"(?<![\w\[(])\d{2,}(?:,\d{2,3})*(?:\.\d+)?(?![\w\])])")
_LEGAL = re.compile(r"\b(?:shall|punish\w*|liable|must|may|entitled|offence|imprisonment|fine|penalty|"
                    r"section|article|right|prohibit\w*|require\w*|empower\w*|bail|arrest\w*)\b|\d",
                    re.IGNORECASE)
CODE_WRITTEN = (CAVEAT_LEAD.strip(), LEGAL_AID.strip(), DISCLAIMER)

UNSUPPORTED_WARN = 2       # this many unsupported provisions or figures -> warning
UNCITED_WARN = 0.5         # this share of legal sentences without a source -> warning


@dataclass
class Verification:
    invalid_ids: list[int] = field(default_factory=list)
    unsupported_provisions: list[str] = field(default_factory=list)  # "Section 139"
    unsupported_figures: list[str] = field(default_factory=list)     # "2019", "10,000"
    misattributed: list[str] = field(default_factory=list)           # "Section 138 [4]"
    uncited: list[str] = field(default_factory=list)                 # legal sentences with no [n]
    claims: int = 0
    cited_sources: list[int] = field(default_factory=list)
    warning: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _kind(word: str) -> str:
    w = word.lower()
    if w.startswith("art"):
        return "article"
    if w.startswith("sch"):
        return "schedule"
    return "section"


def mentions(text: str) -> set[tuple[str, str]]:
    """{("section", "138"), ("article", "21")} named in a text; sub-clauses are dropped."""
    out = set()
    for m in _MENTION.finditer(text):
        kind = _kind(m.group("kind"))
        for n in _ONE_NUM.findall(re.sub(r"\(\w+\)", "", m.group("nums"))):
            out.add((kind, n.upper()))
    return out


_ORDINALS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth",
             "tenth", "eleventh", "twelfth"]
_ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
_ORDINAL_SCHEDULE = re.compile(rf"\b({'|'.join(_ORDINALS)})\s+schedule\b", re.IGNORECASE)


def _source_mentions(e: Evidence) -> set[tuple[str, str]]:
    text = f"{e.header}\n{e.relation}\n{e.text}"
    found = mentions(text)
    # "the Third Schedule" is how an Act names what an answer calls "Schedule III"
    for m in _ORDINAL_SCHEDULE.finditer(text):
        i = _ORDINALS.index(m.group(1).lower())
        found |= {("schedule", _ROMAN[i]), ("schedule", str(i + 1))}
    act, _, number = e.ref.rpartition(" ")
    if number:
        kind = "article" if act == "ART" else "section"
        if number.upper().startswith("SCHEDULE_"):
            kind, number = "schedule", number.split("_", 1)[1]
        found.add((kind, number.upper()))
    return found


def strip_code_written(answer: str) -> str:
    for part in CODE_WRITTEN:
        answer = answer.replace(part, "")
    return answer


def sentences(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        line = line.strip().lstrip("*-• ").strip()
        if not line or line.startswith("#"):
            continue
        out += [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z(])", line) if s.strip()]
    return out


def remove_invalid_ids(answer: str, n_sources: int) -> tuple[str, list[int]]:
    """Drop source numbers that point nowhere: "[7]" with 5 sources; "[2, 9]" -> "[2]"."""
    bad: list[int] = []

    def fix(m: re.Match) -> str:
        nums = [int(x) for x in re.split(r"\s*[,;]\s*", m.group(1))]
        good = [n for n in nums if 1 <= n <= n_sources]
        bad.extend(n for n in nums if n not in good)
        return f"[{', '.join(map(str, good))}]" if good else ""

    return _CITE.sub(fix, answer), bad


def verify(answer: str, evidence: list[Evidence], question: str = "") -> tuple[str, Verification]:
    """The answer with invalid source numbers removed, and what the checks found. Provisions
    named in the question are not counted as invented ("498A" in "498A ka case kya hota hai")."""
    fixed, bad = remove_invalid_ids(answer, len(evidence))
    v = Verification(invalid_ids=sorted(set(bad)))
    body = strip_code_written(fixed)

    by_n = {e.n: _source_mentions(e) for e in evidence}
    # a question may name a provision bare ("498A ka case"): any number in it counts as known
    asked = mentions(question) | {(kind, n.upper()) for n in re.findall(r"\b\d+[A-Z]{0,2}\b", question, re.IGNORECASE)
                                  for kind in ("section", "article")}
    known: set[tuple[str, str]] = set().union(*by_n.values()) | asked
    source_text = "".join(f"\n{e.header}\n{e.relation}\n{e.text}" for e in evidence)
    v.unsupported_provisions = sorted({f"{k.title()} {n}" for k, n in mentions(body) if (k, n) not in known})

    plain_source = source_text.replace(",", "")
    # provision numbers were checked above; the rest are years, amounts and periods
    loose = _MENTION.sub(" ", _CITE.sub(" ", body))
    figures = {f for f in _FIGURE.findall(loose) if f.replace(",", "") not in plain_source}
    v.unsupported_figures = sorted(figures)

    cited: set[int] = set()
    for s in sentences(body):
        ids = [int(x) for c in _CITE.findall(s) for x in re.split(r"\s*[,;]\s*", c)]
        cited.update(ids)
        if ids:  # a provision named here must be in a source this sentence cites
            in_cited = set().union(*(by_n.get(n, set()) for n in ids))
            v.misattributed += [f"{k.title()} {n} [{', '.join(map(str, ids))}]"
                                for k, n in sorted(mentions(s)) if (k, n) in known and (k, n) not in in_cited]
        if len(s.split()) >= 6 and _LEGAL.search(_CITE.sub("", s)):
            v.claims += 1
            if not ids:
                v.uncited.append(s)
    v.cited_sources = sorted(cited)

    problems = v.unsupported_provisions + v.unsupported_figures + v.misattributed
    if len(problems) >= UNSUPPORTED_WARN:
        v.warning = ("Some provisions or figures in this answer were not found in its sources ("
                     + ", ".join(problems[:5]) + "). Check them against the source text.")
    elif v.claims and len(v.uncited) / v.claims > UNCITED_WARN:
        v.warning = "Much of this answer is not tied to a source. Read the sources before relying on it."
    return fixed, v


def citations(evidence: list[Evidence]) -> list[dict]:
    """What each [n] refers to, for the citation chips."""
    return [{"n": e.n, "ref": e.ref, "label": e.header} for e in evidence]
