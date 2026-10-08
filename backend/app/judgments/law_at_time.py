"""The law a judgment applied, as it stood when it was decided, and what has changed since.

For each provision a judgment cites (scripts/build_judgments.py: "provisions"), from the corpus
and the amendment history (data/amendments, app/history/amendments.py):

    text_then      the section as it read on the decision date (None: not yet enacted)
    complete       False when some later change could not be rolled back (its earlier wording
                   is not recorded in the footnotes); the note then says so
    amended_since  amendments that took effect after the decision
    today          its status now; for a repealed Act, the provisions that replaced it

`note` puts that in one sentence written by code, never by the model: a reader of a 2005 ruling on
IPC s.498A must know whether the law it applied still reads the same, and what replaced it.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.history.amendments import COMMENCED, RENUMBERED, Amendment, _after, text_as_on

AMENDMENTS_DIR = Path(__file__).resolve().parents[2] / "data" / "amendments"
NAMES = {"IPC": "IPC", "CRPC": "CrPC", "IEA": "Evidence Act", "BNS": "BNS", "BNSS": "BNSS", "BSA": "BSA",
         "ART": "Article", "code_of_civil_procedure_1908": "CPC", "CRPC_1898": "CrPC 1898"}


class History:
    """Amendment history of every Act, by (act code, section)."""

    def __init__(self, directory: Path = AMENDMENTS_DIR) -> None:
        self.by_section: dict[tuple[str, str], list[Amendment]] = defaultdict(list)
        if directory.exists():
            for f in directory.glob("*.jsonl"):
                if f.name.startswith("_"):
                    continue
                for line in f.read_text(encoding="utf-8").splitlines():
                    a = Amendment(**json.loads(line))
                    self.by_section[(a.act, a.section)].append(a)
            # earlier wordings read from archived copies of the Act (scripts/fill_old_wordings.py)
            filled = directory / "_filled.jsonl"
            if filled.exists():
                for line in filled.read_text(encoding="utf-8").splitlines():
                    f = json.loads(line)
                    for a in self.by_section.get((f["act"], f["section"]), []):
                        if a.by == f["by"] and a.note == f["note"] and a.old_text is None:
                            a.old_text, a.old_source = f["old_text"], f["source"]

    def of(self, ref: str) -> list[Amendment]:
        act, _, section = ref.rpartition(" ")
        return self.by_section.get((act, section), [])


@dataclass
class ProvisionThen:
    ref: str
    cited: int
    title: str = ""
    text_then: str | None = None
    complete: bool = True
    amended_since: list[dict] = field(default_factory=list)
    unknown: list[dict] = field(default_factory=list)
    status_today: str = ""
    now: list[str] = field(default_factory=list)  # current provisions, if replaced
    archived: str = ""  # "archived India Code copy saved 2007-08-21", when the old wording came from one
    note: str = ""


def _label(ref: str) -> str:
    act, _, n = ref.rpartition(" ")
    if act == "ART":
        return f"Article {n}"
    name = NAMES.get(act, act.replace("_", " ").title())
    return f"{name} s.{n}"


def law_at_time(judgment: dict, engine: Any, history: History, limit: int = 8) -> list[ProvisionThen]:
    decided = judgment.get("decided") or ""
    out = []
    for p in judgment.get("provisions", [])[:limit]:
        ref, parts = p["ref"], engine.provision(p["ref"])
        item = ProvisionThen(ref, p["count"])
        if not parts:
            item.note = f"{_label(ref)} is not in ParAILegal's corpus."
            out.append(item)
            continue
        first = parts[0]
        item.title = (first.get("section_title") or (first.get("citation") or "").split("—")[-1]).strip().rstrip(".")
        current = " ".join(x.get("text") or "" for x in parts)
        amendments = history.of(ref)
        if decided and amendments:
            then = text_as_on(current, amendments, decided)
            item.text_then, item.complete, item.unknown = then.text, then.complete, then.unknown
            item.archived = next((u["old_source"] for u in then.undone if u.get("old_source")), "")
        else:
            item.text_then = " ".join(current.split())
        item.amended_since = [{"action": a.action, "by": a.by, "effective": a.effective or (str(a.year) if a.year else None)}
                              for a in amendments
                              if decided and a.action not in (COMMENCED, RENUMBERED) and _after(a, decided)]
        status = (first.get("status") or "").lower()
        item.now = list(dict.fromkeys([*(first.get("corresponds_to") or []),
                                       *(d["ref"] for d in first.get("derived_links") or [])]))
        if status == "repealed":
            item.status_today = f"repealed; replaced by {first.get('replaced_by')}" if first.get("replaced_by") else "repealed"
        elif status == "omitted" or re.search(r"^\W*[^.—]{0,120}[.—]*\s*Omitted by\b", current[:200]):
            item.status_today = "omitted"  # an omitted article is kept as "Compulsory acquisition ...—Omitted by ..."
        else:
            item.status_today = "in force"
        item.note = _note(item, decided)
        out.append(item)
    return out


def _note(item: ProvisionThen, decided: str) -> str:
    label = _label(item.ref)
    if item.text_then is None:
        return f"{label} did not exist yet on {decided}; it was enacted later."
    if item.amended_since:
        changes = "; ".join(f"{a['action']} by {a['by']} from {a['effective']}" for a in item.amended_since[:3])
        more = f" and {len(item.amended_since) - 3} more" if len(item.amended_since) > 3 else ""
        since = f"Amended since the decision ({changes}{more})."
    else:
        since = "Not amended between the decision and today's text" + (
            " (or its repeal)." if item.status_today.startswith("repealed") else ".")
    gaps = (" The wording on the decision date could not be fully rebuilt: some earlier text is not recorded."
            if not item.complete else "")
    if item.archived:
        gaps += f" Earlier wording taken from an {item.archived}, not the Gazette."
    today = ""
    if item.status_today.startswith("repealed"):
        now = ", ".join(_label(r) for r in item.now) if item.now else ""
        today = f" Now {item.status_today}" + (f" ({now})." if now else ".")
    elif item.status_today == "omitted":
        today = " Now omitted."
    return f"Decided on {decided} under {label}. {since}{gaps}{today}"
