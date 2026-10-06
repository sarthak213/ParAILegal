"""Citator: which Supreme Court judgment cites which, and how (followed, distinguished, doubted,
overruled), built from the judgments themselves. There is no open Indian citator, so this one is
made from the text, and every treatment it records keeps the sentence it was read from.

Resolving a citation to a judgment in the dataset (scripts/build_judgments.py records):
  SCR   "[1978] 2 SCR 353"  by year, volume and page range (the dataset's paths encode them:
        1978_2_353_357), so a pinpoint page inside a judgment resolves too
  INSC  "2023 INSC 600"     the neutral citation, directly
  SCC / AIR                  the dataset does not record these. Two ways in:
        - parallel citations judgments print side by side, "(1978) 1 SCC 248 : [1978] 2 SCR 621",
          learned across the whole corpus
        - the case name printed with it, against the titles of judgments decided that year or the
          year before (SCC volumes appear after the decision)

Direction of "overruled": a sentence such as "In Indore Development Authority (2020) 8 SCC 129,
the Constitution Bench overruled Pune Municipal Corporation (2014) 3 SCC 183" names both. Only a
citation that the words point at is marked:
  "Y is/was/stands overruled", "Y ... held per incuriam"     -> Y
  "overruling Y", "overruled the decision in Y"              -> Y
  "X overruled Y", "overruled by X", "overruled in X"        -> not X
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from rapidfuzz import fuzz

# ── Resolving citations ───────────────────────────────────────────────

_SCR = re.compile(r"\[(\d{4})\]\s*(Supp\.?\s*)?(\d{0,2})\s*SCR\s*(\d+)", re.IGNORECASE)
_PATH = re.compile(r"^(S_)?(\d{4})_(\d+)_(\d+)_(\d+)$")
_PARTY_NOISE = re.compile(r"\b(?:and|&)\s+(?:others|ors|anr|another)\b\.?|\bors\b\.?|\banr\b\.?|\betc\b\.?|"
                          r"\bm/s\.?|\bthe\b|\bshri\b|\bsmt\b\.?|\bdr\b\.?|[.,()&@'’]", re.IGNORECASE)


def norm_name(name: str) -> str:
    name = re.sub(r"\s+(?:versus|vs\.?|v\.?)\s+", " v ", name, flags=re.IGNORECASE)
    name = re.sub(r"(?<=\w)\.(?=\w)", "", name)  # "U.P." -> "UP", not "U P"
    return " ".join(_PARTY_NOISE.sub(" ", name.lower()).split())


@dataclass
class Judgment:
    id: str
    title: str
    year: int          # year of the report volume (SCR) / decision
    decided: str = ""
    insc: str = ""


@dataclass
class Index:
    """The dataset's judgments, found by citation or by name."""
    by_id: dict[str, Judgment] = field(default_factory=dict)
    scr: dict[tuple, list[tuple[int, int, str]]] = field(default_factory=lambda: defaultdict(list))
    insc: dict[str, str] = field(default_factory=dict)
    by_year: dict[int, list[tuple[str, str]]] = field(default_factory=lambda: defaultdict(list))  # (norm name, id)
    parallel: dict[str, str] = field(default_factory=dict)  # SCC/AIR key -> id, learned

    def add(self, path: str, title: str, decided: str = "", insc: str = "") -> None:
        if not path or path in self.by_id:
            return
        m = _PATH.match(path)
        year = int(m.group(2)) if m else int(decided[:4] or 0)
        self.by_id[path] = Judgment(path, title, year, decided, insc)
        if m:
            supp, y, vol, first, last = bool(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))
            self.scr[(y, supp, vol)].append((first, last, path))
        if insc:
            self.insc[" ".join(insc.replace("INSC", " INSC ").split())] = path
        decided_year = int(decided[:4]) if decided[:4].isdigit() else year
        self.by_year[decided_year].append((norm_name(title), path))

    def by_scr(self, citation: str) -> str | None:
        m = _SCR.search(citation)
        if not m:
            return None
        y, supp, vol, page = int(m.group(1)), bool(m.group(2)), int(m.group(3) or 1), int(m.group(4))
        for first, last, path in self.scr.get((y, supp, vol), []):
            if first <= page <= last:
                return path
        return None

    def by_name(self, name: str, year: int) -> str | None:
        """A judgment decided in `year` or the year before whose title matches `name`."""
        target = norm_name(name)
        if " v " not in target or len(target) < 8:
            return None
        best, second, best_id = 0.0, 0.0, None
        for y in (year, year - 1):
            for title, path in self.by_year.get(y, []):
                score = fuzz.token_set_ratio(target, title)
                if score > best:
                    best, second, best_id = score, best, path
                elif score > second:
                    second = score
        # a clear, unambiguous match only
        return best_id if best >= 90 and best - second >= 3 else None

    def resolve(self, citation: str, kind: str, name: str = "") -> tuple[str | None, str]:
        """(judgment id or None, how it was found)."""
        if kind == "scr":
            return self.by_scr(citation), "scr"
        if kind == "insc":
            return self.insc.get(citation), "insc"
        if citation in self.parallel:
            return self.parallel[citation], "parallel"
        year = re.search(r"(\d{4})", citation)
        if name and year:
            found = self.by_name(name, int(year.group(1)))
            if found:
                return found, "name"
        return None, ""


