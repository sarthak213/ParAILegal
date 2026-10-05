"""From a question to what the answer step needs: search, gate, evidence.

    prepared = prepare(query, engine, k)
    prepared.sources      the source cards to show (evidence first, in [n] order)
    prepared.context      the numbered evidence for the answer model ("" when declined)
    prepared.fallback     the answer written by code: used when declined or when no model is set up

The answer model plugs in after this (docs/OFFLINE-RAG.md, step 3); everything here is local
and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.answer.evidence import Evidence, assemble, render
from app.answer.gate import CAVEAT, SOURCES_ONLY, decide
from app.answer.sources_only import DEFAULT_LEAD, sources_only_answer

NEAREST = 5  # provisions listed when the gate declines for lack of a clear match


@dataclass
class Prepared:
    query: str
    outcome: str
    reason: str
    sources: list[dict]
    evidence: list[Evidence] = field(default_factory=list)
    context: str = ""
    fallback: str = ""
    unknown: list[str] = field(default_factory=list)


def readable_ref(ref: str) -> str:
    """'ART 512' -> 'Article 512'; 'BNS 999' -> 'Section 999 of the BNS'."""
    act, _, number = ref.partition(" ")
    if act == "ART":
        return f"Article {number}"
    # act codes are "BNS" for the big codes, "code_on_wages_2019" for the rest
    return f"Section {number} of the {act if act.isupper() else act.replace('_', ' ').title()}"


def lead(outcome: str, reason: str, unknown: list[str]) -> str:
    if unknown:
        names = ", ".join(readable_ref(r) for r in unknown)
        return f"**{names} is not in ParAILegal's corpus.** Check the number and the Act."
    if outcome == SOURCES_ONLY and "another country" in reason:
        return "**ParAILegal covers Indian law only**, so it cannot answer questions about other countries' law."
    if outcome == SOURCES_ONLY:
        return ("**No provision clearly answers this question.** The nearest provisions are below, "
                "in case one of them helps; try naming the Act or using legal terms.")
    if outcome == CAVEAT:
        return ("**The closest provisions found are below; they may not cover your exact situation.** "
                "No answer model is set up yet, so they are listed rather than explained.")
    return DEFAULT_LEAD


def prepare(query: str, engine: Any, k: int) -> Prepared:
    hits = engine.search(query, k=k)
    unknown = engine.unknown_citations(query)
    d = decide(query, hits, unknown)
    if d.outcome == SOURCES_ONLY:
        nearest = [] if (d.unknown or "another country" in d.reason or not hits) else hits[:NEAREST]
        return Prepared(query, d.outcome, d.reason, nearest, unknown=d.unknown,
                        fallback=sources_only_answer(nearest, lead(d.outcome, d.reason, d.unknown)))
    evidence = assemble(d.evidence, engine)
    sources = [e.hit for e in evidence]
    return Prepared(query, d.outcome, d.reason, sources, evidence, render(evidence),
                    fallback=sources_only_answer(sources, lead(d.outcome, d.reason, d.unknown)),
                    unknown=d.unknown)
