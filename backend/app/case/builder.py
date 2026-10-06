"""Case Builder, criminal (CB-1): from typed facts to offences, elements, procedure and a brief.

A fixed sequence, not a free-roaming agent; each step's output is shown and can be edited before
the next runs (docs/OFFLINE-RAG.md section 8):

  structure_facts   the model turns the narrative into parties, events, harm, property, documents
  offences          search over each sentence of the facts; definitions map to the sections that
                    punish them (BNS 101 -> 103); procedure for each from the BNSS First Schedule
  check_elements    the model marks each element of an offence (app/case/elements.py) shown, not
                    shown or unclear, quoting the fact that shows it; a quote that is not in the
                    facts is not accepted, and the element becomes unclear
  brief             the answer pipeline over the confirmed offences, with the facts as the question

Everything the model writes is checked or anchored: elements are hand-written with statute quotes,
procedure is read from the schedule, and the brief goes through the usual verifier.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from app.case.elements import OFFENCES, Offence
from app.case.schedule import Schedule
from app.llm.client import complete_json

# ── Facts ──────────────────────────────────────────────────────────────

FACTS_SCHEMA = {
    "type": "object",
    "properties": {
        "parties": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "role": {"type": "string"}}, "required": ["name", "role"]}},
        "events": {"type": "array", "items": {"type": "object", "properties": {
            "when": {"type": "string"}, "what": {"type": "string"}}, "required": ["when", "what"]}},
        "harm": {"type": "array", "items": {"type": "string"}},
        "property": {"type": "array", "items": {"type": "string"}},
        "documents": {"type": "array", "items": {"type": "string"}},
        "acts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["parties", "events", "harm", "property", "documents", "acts"],
}

FACTS_PROMPT = """Read these facts of a case and list, using only what the text says:
- parties: each person or organisation, with their role (for example "complainant", "accused", "witness", "husband of the complainant")
- events: what happened, in order, each with when it happened as stated in the text ("" if not stated)
- harm: injuries, deaths, threats or losses
- property: money, goods or documents involved, with amounts as written
- documents: papers, messages, recordings or reports mentioned
- acts: each separate thing an accused person did that may be wrong, one short sentence each, only things this text says happened. Name people by their role, not their name, and say why if the text says why. (The form only, from an unrelated case: "the cashier took money from the shop's till without the owner's consent".)
Do not add anything that is not in the text. Do not name any law.

