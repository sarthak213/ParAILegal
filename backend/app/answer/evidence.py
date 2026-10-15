"""Evidence assembly: what the answer model reads.

Search returns chunks (a clause, or one part of a long section). The model gets whole
provisions, numbered [1]..[n], each with a status line written by code:

  1. chunk -> provision: all parts of the section (or Article, or judgment) joined in order;
     a provision longer than PROVISION_TOKENS keeps its opening and the part that matched
  2. a repealed provision brings its replacement ("corresponds_to"; derived links are marked
     "probably corresponds"), so an IPC question is answered with the BNS too
  3. at most MAX_PROVISIONS and about BUDGET_TOKENS in all, best first
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Protocol

from app.answer import context as ctx
from app.answer.sources_only import heading, status_note

# The prompt is read at ~370 tokens/s on the Arc GPU and ~80 on the CPU, so the evidence size
# is what sets the wait before the answer starts
PROVISION_TOKENS = 400
BUDGET_TOKENS = 1800
MAX_PROVISIONS = 6
MAX_LINKS = 2          # replacements added per repealed provision
OPENING_WORDS = 60     # kept from the start of a trimmed provision
WORDS_PER_TOKEN = 0.75


class ProvisionSource(Protocol):
    def provision(self, ref: str) -> list[dict]: ...


@dataclass
class Evidence:
    n: int
    ref: str            # "BNS 103"
    header: str         # "Section 103 — Punishment for murder, The Bharatiya Nyaya Sanhita, 2023"
    status: str         # "in force", "Repealed; replaced by …", "Applies only in Goa"
    text: str
    hit: dict           # the chunk shown as the source card
    trimmed: bool = False
    linked: bool = False  # added as the replacement of another provision, not found by search
    relation: str = ""    # "Formerly Section 302 — Punishment for murder, The Indian Penal Code [2]"
    notes: list[str] = field(default_factory=list)  # its amendment history, the judgments applying it

    def context(self) -> str:
        """The lines written by code about the provision, as the model reads them."""
        return "\n".join([self.relation, *self.notes]).strip()


def tokens(text: str) -> int:
    return math.ceil(len(text.split()) / WORDS_PER_TOKEN)


def _words(text: str, n: int) -> str:
    w = text.split()
    return " ".join(w[:n]) + (" …" if len(w) > n else "")


# An "Illustrations." block runs to the next numbered sub-section, Explanation or the end
_ILLUSTRATIONS = re.compile(r"Illustrations?\s*[.:—-]?\s.*?(?=\(\d+[A-Z]?\)\s|Explanation\b|$)", re.DOTALL)
_PART = "\x00"  # marks part boundaries while illustrations are cut out


def drop_illustrations(texts: list[str]) -> list[str]:
    """The parts' texts without illustration blocks (examples, not law): in a long section they
    sit between the definition and the punishments, which a cut would otherwise lose."""
    joined = _PART.join(texts)
    cut = _ILLUSTRATIONS.sub(lambda m: "[Illustrations omitted.] " + _PART * m.group(0).count(_PART), joined)
    return cut.split(_PART)


def provision_text(parts: list[dict], matched_id: str | None) -> tuple[str, bool]:
    """The provision's text, and whether it had to be shortened: first by leaving out its
    illustrations, then by keeping its opening and the part that matched."""
    texts = [p.get("text") or "" for p in parts]
    full = " ".join(texts)
    if tokens(full) <= PROVISION_TOKENS:
        return full, False
    texts = drop_illustrations(texts)
    full = " ".join(t for t in texts if t.strip())
    if tokens(full) <= PROVISION_TOKENS:
        return full, True
    room = int(PROVISION_TOKENS * WORDS_PER_TOKEN)
    at = next((i for i, p in enumerate(parts) if p.get("chunk_id") == matched_id), 0)
    if at == 0:
        return _words(full, room), True
    opening = _words(texts[0], OPENING_WORDS)
    return opening + " […] " + _words(" ".join(texts[at:]), room - OPENING_WORDS), True


def _card(chunk: dict, ref: str) -> dict:
    hit = {k: v for k, v in chunk.items() if not k.startswith("_")}
    hit.update(_ref=ref, _linked=True)
    return hit


def links_of(hit: dict) -> list[tuple[str, bool]]:
    """(ref, official) for the provisions that replaced a repealed one."""
    if (hit.get("status") or "").lower() != "repealed":
        return []
    out = [(r, True) for r in hit.get("corresponds_to") or []]
    out += [(d["ref"], False) for d in sorted(hit.get("derived_links") or [], key=lambda d: -d.get("score", 0))]
    return out[:MAX_LINKS]


def assemble(hits: list[dict], source: ProvisionSource, context: ctx.Context | None = None) -> list[Evidence]:
    """context: the amendment history and the judgments, for each provision's notes (app/answer/context.py)."""
    context = context or ctx.Context()
    # the provisions to include, in order: each hit, then the replacements of a repealed one
    plan: list[tuple[str, dict, bool]] = []  # (ref, hit, added as a replacement)
    seen: set[str] = set()
    for h in hits:
        ref = h.get("_ref")
        if not ref or ref in seen:
            continue
        seen.add(ref)
        plan.append((ref, h, False))
        for link, _ in links_of(h):
            if link in seen:
                continue
            parts = source.provision(link)
            if parts:
                seen.add(link)
                plan.append((link, _card(parts[0], link), True))

    out: list[Evidence] = []
    used = 0
    for ref, hit, linked in plan:
        if len(out) == MAX_PROVISIONS:
            break
        parts = source.provision(ref) or [hit]
        text, trimmed = provision_text(parts, hit.get("chunk_id"))
        notes = [n for n in (ctx.history(ref, context), ctx.cases(ref, parts[0], context)) if n]
        cost = tokens(text) + 30 + sum(tokens(n) for n in notes) + 15  # + header, status and relation
        if out and used + cost > BUDGET_TOKENS:
            continue  # a shorter provision further down may still fit
        used += cost
        out.append(Evidence(n=len(out) + 1, ref=ref, header=heading(hit),
                            status=status_note(hit) or "In force", text=text, hit=hit,
                            trimmed=trimmed, linked=linked, notes=notes))
    # each old ↔ new link, whether or not the other provision is among the sources ("Formerly
    # Section 145 of the CrPC"): most case law cites the old number. A source among these is
    # named with its number, so the model can say which replaced which
    shown = {e.ref: e.n for e in out}
    first = {e.ref: (source.provision(e.ref) or [e.hit])[0] for e in out}
    seen_from: dict[str, list[tuple[str, bool]]] = {}
    for e in out:
        for ref, stated in ctx.linked(first[e.ref]):
            seen_from.setdefault(ref, []).append((e.ref, stated))
    for e in out:
        e.relation = ctx.relation(first[e.ref], source, shown, seen_from.get(e.ref, []))
    return out


def render(evidence: list[Evidence]) -> str:
    """The numbered sources as the model reads them."""
    blocks = []
    for e in evidence:
        lines = [f"[{e.n}] {e.header}", f"Status: {e.status}"]
        if e.context():
            lines.append(e.context())
        if e.trimmed:
            lines.append("(Shortened: only the opening and the relevant part are shown.)")
        lines.append(e.text)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
