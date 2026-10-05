"""Hybrid search: exact citation lookup + BM25 keywords + (optional) dense vectors, fused.

    engine = SearchEngine.from_corpus()
    results = engine.search("u/s 438 CrPC", k=10)

Fusion is weighted reciprocal rank: score(d) = Σ w_i / (RRF_K + rank_i(d)). The router's domain
is a soft boost, never a filter, so a routing mistake can no longer hide the right answer.
"""

from __future__ import annotations

import math
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
    # Tuned on the dev split with eval/tune.py --dense bge-small (2026-10-05, 892-Act corpus).
    exact: float = 8.0            # a provision named in the query
    exact_uncertain: float = 4.0  # e.g. "dhara 302" read as IPC 302
    case_name: float = 1.5
    bm25: float = 0.5
    dense: float = 0.5
    domain_boost: float = 0.0     # soft prior for the routed domain (tuning switched it off)
    constitution_route: float = 0.5  # statutes scaled by 1/(1 + this) when a query is constitutional
    # Repealed or omitted text (the IPC, the replaced labour laws, omitted articles) is scaled by
    # this unless the query asks for the old law: current law answers first, and each old
    # section stays one link away from its replacement ("corresponds_to").
    superseded: float = 0.3
    # How much the rest of the statute book cites an Act (0-1, from scripts/build_corpus.py):
    # scores are scaled by 1 + authority * this, so the BNSS outranks the Madras District Police
    # Act of 1859 on "arrest without warrant". The Constitution and landmark cases count as 1.
    # Kept small: larger values bury the specialist Acts ("other_acts" questions).
    authority: float = 0.3
    # A local Act (the Madras District Police Act, 1859; the Delhi Rent Control Act) is scaled by
    # this unless the query names its territory: a general question wants the law of all India.
    regional: float = 0.5
    rerank_top: int = 20          # candidates the cross-encoder re-orders, when one is set
    rerank_prior: float = 20.0    # weight of log(prior) added to the cross-encoder's score (tuned on dev)
    fields: FieldWeights = field(default_factory=FieldWeights)


@dataclass
class Components:
    """Ranked chunk indices from each retriever for one query."""

    exact: list[int]
    uncertain: list[int]
    cases: list[int]
    bm25: list[int]
    dense: list[int]
    routed: str
    wants_old: bool = False  # the query cites or names a replaced law
    text: str = ""           # the query, lower-cased (does it name a local Act's territory?)
    dense_score: dict[int, float] = field(default_factory=dict)  # cosine of each dense hit


SUPERSEDED = ("repealed", "omitted")
# a query that asks about the old law: "under the IPC", "old CrPC", "the repealed Evidence Act"
_OLD_LAW = re.compile(r"\b(?:ipc|i\.p\.c|indian penal code|crpc|cr\.?p\.?c|code of criminal procedure|"
                      r"evidence act|iea|repealed|old (?:law|code|act|section)s?|previous(?:ly)?|"
                      r"before (?:the )?(?:bns|bnss|bsa|2024|july 2024))\b", re.IGNORECASE)

