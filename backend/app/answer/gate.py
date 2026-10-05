"""Confidence gate: answer, answer with a caveat, or show only the sources.

Rules first, then two signals search already computed (so the gate costs nothing):
  _exact   the query named this provision ("Section 103 BNS"): always answered
  _ce      the cross-encoder's raw logit for a hit: how well its wording answers the question
  _cos     the best dense cosine for the query: low when nothing in the corpus is about it

Thresholds are fitted on the dev split by eval/calibrate_gate.py. Declining an answerable
question costs more than answering an unanswerable one with a caveat (the answer model is also
told to say when its sources do not answer), so the fit weights the first error 3:1.
The dense cosine is what separates off-topic questions: adding it cut the dev error cost from
7 to 4. Retriever agreement (BM25 and dense both ranking the top hit highly) added nothing over
the logit, so it is not used. A per-hit logit cut for the evidence lost far more
relevant provisions than it removed noise, so the evidence is the top few hits by rank.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ANSWER, CAVEAT, SOURCES_ONLY = "answer", "caveat", "sources_only"
EVIDENCE_HITS = 5  # top hits by rank given to the answer model: 92% of essential provisions

# Law of another country: the corpus is Indian law only. Skipped when the question also
# mentions India ("compare US and Indian law"), since the Indian half can still be answered.
_FOREIGN = re.compile(
    # bare "us" is left out: it is usually the pronoun ("can police arrest us")
    r"\b(?:u\.s\.(?:a\.)?|usa|united states|american?|u\.?k\.?|united kingdom|england|english law|"
    r"scotland|britain|canad(?:a|ian)|australian?|new zealand|singapore|malaysia|germany|german|"
    r"france|french|european union|eu law|gdpr|china|chinese|japan(?:ese)?|pakistan(?:i)?|"
    r"bangladesh(?:i)?|nepal(?:i)?|sri lanka|dubai|uae|california|new york|texas|florida|"
    r"first amendment|miranda|green card)(?!\w)", re.IGNORECASE)  # not \b: "u.s." ends in a dot
_INDIA = re.compile(r"\bindia(?:n)?\b|\bbharat", re.IGNORECASE)


@dataclass(frozen=True)
class Thresholds:
    # Fitted by eval/calibrate_gate.py on the dev split (2026-10-05). Test split: 11/15
    # no-answer questions declined, 2/124 answerable ones declined (both lay wording).
    low: float = -4.28    # decline when the best logit is below this ...
    cos_low: float = 0.66  # ... and the best dense cosine is below this
    high: float = 6.41    # best logit at or above: answer plainly; below: answer with a caveat


THRESHOLDS = Thresholds()


@dataclass
class Decision:
    outcome: str                                       # ANSWER / CAVEAT / SOURCES_ONLY
    evidence: list[dict] = field(default_factory=list)  # hits the answer may use, best first
    reason: str = ""
    unknown: list[str] = field(default_factory=list)    # named provisions the corpus lacks


def best_logit(hits: list[dict]) -> float | None:
    scores = [h["_ce"] for h in hits if not h.get("_exact") and h.get("_ce") is not None]
    return max(scores) if scores else None


def best_cos(hits: list[dict]) -> float | None:
    return next((h["_cos"] for h in hits if h.get("_cos") is not None), None)


def foreign(query: str) -> bool:
    return bool(_FOREIGN.search(query)) and not _INDIA.search(query)


def decide(query: str, hits: list[dict], unknown: list[str] | None = None,
           t: Thresholds = THRESHOLDS) -> Decision:
    unknown = list(unknown or [])
    evidence = hits[:EVIDENCE_HITS]

    if any(h.get("_exact") for h in hits):
        # the named provisions, plus only what clearly matches too: "Section 302 IPC" should not
        # come with whichever sections happened to fill the rest of the list
        named = [h for h in hits if h.get("_exact") or (h.get("_ce") is not None and h["_ce"] >= t.high)]
        return Decision(ANSWER, named[:EVIDENCE_HITS], "the question names the provision", unknown)
    if unknown:
        return Decision(SOURCES_ONLY, [], "the provision named in the question is not in the corpus", unknown)
    if foreign(query):
        return Decision(SOURCES_ONLY, [], "the question is about the law of another country")
    if not hits:
        return Decision(SOURCES_ONLY, [], "no provisions found")

    top, cos = best_logit(hits), best_cos(hits)
    if top is None:  # no reranker: nothing to judge relevance by, so answer carefully
        return Decision(CAVEAT, evidence, "relevance not scored")
    if top < t.low and (cos is None or cos < t.cos_low):
        return Decision(SOURCES_ONLY, [], "no provision clearly matches the question")
    if top >= t.high:
        return Decision(ANSWER, evidence, "a provision clearly matches")
    return Decision(CAVEAT, evidence, "the closest provisions may not cover the exact question")
