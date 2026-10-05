"""Query understanding: deterministic, about a millisecond, no model.

Turns "dhara 302 kya hai" or "u/s 438 CrPC" into:
  - citations   exact provisions named in the query (old codes mapped to the new ones)
  - keywords    terms for BM25, including glossary expansions and typo corrections
  - text        the cleaned query for the dense retriever
  - mode        ADVOCATE / SUMMARISE, stripped from the text
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from app.search.legal_data import ACT_ALIASES, GLOSSARY, OLD_CODES, SPLIT_TARGETS, STOPWORDS
from app.search.legal_data import IPC_TO_BNS as IPC_GUESS

_MODE_RE = re.compile(r"^\s*(ADVOCATE|SUMMARISE|SUMMARIZE)\s*:\s*", re.IGNORECASE)
_ACT = "|".join(f"(?:{p})" for p, _ in ACT_ALIASES)
_SECTION_WORD = r"(?:sections?|secs?\.?|s\.|u/s\.?|under\s+section|dhara|धारा|§)"
_NUM = r"(\d{1,3}[A-Za-z]{0,2})(?:\s*\(\s*\w+\s*\))*"

# "Section 302 IPC", "s. 438 of the CrPC", "u/s 498A", "dhara 302", "302 IPC", "IPC 302", "BNS 103"
_SECTION_FIRST = re.compile(rf"{_SECTION_WORD}\s*{_NUM}(?:\s*(?:,|of|of\s+the|under|under\s+the)?\s*({_ACT}))?",
                            re.IGNORECASE)
_NUMBER_ACT = re.compile(rf"(?<![\w(]){_NUM}\s+({_ACT})\b", re.IGNORECASE)
_ACT_NUMBER = re.compile(rf"\b({_ACT})\s+(?:section\s+|s\.\s*)?{_NUM}\b", re.IGNORECASE)
_ARTICLE = re.compile(r"(?:\bart(?:icle|cle|ical|ial|icl)s?\b|\barts?\.|anuchhed|अनुच्छेद)\s*(\d{1,3}[A-Za-z]{0,2})", re.IGNORECASE)
_SCHEDULE = re.compile(r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth)\s+schedule\b",
                       re.IGNORECASE)
_SCHEDULE_NUM = {w: i for i, w in enumerate(
    "first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth".split(), 1)}
_GLOSSARY_WORDS = frozenset(w for term in GLOSSARY for w in term.split())
_GLOSSARY_ASCII = frozenset(w for w in _GLOSSARY_WORDS if w.isascii())
_TOKEN = re.compile(r"[\wऀ-ॿ]+(?:'\w+)?", re.UNICODE)


@dataclass(frozen=True)
class Citation:
    """A provision named in the query: ("BNS", "103"), ("ART", "21"), ("ART", "SCHEDULE_7")."""

    act: str
    number: str
    via: str | None = None  # e.g. "IPC 302" when mapped from an old code
    certain: bool = True     # False when the act had to be guessed

    @property
    def ref(self) -> str:
        return f"{self.act} {self.number}"


@dataclass
class ParsedQuery:
    raw: str
    text: str
    mode: str | None
    citations: list[Citation] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    expansions: list[str] = field(default_factory=list)
    corrections: dict[str, str] = field(default_factory=dict)
    corrected: str = ""  # the text with typos fixed

    @property
    def english(self) -> str:
        """Corrected text plus glossary translations: what an English-only model should read."""
        return " ".join([self.corrected or self.text, *self.expansions]).strip()


# ── Normalisation ──────────────────────────────────────────────────────


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"§+\s*", "section ", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_mode(text: str) -> tuple[str | None, str]:
    m = _MODE_RE.match(text)
    if not m:
        return None, text
    mode = m.group(1).upper().replace("SUMMARIZE", "SUMMARISE")
    return mode, text[m.end():].strip()


# ── Citations ──────────────────────────────────────────────────────────


def _act_code(name: str | None) -> str | None:
    if not name:
        return None
    for pattern, code in ACT_ALIASES:
        if re.fullmatch(pattern, name.strip(), re.IGNORECASE):
            return code
    return None


def _resolve(act: str | None, number: str, explicit_section: bool) -> Citation | None:
    number = number.upper()
    if act in OLD_CODES:
        new_act, table = OLD_CODES[act]
        target = table.get(number)
        # the replacement first; the old section itself is added alongside it (see find_citations)
        return Citation(new_act, target, via=f"{act} {number}") if target else Citation(act, number)
    if act in ("BNS", "BNSS", "BSA"):
        return Citation(act, number)
    if explicit_section:
        # "dhara 302", "section 438" with no act: in everyday usage these are almost always
        # the old IPC numbers people know; keep the BNS reading as a less certain alternative.
        mapped = IPC_GUESS.get(number)
        if mapped:
            return Citation("BNS", mapped, via=f"IPC {number}", certain=False)
        return Citation("BNS", number, certain=False)
    return None



def find_citations(text: str) -> list[Citation]:
    found: list[Citation] = []
    spans: list[tuple[int, int]] = []

    def add(c: Citation | None, span: tuple[int, int]) -> None:
        if c and c not in found and not any(s <= span[0] < e for s, e in spans):
            found.append(c)
            spans.append(span)
            for extra in SPLIT_TARGETS.get(c.via or "", []):  # e.g. IPC 498A -> BNS 85 and BNS 86
                act, number = extra.split()
                found.append(Citation(act, number, via=c.via, certain=c.certain))
            if c.via:  # the old section too: lawyers compare the two ("IPC 302" -> BNS 103 + IPC 302)
                act, number = c.via.split()
                found.append(Citation(act, number, certain=c.certain))

    for m in _SECTION_FIRST.finditer(text):
        add(_resolve(_act_code(m.group(2)), m.group(1), explicit_section=True), m.span())
    for m in _ACT_NUMBER.finditer(text):
        add(_resolve(_act_code(m.group(1)), m.group(2), explicit_section=False), m.span())
    for m in _NUMBER_ACT.finditer(text):
        add(_resolve(_act_code(m.group(2)), m.group(1), explicit_section=False), m.span())
    for m in _ARTICLE.finditer(text):
        add(Citation("ART", m.group(1).upper()), m.span())
    for m in _SCHEDULE.finditer(text):
        add(Citation("ART", f"SCHEDULE_{_SCHEDULE_NUM[m.group(1).lower()]}"), m.span())
    if re.search(r"\bpreamble\b", text, re.IGNORECASE):
        found.append(Citation("ART", "PREAMBLE"))
    # An uncertain guess is dropped if the same query names that act's citation explicitly
    certain = {c.ref for c in found if c.certain}
    return [c for c in found if c.certain or c.ref not in certain]


# ── Keywords ───────────────────────────────────────────────────────────


def _glossary_hits(text: str) -> list[str]:
    low = text.lower()
    hits = []
    for term in sorted(GLOSSARY, key=len, reverse=True):  # longest first: "agrim zamanat" before "zamanat"
        pattern = rf"(?<![\wऀ-ॿ]){re.escape(term)}(?![\wऀ-ॿ])"
        if re.search(pattern, low):
            hits.append(term)
            low = re.sub(pattern, " ", low)
    return hits


class QueryParser:
    """Holds the corpus vocabulary used for typo correction."""

    def __init__(self, vocabulary: set[str] | dict[str, int] | None = None) -> None:
        """vocabulary: corpus words, ideally with their counts (word -> count)."""
        counts = vocabulary if isinstance(vocabulary, dict) else dict.fromkeys(vocabulary or (), 1)
        self.counts = {w: n for w, n in counts.items() if len(w) >= 4}
        self.vocabulary = set(self.counts)
        self._choices = sorted(self.vocabulary | _GLOSSARY_ASCII)

    def correct(self, token: str) -> str | None:
        """Nearest known word for a likely typo, or None if the token is fine or unknowable."""
        if (len(token) < 5 or not token.isascii() or token.isdigit() or token in self.vocabulary
                or token in STOPWORDS or token in _GLOSSARY_WORDS):
            return None
        if not self._choices:
            return None
        candidates = process.extract(token, self._choices, scorer=fuzz.ratio, score_cutoff=80, limit=10)
        if not candidates:
            return None

        # near-equal matches: prefer a legal-glossary word, then the commoner word ("dowery" is as
        # close to "dower" as to "dowry"; a large corpus has both)
        def rank(c: tuple) -> float:
            word, score = c[0], c[1]
            return score + (5 if word in _GLOSSARY_ASCII else 0) + 3 * math.log10(self.counts.get(word, 1))

        best = max(candidates, key=rank)[0]
        return best if best != token else None

    def parse(self, raw: str) -> ParsedQuery:
        text = normalise(raw)
        mode, text = strip_mode(text)
        citations = find_citations(text)

        corrections: dict[str, str] = {}
        tokens = []
        for tok in _TOKEN.findall(text.lower()):
            fixed = self.correct(tok)
            if fixed:
                corrections[tok] = fixed
            tokens.append(fixed or tok)
        corrected = " ".join(tokens)

        hits = _glossary_hits(corrected)
        expansions = [GLOSSARY[h] for h in hits]
        # A provision implied by a glossary term ("anti-defection" -> "tenth schedule") counts as
        # a citation too, but a less certain one than a provision the user named.
        for c in find_citations(" ".join(expansions)):
            implied = Citation(c.act, c.number, via=c.via, certain=False)
            if all(x.ref != implied.ref for x in citations):
                citations.append(implied)
        keywords = []
        for tok in _TOKEN.findall(corrected + " " + " ".join(expansions)):
            if (len(tok) > 1 and tok not in STOPWORDS and not tok.isdigit() and tok not in keywords
                    and tok.isascii()):
                keywords.append(tok)
        return ParsedQuery(raw=raw, text=text, mode=mode, citations=citations, keywords=keywords,
                           expansions=expansions, corrections=corrections, corrected=corrected)