ACT_TITLES = {"BNS": "bharatiya nyaya sanhita", "BNSS": "bharatiya nagarik suraksha sanhita",
              "BSA": "bharatiya sakshya adhiniyam", "IPC": "indian penal code",
              "CRPC": "code of criminal procedure", "IEA": "indian evidence act"}
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
                 router: Any = None, weights: Weights | None = None, reranker: Any = None) -> None:
        self.chunks = chunks
        self.dense = dense
        self.reranker = reranker
        self.router = router
        self.weights = weights or Weights()
        self.by_ref: dict[str, list[int]] = defaultdict(list)
        for i, c in enumerate(chunks):
            self.by_ref[ref_of(c)].append(i)
        self.case_keys = self._case_keys()
        self._authority = [1.0 if c["_domain"] != "statutes" else float(c.get("authority") or 0.0)
                           for c in chunks]
        self._refs = [ref_of(c) for c in chunks]
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
            act_name = ACT_TITLES.get(act) or (c.get("document_title") or "").lower()
            heading = f"section {c.get('section')} {act.lower()} {act_name} {c.get('chapter_title') or ''}"
        old = list(aliases.get(ref, []))
        if c.get("ipc_equivalent"):
            old.append(str(c["ipc_equivalent"]).lower())
        # linked sections of the Act it replaced or that replaced it ("income_tax_act_1961 80C"
        # -> "income tax act 1961 section 80c"), so a search by the old number finds the new one
        for linked in [*(c.get("corresponds_to") or []), *(d["ref"] for d in c.get("derived_links") or [])]:
            act, _, number = linked.rpartition(" ")
            old.append(f"{act.replace('_', ' ').lower()} section {number.lower()}")
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

    def components(self, query: str, domain: str | None = None) -> Components:
        """Each retriever's ranked list for a query: the expensive part, independent of weights."""
        pq = self.parser.parse(query)
        exact: list[int] = []
        uncertain: list[int] = []
        for c in pq.citations:
            (exact if c.certain else uncertain).extend(self.by_ref.get(c.ref, []))
        low = pq.text.lower()
        case_refs = dict.fromkeys(ref for key, ref in self.case_keys if fuzz.partial_ratio(key, low) >= 88)
        cases = [i for ref in case_refs for i in self.by_ref[ref]]
        bm = [i for i, _ in self.bm25.search(pq.keywords, limit=60, weights=self.weights.fields)]
        dense_hits = self.dense(pq.text, 60) if self.dense is not None else []
        dn = [i for i, _ in dense_hits]
        routed = domain or (self.router.route(pq.text) if self.router else "all")
        wants_old = any(c.via or c.act in OLD_CODES for c in pq.citations) or bool(_OLD_LAW.search(pq.text))
        return Components(exact, uncertain, cases, bm, dn, routed, wants_old, pq.text.lower(),
                          {i: s for i, s in dense_hits})

    def prior(self, i: int, comp: Components, weights: Weights) -> float:
        """How much a provision's standing, apart from its wording, should count for this query:
        the product of the soft priors. Fusion multiplies by it; the reranker adds its log."""
        c = self.chunks[i]
        factor = 1.0
        if comp.routed and comp.routed != "all" and weights.domain_boost and c["_domain"] == comp.routed:
            factor *= 1 + weights.domain_boost
        if comp.routed == "constitution" and weights.constitution_route and c["_domain"] == "statutes":
            # a constitutional question: Acts named after an institution (the Finance Commission
            # Act, the Representation of the People Act) step back from the Articles; landmark
            # cases keep their place, since many such questions are answered by a judgment
            factor /= 1 + weights.constitution_route
        if weights.authority:
            factor *= 1 + weights.authority * self._authority[i]
        if i in comp.exact:  # a provision the user named is never held back
            return factor
        if weights.regional != 1.0 and c.get("scope") == "regional" \
                and not any(p in comp.text for p in c.get("places") or []):
            factor *= weights.regional
        if weights.superseded != 1.0 and not comp.wants_old and c.get("status") in SUPERSEDED:
            factor *= weights.superseded
        return factor

    def fuse(self, comp: Components, weights: Weights, k: int = 10) -> list[tuple[int, float]]:
        """Weighted reciprocal-rank fusion of the component lists, plus the soft domain boost."""
        scores: dict[int, float] = defaultdict(float)
        for weight, idxs in ((weights.exact, comp.exact), (weights.exact_uncertain, comp.uncertain),
                             (weights.case_name, comp.cases), (weights.bm25, comp.bm25),
                             (weights.dense, comp.dense)):
            if weight:
                for rank, i in enumerate(idxs, start=1):
                    scores[i] += weight / (RRF_K + rank)
        for i in scores:
            scores[i] *= self.prior(i, comp, weights)
        # one entry per provision: a long section split into parts would otherwise fill several
        # places ("CRPC 438, CRPC 438, CRPC 438") and push its BNSS equivalent out; its best part stands in
        out, seen = [], set()
        for i, s in sorted(scores.items(), key=lambda kv: -kv[1]):
            ref = self._refs[i]
            if ref in seen:
                continue
            seen.add(ref)
            out.append((i, s))
            if len(out) == k:
                break
        return out

    def rerank(self, query: str, comp: Components, fused: list[tuple[int, float]], k: int
               ) -> tuple[list[tuple[int, float]], dict[int, float]]:
        """Re-order fused candidates with the cross-encoder. Provisions the user named stay first.
        Also returns each re-ordered candidate's raw cross-encoder logit (before the priors):
        how well its wording answers the question, which the answer gate reads."""
        pinned = [(i, s) for i, s in fused if i in set(comp.exact)]
        rest = [(i, s) for i, s in fused if i not in set(comp.exact)]
        raw: dict[int, float] = {}
        if rest and self.reranker is not None:
            scores = self.reranker(query, [self.chunks[i]["_embed_text"] for i, _ in rest])
            raw = {i: sc for (i, _), sc in zip(rest, scores, strict=True)}
            # the cross-encoder judges wording only; add the priors (as a log, since its scores
            # are logits) so it does not undo them: a local or repealed Act stays held back
            w = self.weights.rerank_prior
            rest = sorted(((i, sc + w * math.log(self.prior(i, comp, self.weights)))
                           for (i, _), sc in zip(rest, scores, strict=True)), key=lambda kv: -kv[1])
        return (pinned + rest)[:k], raw

    def search(self, query: str, k: int = 10, domain: str | None = None) -> list[dict]:
        """Ranked hits. Besides the chunk's fields, each hit carries the signals the answer gate
        reads: `_exact` (the query named this provision), `_ce` (raw cross-encoder logit, None if
        not reranked), `_bm25_rank` / `_dense_rank` (1-based rank in each retriever, or None) and
        `_cos` (the best dense cosine among the query's dense hits: low for off-topic questions)."""
        comp = self.components(query, domain)
        raw: dict[int, float] = {}
        if self.reranker is not None:
            pool = self.fuse(comp, self.weights, max(k, self.weights.rerank_top))
            # the reranker is English-only: give it typo-corrected text plus glossary translations
            ranked, raw = self.rerank(self.parser.parse(query).english, comp, pool, k)
        else:
            ranked = self.fuse(comp, self.weights, k)
        bm25_rank = self._ref_ranks(comp.bm25)
        dense_rank = self._ref_ranks(comp.dense)
        exact = {self._refs[i] for i in comp.exact}
        cos = round(max(comp.dense_score.values()), 4) if comp.dense_score else None
        out = []
        for rank, (i, score) in enumerate(ranked, start=1):
            ref = self._refs[i]
            hit = {key: v for key, v in self.chunks[i].items() if not key.startswith("_")}
            hit.update(_score=round(score, 6), _rank=rank, _ref=ref, _exact=ref in exact,
                       _ce=round(raw[i], 4) if i in raw else None,
                       _bm25_rank=bm25_rank.get(ref), _dense_rank=dense_rank.get(ref), _cos=cos)
            out.append(hit)
        return out

    def _ref_ranks(self, idxs: list[int]) -> dict[str, int]:
        """Best 1-based rank of each provision in a retriever's list (its parts count as one)."""
        ranks: dict[str, int] = {}
        for rank, i in enumerate(idxs, start=1):
            ranks.setdefault(self._refs[i], rank)
        return ranks

    def unknown_citations(self, query: str) -> list[str]:
        """Provisions the query names with certainty that the corpus does not hold
        ("BNS 999", "ART 512"): the answer says so instead of guessing."""
        return [c.ref for c in self.parser.parse(query).citations if c.certain and c.ref not in self.by_ref]

    def provision(self, ref: str) -> list[dict]:
        """All chunks of one provision, in document order: the parts of a long section, an
        Article's clauses, a judgment's paragraphs."""
        return [self.chunks[i] for i in self.by_ref.get(ref, [])]

    # ── Construction ───────────────────────────────────────────────────

    @classmethod
    def from_corpus(cls, settings: Any = None, dense: DenseSearch | str | None = None,
                    weights: Weights | None = None, rerank: str | None = None) -> SearchEngine:
        """dense: a DenseSearch callable, or an embedder key from app.search.dense.SPECS."""
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
            texts, metadata = loader(settings).load_dataset()
            for text, m in zip(texts, metadata, strict=True):
                c = dict(m)
                c["_embed_text"] = text  # citation + body, as v1 embedded it
                c["_domain"] = domain
                c["source_type"] = domain
                if domain == "statutes":
                    c["_act"] = c.get("act_code") or (c.get("chunk_id") or "").split("_")[0].upper()
                chunks.append(c)
        if isinstance(dense, str):
            from app.search.dense import DenseIndex

            dense = DenseIndex(dense, [c["_embed_text"] for c in chunks])
        reranker = None
        if rerank:
            from app.search.rerank import Reranker

            reranker = Reranker(rerank)
        return cls(chunks, dense=dense, router=QueryRouter(settings), weights=weights, reranker=reranker)
