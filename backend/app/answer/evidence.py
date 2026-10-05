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
from dataclasses import dataclass
from typing import Protocol

from app.answer.sources_only import heading, status_note

PROVISION_TOKENS = 600
BUDGET_TOKENS = 2500
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
    relation: str = ""    # "Replaces Section 302 of the Indian Penal Code [2]"


def tokens(text: str) -> int:
    return math.ceil(len(text.split()) / WORDS_PER_TOKEN)


def _words(text: str, n: int) -> str:
    w = text.split()
    return " ".join(w[:n]) + (" …" if len(w) > n else "")


def provision_text(parts: list[dict], matched_id: str | None) -> tuple[str, bool]:
    """The provision's text, and whether it had to be shortened."""
    texts = [p.get("text") or "" for p in parts]
    full = " ".join(texts)
    if tokens(full) <= PROVISION_TOKENS:
        return full, False
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


def assemble(hits: list[dict], source: ProvisionSource) -> list[Evidence]:
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
        cost = tokens(text) + 30  # + header and status line
        if out and used + cost > BUDGET_TOKENS:
            continue  # a shorter provision further down may still fit
        used += cost
        out.append(Evidence(n=len(out) + 1, ref=ref, header=heading(hit),
                            status=status_note(hit) or "In force", text=text, hit=hit,
                            trimmed=trimmed, linked=linked))
    # mark each old ↔ new pair, including pairs that search found on its own (BNS 103 ranked
    # above IPC 302 for "section 302 IPC"), so the model can say which replaced which
    by_ref = {e.ref: e for e in out}
    for old in out:
        for link, official in links_of(old.hit):
            new = by_ref.get(link)
            if new and not new.relation:
                verb = "Corresponds to" if official else "Probably corresponds to (matched by wording, not an official table)"
                new.relation = f"{verb} {old.header} [{old.n}]"
                old.relation = old.relation or f"Now {new.header} [{new.n}]"
    return out


def render(evidence: list[Evidence]) -> str:
    """The numbered sources as the model reads them."""
    blocks = []
    for e in evidence:
        lines = [f"[{e.n}] {e.header}", f"Status: {e.status}"]
        if e.relation:
            lines.append(e.relation)
        if e.trimmed:
            lines.append("(Shortened: only the opening and the relevant part are shown.)")
        lines.append(e.text)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
