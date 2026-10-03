"""Hybrid search: exact citation lookup + BM25 keywords + (optional) dense vectors, fused.

    engine = SearchEngine.from_corpus()
    results = engine.search("u/s 438 CrPC", k=10)

Fusion is weighted reciprocal rank: score(d) = Σ w_i / (RRF_K + rank_i(d)). The router's domain
is a soft boost, never a filter, so a routing mistake can no longer hide the right answer.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

from app.search.bm25 import BM25Index, FieldWeights
from app.search.legal_data import OLD_CODES
from app.search.query import ParsedQuery, QueryParser

RRF_K = 60

# A dense retriever plugs in here: (query text, limit) -> [(chunk index, score)] best first.
DenseSearch = Callable[[str, int], list[tuple[int, float]]]


@dataclass
class Weights:
    exact: float = 4.0          # a provision named in the query
    exact_uncertain: float = 2.0  # e.g. "dhara 302" read as IPC 302
    case_name: float = 3.0
    bm25: float = 1.0
    dense: float = 1.0
    domain_boost: float = 0.15  # soft prior for the routed domain
    fields: FieldWeights = field(default_factory=FieldWeights)


ACT_TITLES = {"BNS": "bharatiya nyaya sanhita", "BNSS": "bharatiya nagarik suraksha sanhita",
              "BSA": "bharatiya sakshya adhiniyam"}
_GENERIC_PARTY = re.compile(r"^(?:the\s+)?(?:state\s+of\s+\w+|union\s+of\s+india|.*municipal corporation.*)$",
                            re.IGNORECASE)


def ref_of(chunk: dict) -> str:
    """'BNS 103', 'ART 21', 'CASE maneka_gandhi'."""
    domain = chunk["_domain"]
    if domain == "constitution":
        return f"ART {chunk.get('section')}"
    if domain == "judgements":
        return "CASE " + re.sub(r"_(ratio|held)_\d+$", "", chunk.get("chunk_id", ""))
    return f"{chunk['_act']} {chunk.get('section')}"


def _old_aliases() -> dict[str, list[str]]:
    """New ref → old-code aliases, e.g. 'BNS 103' → ['ipc 302 indian penal code']."""
    names = {"IPC": "ipc indian penal code", "CRPC": "crpc code of criminal procedure",
             "IEA": "iea indian evidence act"}
    out: dict[str, list[str]] = defaultdict(list)
    for old, (new_act, table) in OLD_CODES.items():
        for old_num, new_num in table.items():
            out[f"{new_act} {new_num}"].append(f"{names[old]} {old_num.lower()} section {old_num.lower()}")
    return out


class SearchEngine:
    def __init__(self, chunks: list[dict], dense: DenseSearch | None = None,
                 router: Any = None, weights: Weights | None = None) -> None:
        self.chunks = chunks
        self.dense = dense
        self.router = router
        self.weights = weights or Weights()
        self.by_ref: dict[str, list[int]] = defaultdict(list)
        for i, c in enumerate(chunks):
            self.by_ref[ref_of(c)].append(i)
        self.case_keys = self._case_keys()
        aliases = _old_aliases()
        docs = [self._index_doc(c, aliases) for c in chunks]
        self.bm25 = BM25Index(docs)
        self.parser = QueryParser(self.bm25.vocabulary)

    # ── Index documents ────────────────────────────────────────────────

    @staticmethod
    def _index_doc(c: dict, aliases: dict[str, list[str]]) -> dict[str, str]:
        domain, ref = c["_domain"], ref_of(c)
        citation = (c.get("citation") or "").split("\n")[0]
        if domain == "constitution":
            # "Article 352 — Proclamation of Emergency" → the name after the dash
            title = citation.split("—", 1)[-1].strip() if "—" in citation else citation
            part = (c.get("citation") or "").split("\n")[1] if "\n" in (c.get("citation") or "") else ""
            heading = f"article {c.get('section')} constitution of india {part}"
        elif domain == "judgements":
            title = c.get("case_name") or citation
            heading = f"{c.get('case_citation') or ''} supreme court judgment {c.get('rhetorical_role') or ''}"
        else:
            act = c["_act"]
            title = c.get("section_title") or ""
            heading = f"section {c.get('section')} {act.lower()} {ACT_TITLES[act]} {c.get('chapter_title') or ''}"
        old = list(aliases.get(ref, []))
        if c.get("ipc_equivalent"):
            old.append(str(c["ipc_equivalent"]).lower())
        return {"title": title, "heading": heading, "body": c.get("text") or "", "aliases": " ".join(old)}

    def _case_keys(self) -> list[tuple[str, str]]:
        """(distinctive party name, ref) for each landmark case, for typo-tolerant name matching."""
        keys = []
        for ref, idxs in self.by_ref.items():
            if not ref.startswith("CASE "):
                continue
            name = self.chunks[idxs[0]].get("case_name") or ""
            for party in re.split(r"\s+v\.?\s+|\s+vs\.?\s+", name):
                party = re.sub(r"\(.*?\)", "", party).strip()
                if party and not _GENERIC_PARTY.match(party):
                    keys.append((party.lower(), ref))
        return keys

    # ── Search ─────────────────────────────────────────────────────────

    def parse(self, query: str) -> ParsedQuery:
        return self.parser.parse(query)

    def search(self, query: str, k: int = 10, domain: str | None = None) -> list[dict]:
        pq = self.parser.parse(query)
        w = self.weights
        ranked_lists: list[tuple[float, list[int]]] = []

        exact: list[int] = []
        uncertain: list[int] = []
        for c in pq.citations:
            (exact if c.certain else uncertain).extend(self.by_ref.get(c.ref, []))
        if exact:
            ranked_lists.append((w.exact, exact))
        if uncertain:
            ranked_lists.append((w.exact_uncertain, uncertain))

        low = pq.text.lower()
        cases = [ref for key, ref in self.case_keys if fuzz.partial_ratio(key, low) >= 88]
        if cases:
            ranked_lists.append((w.case_name, [i for ref in dict.fromkeys(cases) for i in self.by_ref[ref]]))

        bm = self.bm25.search(pq.keywords, limit=60, weights=w.fields)
        if bm:
            ranked_lists.append((w.bm25, [i for i, _ in bm]))

        if self.dense is not None:
            dn = self.dense(pq.text, 60)
            if dn:
                ranked_lists.append((w.dense, [i for i, _ in dn]))

        scores: dict[int, float] = defaultdict(float)
        for weight, idxs in ranked_lists:
            for rank, i in enumerate(idxs, start=1):
                scores[i] += weight / (RRF_K + rank)

        routed = domain or (self.router.route(pq.text) if self.router else "all")
        if routed and routed != "all" and w.domain_boost:
            for i in scores:
                if self.chunks[i]["_domain"] == routed:
                    scores[i] *= 1 + w.domain_boost

        best = sorted(scores, key=lambda i: -scores[i])[:k]
        out = []
        for rank, i in enumerate(best, start=1):
            hit = {key: v for key, v in self.chunks[i].items() if not key.startswith("_")}
            hit.update(_score=round(scores[i], 6), _rank=rank, _ref=ref_of(self.chunks[i]))
            out.append(hit)
        return out

    # ── Construction ───────────────────────────────────────────────────

    @classmethod
    def from_corpus(cls, settings: Any = None, dense: DenseSearch | None = None,
                    weights: Weights | None = None) -> SearchEngine:
        from app.core.config import settings as default_settings
        from app.infrastructure.loaders.dataset_loader import (
            ConstitutionLoader,
            JudgementLoader,
            StatuteLoader,
        )
        from app.services.routing_service import QueryRouter

        settings = settings or default_settings
        chunks: list[dict] = []
        for loader, domain in ((ConstitutionLoader, "constitution"), (StatuteLoader, "statutes"),
                               (JudgementLoader, "judgements")):
            _, metadata = loader(settings).load_dataset()
            for m in metadata:
                c = dict(m)
                c["_domain"] = domain
                c["source_type"] = domain
                if domain == "statutes":
                    c["_act"] = (c.get("chunk_id") or "").split("_")[0].upper()
                chunks.append(c)
        return cls(chunks, dense=dense, router=QueryRouter(settings), weights=weights)
