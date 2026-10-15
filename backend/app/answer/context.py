"""What the answer model is told about each provision besides its text, written by code.

  relation  what it replaced or what replaced it, whether or not the other is among the sources:
            "Formerly Section 145 of the Code of Criminal Procedure, 1973", "Now Section 164 —
            ..., Bharatiya Nagarik Suraksha Sanhita, 2023". Most case law cites the old number.
  history   its latest amendments, from the footnotes (app/history/amendments.py): "Inserted by
            Act 46 of 1983, in force from 1983-12-25"
  cases     the Supreme Court judgments that deal most with it (or the provision it replaced),
            weighed by how often later judgments cite them, by name and year only: the model is not given their
            holdings, so it can point to them but not misstate them. Judgments noted as overruled
            or per incuriam are left out.

Each line is short: the evidence budget (app/answer/evidence.py) stays for the provisions' text.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.answer.sources_only import heading

MAX_HISTORY = 3
MAX_CASES = 2
MIN_MENTIONS = 3  # times a judgment names the provision (or the one it replaced) to count as applying it
MAX_MERGED = 30  # old section numbers listed for a new section that merged them (BNS 2: the IPC's definitions)
_SKIP_ACTIONS = {"commenced", "renumbered"}


@dataclass
class Context:
    """Optional sources of context: either may be None (not installed, or in tests)."""
    history: Any = None     # app.judgments.law_at_time.History
    judgments: Any = None   # app.judgments.store.JudgmentStore


def linked(hit: dict) -> list[tuple[str, bool]]:
    """(ref, stated) for the provisions linked to this one as old <-> new, stated links first."""
    out = [(r, True) for r in hit.get("corresponds_to") or []]
    out += [(d["ref"], False) for d in sorted(hit.get("derived_links") or [], key=lambda d: -d.get("score", 0))]
    return out


def relation(hit: dict, source: Any, shown: dict[str, int], also: list[tuple[str, bool]] = ()) -> str:
    """'Formerly Section 145 of the CrPC [3]' for a provision in force; 'Now Section 164 of the
    BNSS' for a repealed one; '' when it has no old <-> new link. shown: refs among the sources -> n;
    also: links seen from the other side (a source that names this one)."""
    repealed = (hit.get("status") or "").lower() == "repealed"
    # the other side's provisions, by Act and by whether the link is stated or only probable
    groups: dict[tuple[str, bool], list[tuple[str, dict]]] = {}
    for ref, stated in dict.fromkeys([*linked(hit), *also]):
        other = source.provision(ref)
        if not other or ((other[0].get("status") or "").lower() == "repealed") == repealed:
            continue  # a link between two old or two new provisions is not a replacement
        act = other[0].get("act_title") or other[0].get("document_title") or ref.rpartition(" ")[0]
        groups.setdefault((act, stated), []).append((ref, other[0]))
    parts = []
    for (act, stated), items in groups.items():
        verb = ("Now" if repealed else "Formerly") if stated else ("Probably now" if repealed else "Probably formerly")
        if len(items) == 1:  # one provision: its heading, and its number among the sources
            ref, first = items[0]
            text = heading(first) + (f" [{shown[ref]}]" if ref in shown else "")
        else:  # merged ("Formerly Sections 499, 500, 501 and 502 of the IPC"): the numbers only
            numbers = [ref.rpartition(" ")[2] for ref, _ in items]
            listed = numbers[:MAX_MERGED]
            more = f" and {len(numbers) - len(listed)} more" if len(numbers) > len(listed) else ""
            text = f"Sections {', '.join(listed[:-1])} and {listed[-1]}{more} of {act}" if not more else \
                   f"Sections {', '.join(listed)}{more} of {act}"
        parts.append(f"{verb} {text}" + ("" if stated else " (matched by wording, not an official table)"))
    return "; ".join(parts[:3])


def history(ref: str, context: Context) -> str:
    """'History: substituted by Act 5 of 2009, in force from 2010-11-01; inserted by ...'."""
    if context.history is None:
        return ""
    changes = [a for a in context.history.of(ref) if a.action not in _SKIP_ACTIONS]
    if not changes:
        return ""
    changes.sort(key=lambda a: a.effective or (str(a.year) if a.year else ""), reverse=True)
    items = []
    for a in changes[:MAX_HISTORY]:
        when = f", in force from {a.effective}" if a.effective else (f" ({a.year})" if a.year else "")
        by = f" by {a.by}" if a.by else ""
        items.append(f"{a.action}{by}{when}")
    more = f"; and {len(changes) - MAX_HISTORY} earlier change(s)" if len(changes) > MAX_HISTORY else ""
    return "History: " + "; ".join(items) + more + "."


def cases(ref: str, hit: dict, context: Context) -> str:
    """'Applied in: K.M. NANAVATI v. STATE OF MAHARASHTRA (1961), cited in 120 later judgments; ...'."""
    store = context.judgments
    if store is None:
        return ""
    refs = [ref, *(r for r, stated in linked(hit) if stated)]
    marks = ",".join("?" * len(refs))
    rows = store.db.execute(f"SELECT j.id, j.title, j.decided, SUM(c.count) FROM cites c JOIN judgments j "
                            f"ON j.rowid = c.rowid WHERE c.ref IN ({marks}) GROUP BY j.id", refs).fetchall()
    ranked = []
    for jid, title, decided, mentions in rows:
        status = store.status.get(jid, {})
        if mentions < MIN_MENTIONS or status.get("overruled_noted_in") or status.get("per_incuriam_noted_in"):
            continue
        cited_by = len(status.get("cited_by", []))
        # how much it deals with the provision, and how much later judgments rely on it: by later
        # citations alone, a famous judgment that names the section once in passing came first
        # (the privacy judgment for a confession section)
        ranked.append((mentions * math.log(2 + cited_by), cited_by, title, (decided or "")[:4]))
    ranked = sorted(ranked, reverse=True)[:MAX_CASES]
    if not ranked:
        return ""
    return "Leading Supreme Court judgments applying it (names only; their holdings are not given here): " + \
        "; ".join(f"{title} ({year})" + (f", cited in {n} later judgments" if n else "") for _, n, title, year in ranked) + "."