Facts:
{facts}"""

# words an act may use that the facts need not contain: roles ("the husband"), joining words
_ROLE_WORDS = set("""accused complainant victim deceased husband wife mother father son daughter brother sister
    parents parent in-laws inlaws law relative relatives family person persons someone man woman boy girl child
    their there them they with without from into because when after before about which that this unless while
    also then were have been being said does did done made make""".split())


def grounded(act: str, facts: str, share: float = 0.6) -> bool:
    """Most of the act's content words are in the facts. A 4B model sometimes copies an example
    from its prompt into the acts ("threatened by message to kill") though the facts say nothing of it."""
    plain = facts.lower()
    stems = {w[:5] for w in re.findall(r"[a-z]{4,}", plain)}
    words = [w for w in re.findall(r"[a-z]{4,}", act.lower()) if w not in _ROLE_WORDS]
    if not words:
        return True
    return sum(w[:5] in stems for w in words) >= share * len(words)


async def structure_facts(facts: str, base_url: str) -> dict:
    """The structured facts; acts the facts do not bear out are moved to "acts_dropped", so the
    lawyer sees them and can put one back."""
    out = await complete_json(base_url, [{"role": "user", "content": FACTS_PROMPT.format(facts=facts)}],
                              FACTS_SCHEMA, max_tokens=900)
    acts = [a for a in out.get("acts", []) if a.strip()]
    out["acts"] = [a for a in acts if grounded(a, facts)]
    out["acts_dropped"] = [a for a in acts if not grounded(a, facts)]
    return out


# ── Offences ───────────────────────────────────────────────────────────

# a definition section points to the offence that punishes it: BNS 101 (murder defined) -> BNS 103
DEFINED_IN: dict[str, str] = {e.source: o.ref for o in OFFENCES.values() for e in o.elements if e.source}
MAX_QUERIES = 12
CANDIDATES = 8
PER_ACT = 2  # each act's own top offences are kept, so one theme cannot crowd out the rest
# a provision that punishes something (special Acts have no schedule row: "Penalty for demanding dowry")
_PUNISHES = re.compile(r"shall be punish|shall be punishable|punishable with", re.IGNORECASE)


def sentences(facts: str) -> list[str]:
    parts = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", facts) if len(s.split()) >= 4]
    return parts[:MAX_QUERIES]


def is_offence(ref: str, schedule: Schedule, engine: Any = None) -> bool:
    act, _, number = ref.rpartition(" ")
    if ref in OFFENCES or (act == "BNS" and bool(schedule.classify(number))):
        return True
    if act in ("BNS", "BNSS", "BSA", "ART", "CASE", "IPC", "CRPC", "IEA") or engine is None:
        return False  # BNS offences are all in the schedule; the rest are procedure, evidence or repealed
    return any(_PUNISHES.search(p.get("text") or "") for p in engine.provision(ref))


def offences(facts: str, engine: Any, schedule: Schedule, acts: list[str] | None = None,
             k: int = 10, include: list[str] | None = None) -> list[dict]:
    """Candidate offences for the facts, best first, each with its classification and whether
    a checklist of elements exists for it.

    acts: the structured facts' "acts" (one short sentence per thing done); searched one by one,
    they find far better offences than the narrative's sentences. Scored by search rank (the
    cross-encoder rates narrative text too low to separate offences).

    include: sections the lawyer added by number ("BNS 85"); listed first, whatever search found."""
    acts = [a for a in (acts or []) if a.strip()][:MAX_QUERIES]
    # the opening of the narrative too: it carries the relationships the acts may leave out
    queries = [*acts, facts[:600]] if acts else sentences(facts)
    score: dict[str, float] = defaultdict(float)
    hit_of: dict[str, dict] = {}
    kept: list[str] = []  # each act's own best offences, in order
    for query in queries:
        own = 0
        for h in engine.search(query, k=k):
            ref = DEFINED_IN.get(h["_ref"], h["_ref"])
            if not is_offence(ref, schedule, engine):
                continue
            score[ref] += 1 / (h["_rank"] + 1)
            hit_of.setdefault(ref, h)
            if own < PER_ACT:
                own += 1
                if ref not in kept:
                    kept.append(ref)
    ranked = sorted(score, key=lambda r: -score[r])
    chosen = sorted(dict.fromkeys(kept + ranked), key=lambda r: -score[r])[:max(CANDIDATES, len(kept))]
    added = [r for r in dict.fromkeys(include or []) if engine.provision(r)]
    out = []
    for ref in [*added, *(r for r in chosen if r not in added)]:
        act, _, number = ref.rpartition(" ")
        offence = OFFENCES.get(ref)
        parts = engine.provision(ref)
        title = (parts[0].get("citation") if parts else hit_of[ref].get("citation") or "").split("\n")[0]
        out.append({
            "ref": ref,
            "name": offence.name if offence else title.split("—", 1)[-1].strip().rstrip("."),
            "title": title,
            "act": (parts[0] if parts else hit_of[ref]).get("document_title"),
            "score": round(score.get(ref, 0.0), 3),
            "added": ref in added,
            "has_checklist": offence is not None,
            "classification": [c.as_dict() for c in schedule.classify(number)] if act == "BNS" else [],
        })
    return out


# ── Elements ───────────────────────────────────────────────────────────

SHOWN, NOT_SHOWN, UNCLEAR = "shown", "not_shown", "unclear"

ELEMENTS_SCHEMA = {
    "type": "object",
    "properties": {"elements": {"type": "array", "items": {"type": "object", "properties": {
        "n": {"type": "integer"},
        "status": {"type": "string", "enum": [SHOWN, NOT_SHOWN, UNCLEAR]},
        "fact": {"type": "string"},
    }, "required": ["n", "status", "fact"]}}},
    "required": ["elements"],
}

ELEMENTS_PROMPT = """You are checking whether the facts of a case show each element of the offence of {name}.

For each numbered element, answer:
- status: "shown" if the facts clearly show it, "not_shown" if the facts show it did not happen, "unclear" if the facts do not say
- fact: the exact words from the facts that show this particular element, copied word for word ("" if none). Quote the words about this element itself, not about other events.
Judge only from the facts. Do not assume anything the facts do not say.