_PAIR = re.compile(
    r"(?P<a>\(\d{4}\)\s*\d{1,2}\s*SCC\s*\d+|AIR\s*\d{4}\s*SC\s*\d+)\s*[:=;,]\s*"
    r"(?P<b>\[\d{4}\]\s*(?:Supp\.?\s*)?\d{0,2}\s*S\.?\s?C\.?\s?R\.?\s*\d+)"
    r"|(?P<c>\[\d{4}\]\s*(?:Supp\.?\s*)?\d{0,2}\s*S\.?\s?C\.?\s?R\.?\s*\d+)\s*[:=;,]\s*"
    r"(?P<d>\(\d{4}\)\s*\d{1,2}\s*SCC\s*\d+|AIR\s*\d{4}\s*SC\s*\d+)", re.IGNORECASE)


def _key(raw: str) -> str:
    """The citation key scripts/build_judgments.py uses ("(1978) 1 SCC 248", "AIR 1978 SC 597")."""
    s = " ".join(raw.replace(".", "").split())
    if m := re.match(r"\((\d{4})\)\s*(\d+)\s*SCC\s*(\d+)", s, re.IGNORECASE):
        return f"({m.group(1)}) {m.group(2)} SCC {m.group(3)}"
    if m := re.match(r"AIR\s*(\d{4})\s*SC\s*(\d+)", s, re.IGNORECASE):
        return f"AIR {m.group(1)} SC {m.group(2)}"
    return s


def learn_parallels(index: Index, texts: list[str]) -> int:
    """SCC/AIR citations printed beside an SCR one; returns how many were learned."""
    learned = 0
    for text in texts:
        for m in _PAIR.finditer(text):
            other, scr = (m["a"], m["b"]) if m["a"] else (m["d"], m["c"])
            target = index.by_scr(scr.replace(".", "").replace("S C R", "SCR"))
            if target and _key(other) not in index.parallel:
                index.parallel[_key(other)] = target
                learned += 1
    return learned


# ── Treatment, with direction ─────────────────────────────────────────

_BE = r"(?:is|was|were|are|has\s+been|have\s+been|had\s+been|stands?|being|been|stood)"
_ADV = r"(?:\s+(?:hereby|thus|therefore|accordingly|rightly|expressly|impliedly|consequently|also|since|later|subsequently))*"
_AFTER = {  # read right after the citation: the case cited is the one treated
    "overruled": re.compile(rf"^[^\[\]()]{{0,90}}?\b{_BE}{_ADV}\s+overruled\b(?!\s+(?:on|to\s+the\s+extent))", re.IGNORECASE),
    "per incuriam": re.compile(rf"^[^\[\]()]{{0,90}}?\b{_BE}{_ADV}\s+(?:held\s+(?:to\s+be\s+)?|declared\s+)?(?:per\s+incuriam|not\s+(?:good|correct)\s+law|no\s+longer\s+good\s+law)", re.IGNORECASE),
    "doubted": re.compile(rf"^[^\[\]()]{{0,90}}?\b{_BE}{_ADV}\s+(?:doubted|referred\s+to\s+a\s+larger\s+bench)", re.IGNORECASE),
}
_BEFORE = {  # read right before the citation: words pointing at it
    "overruled": re.compile(r"\b(?:overrul(?:ing|ed)|overrule)\s+(?:the\s+)?(?:(?:earlier\s+)?(?:decision|judgment|view|ratio|dictum|law\s+laid\s+down)s?\s+)?(?:of\s+this\s+Court\s+)?(?:(?:rendered\s+)?in\s+)?(?:the\s+case\s+of\s+)?[^\[\]()]{0,80}$", re.IGNORECASE),
    "per incuriam": re.compile(r"\b(?:per\s+incuriam|(?:held|declared)\s+per\s+incuriam)\s+(?:the\s+)?(?:decision\s+)?(?:in\s+)?[^\[\]()]{0,80}$", re.IGNORECASE),
}
# "overruled by X" / "overruled in X" / "overruled by this Court in subsequent judgment in X":
# X did the overruling (a case name can be long; the window stops at a sentence end)
_OVERRULER = re.compile(r"\boverruled\s+(?:by|in)\b[^;\[\]()]{0,160}$", re.IGNORECASE)  # "K.S." has full stops
# The newer Reports' case-law table: "[2011] 9 SCR 382 overruled Para 5 [1971] 3 SCR 590 relied on Para 17";
# each label follows its own citation and ends with the paragraph it is cited in
_TABLE_LABEL = r"(referred\s+to|relied\s+(?:up)?on|followed|not\s+followed|overruled|partly\s+overruled|held\s+per\s+incuriam|" \
               r"per\s+incuriam|distinguished|approved|explained|considered|doubted|affirmed|reversed|cited|applied)"
