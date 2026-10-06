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

from app.answer import urgent
from app.answer.evidence import Evidence, assemble, render
from app.answer.followup import Previous, is_follow_up
from app.answer.gate import CAVEAT, EVIDENCE_HITS, NO_CLEAR_MATCH, SOURCES_ONLY, Decision, decide
from app.answer.prompts import RESEARCH, mode_of
from app.answer.sources_only import DEFAULT_LEAD, sources_only_answer
from app.search.query import strip_mode

NEAREST = 5  # provisions listed when the gate declines for lack of a clear match
FOLLOW_UP_REFS = 3  # provisions of the earlier answer kept in the running for a follow-up


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
    mode: str = RESEARCH
    question: str = ""  # the query without its mode prefix ("ADVOCATE: ...")
    read_as: str = ""   # the question in legal terms when that differs (typos fixed, glossary)
    earlier: str = ""   # the question this one follows up, if it is a follow-up
    notice: str = ""    # urgent-help notices to show above the answer (app/answer/urgent.py)


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


def prepare(query: str, engine: Any, k: int, mode: str | None = None,
            previous: Previous | None = None) -> Prepared:
    """mode: research / summarise / advocate; if None, read from a "SUMMARISE: ..." prefix.
    previous: the question answered just before, and the sources shown with it (app/answer/followup.py)."""
    prefix, question = strip_mode(query)
    mode = mode_of(mode or prefix)
    parsed = engine.parse(question)
    earlier = strip_mode(previous.question)[1].strip() if previous else ""
    if earlier and is_follow_up(question, bool(parsed.citations)):
        # search reads both questions, and the earlier answer's provisions stay in the running
        hits = engine.search(f"{earlier} {question}", k=k,
                             context_refs=engine.refs_of_chunks(previous.chunk_ids)[:FOLLOW_UP_REFS])
    else:
        earlier = ""
        hits = engine.search(query, k=k)
    unknown = engine.unknown_citations(query)
    read_as = parsed.english if parsed.english.lower() != question.lower() else ""
    notice = urgent.block(question)
    d = decide(question, hits, unknown)
    if notice and d.outcome == SOURCES_ONLY and d.reason == NO_CLEAR_MATCH and hits:
        # someone in trouble now, asking in their own words: answer from the nearest provisions
        # with the caveat rather than decline (lay wording scores low with the reranker)
        d = Decision(CAVEAT, hits[:EVIDENCE_HITS], "urgent: answering from the nearest provisions")
    common = dict(unknown=d.unknown, mode=mode, question=question, read_as=read_as, earlier=earlier, notice=notice)
    if d.outcome == SOURCES_ONLY:
        nearest = [] if (d.unknown or "another country" in d.reason or not hits) else hits[:NEAREST]
        return Prepared(query, d.outcome, d.reason, nearest,
                        fallback=notice + sources_only_answer(nearest, lead(d.outcome, d.reason, d.unknown)), **common)
    evidence = assemble(d.evidence, engine)
    sources = [e.hit for e in evidence]
    return Prepared(query, d.outcome, d.reason, sources, evidence, render(evidence),
                    fallback=notice + sources_only_answer(sources, lead(d.outcome, d.reason, d.unknown)), **common)