Elements:
{elements}

Facts:
{facts}"""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip().lower()


async def check_elements(facts: str, offence: Offence, base_url: str) -> list[dict]:
    listed = "\n".join(f"{i}. {e.label}" for i, e in enumerate(offence.elements, 1))
    reply = await complete_json(
        base_url, [{"role": "user", "content": ELEMENTS_PROMPT.format(name=offence.name, elements=listed, facts=facts)}],
        ELEMENTS_SCHEMA, max_tokens=120 * len(offence.elements) + 100)
    marks = {m["n"]: m for m in reply.get("elements", []) if isinstance(m.get("n"), int)}
    plain = _norm(facts)
    out = []
    for i, e in enumerate(offence.elements, 1):
        m = marks.get(i, {})
        status, fact = m.get("status", UNCLEAR), (m.get("fact") or "").strip()
        checked = True
        if fact and _norm(fact).strip(" .\"'") not in plain:
            # the model "quoted" words that are not in the facts: do not accept the tick
            status, fact, checked = UNCLEAR, "", False
        if status == SHOWN and not fact:
            status = UNCLEAR  # a tick must point to the fact behind it
        out.append({"n": i, "label": e.label, "kind": e.kind, "group": e.group,
                    "law": {"quote": e.quote, "source": e.source or offence.ref},
                    "status": status, "fact": fact, "quote_checked": checked})
    return out


# ── Brief ──────────────────────────────────────────────────────────────

_MARK = {SHOWN: "shown", NOT_SHOWN: "NOT shown", UNCLEAR: "open"}


def checklist_text(checklists: dict[str, list[dict]]) -> str:
    """The confirmed checklists as plain lines for the brief prompt."""
    lines = []
    for ref, checks in checklists.items():
        name = OFFENCES[ref].name if ref in OFFENCES else ref
        lines.append(f"{name} ({ref}): {element_summary(checks)}")
        for c in checks:
            fact = f' (fact: "{c["fact"]}")' if c.get("fact") else ""
            lines.append(f"  - {c['label']}: {_MARK.get(c['status'], 'open')}{fact}")
    return "\n".join(lines) or "(no checklist)"


def procedure_table(refs: list[str], schedule: Schedule) -> str:
    """Markdown table of each BNS offence's classification, from the BNSS First Schedule; written
    by code and put after the model's text."""
    rows = []
    for ref in refs:
        act, _, number = ref.rpartition(" ")
        for c in schedule.classify(number) if act == "BNS" else []:
            rows.append(f"| BNS {c.section}{c.sub} | {c.offence.rstrip('.')} | {c.punishment.rstrip('.')} | "
                        f"{c.cognizable} | {c.bailable} | {c.court} |")
    if not rows:
        return ""
    return ("\n\n### Procedure (BNSS First Schedule)\n\n| Section | Offence | Punishment | Cognizable | Bailable | "
            "Triable by |\n|---|---|---|---|---|---|\n" + "\n".join(rows) +
            "\n\n*The Schedule's own note: its offence and punishment columns indicate the substance of each "
            "section; they are not its definition or its punishment.*")


def brief_evidence(refs: list[str], engine: Any) -> list:
    """The confirmed offences (and the replacements of any repealed ones) as numbered evidence."""
    from app.answer.evidence import assemble

    hits = []
    for ref in refs:
        parts = engine.provision(ref)
        if parts:
            hits.append({**{k: v for k, v in parts[0].items() if not k.startswith("_")}, "_ref": ref})
    return assemble(hits, engine)


def element_summary(checks: list[dict]) -> str:
    """'met', 'not met' or 'open': required elements and at least one of each alternative group."""
    required = [c for c in checks if c["kind"] == "element"]
    groups: dict[str, list[dict]] = defaultdict(list)
    for c in checks:
        if c["kind"] == "any_of":
            groups[c["group"]].append(c)
    states = [c["status"] for c in required]
    for alts in groups.values():
        s = {a["status"] for a in alts}
        states.append(SHOWN if SHOWN in s else UNCLEAR if UNCLEAR in s else NOT_SHOWN)
    if NOT_SHOWN in states:
        return "not met"
    return "met" if all(s == SHOWN for s in states) else "open"