_TABLE_AFTER = re.compile(rf"^\s*{_TABLE_LABEL}\s+Paras?\s+\d+", re.IGNORECASE)
_TABLE_BEFORE = re.compile(rf"\b{_TABLE_LABEL}\s+Paras?\s+\d+[\d,\s&and]*$", re.IGNORECASE)
# The Supreme Court Reports' case-law list: groups of citations, each ending with its treatment,
# "A (2009) 1 SCC 267 : [2008] 13 SCR 638; B (1971) 1 SCC 545 : [1971] 3 SCR 590 – referred to."
_LIST_LABEL = re.compile(r"\s[–—-]\s*(?P<label>referred\s+to|relied\s+(?:up)?on|followed|not\s+followed|overruled|"
                         r"partly\s+overruled|held\s+per\s+incuriam|per\s+incuriam|distinguished|approved|explained|"
                         r"considered|doubted|affirmed|reversed|set\s+aside|cited|applied)\s*\.", re.IGNORECASE)
_LIST_TREATMENT = {"followed": "followed", "relied on": "followed", "relied upon": "followed", "approved": "followed",
                   "applied": "followed", "affirmed": "followed", "overruled": "overruled",
                   "partly overruled": "overruled", "held per incuriam": "per incuriam", "per incuriam": "per incuriam",
                   "distinguished": "distinguished", "doubted": "doubted", "not followed": "distinguished"}
_FOLLOWED = re.compile(r"\bfollow(?:ed|ing)\b|\brelied\s+(?:up)?on\b|\breliance\s+(?:is|was|has\s+been)\s+placed\b|\breiterat|\bapprov(?:ed|ing)\b", re.IGNORECASE)
_DISTINGUISHED = re.compile(r"\bdistinguish", re.IGNORECASE)


def treatment(text: str, start: int, end: int, other_spans: list[tuple[int, int]]) -> tuple[str | None, str]:
    """(treatment of the citation at text[start:end], the words it was read from).

    other_spans: the other citations in the same text; a window stops at the nearest one, so a
    treatment written about one case is not given to its neighbour."""
    after_stop = min([s for s, _ in other_spans if s >= end] + [len(text), end + 160])
    before_start = max([e for _, e in other_spans if e <= start] + [0, start - 160])
    after, before = text[end:after_stop], text[before_start:start]
    if m := _TABLE_AFTER.match(after):  # a case-law table entry: its label is its treatment
        label = " ".join(m.group(1).lower().split())
        return _LIST_TREATMENT.get(label), text[start:end + m.end()]
    if _TABLE_BEFORE.search(before):
        before = ""  # the label before it closes the previous entry
    for kind, pat in _AFTER.items():
        if m := pat.search(after):
            return kind, text[start:end + m.end()]
    if not _OVERRULER.search(before):
        for kind, pat in _BEFORE.items():
            if m := pat.search(before):
                return kind, text[before_start + m.start():end]
    window = text[max(before_start, start - 120): min(after_stop, end + 60)]
    if _DISTINGUISHED.search(window):
        return "distinguished", window
    if _FOLLOWED.search(window):
        return "followed", window
    return None, ""


_ANY_CITATION = re.compile(
    r"\(\d{4}\)\s*\d{1,2}\s*SCC\s*\d+|AIR\s*\d{4}\s*SC\s*\d+|\[\d{4}\]\s*(?:Supp\.?\s*)?\d{0,2}\s*S\.?\s?C\.?\s?R\.?\s*\d+|\d{4}\s*INSC\s*\d+",
    re.IGNORECASE)


def treatments_in(text: str) -> list[tuple[str, int, int, str | None, str]]:
    """Every citation in a paragraph with its treatment: (citation text, start, end, treatment, evidence).

    A citation inside a case-law list group takes the group's label ("– overruled.", "– referred to."),
    since the editors wrote it for exactly those cases; other citations are read from the prose."""
    spans = [(m.start(), m.end()) for m in _ANY_CITATION.finditer(text)]
    groups, start = [], 0
    for m in _LIST_LABEL.finditer(text):
        label = " ".join(m["label"].lower().split())
        groups.append((start, m.start(), _LIST_TREATMENT.get(label), text[m.start():m.end()].strip(" –—-")))
        start = m.end()
    out = []
    for i, (s, e) in enumerate(spans):
        group = next((g for g in groups if g[0] <= s and e <= g[1]), None)
        if group:
            kind, evidence = group[2], group[3]
        else:
            kind, evidence = treatment(text, s, e, spans[:i] + spans[i + 1:])
        out.append((text[s:e], s, e, kind, evidence))
    return out
