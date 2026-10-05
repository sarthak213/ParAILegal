"""The v2 search engine behind the interface the API routes already use.

The routes call `search(query, k, domain)`, read `queryRouter`, `answerer` and `settings`, and
`index_stats()` for /ready; RAGSystem (v1: Qdrant, Cohere, Groq, Sarvam) offers the same.
Everything here runs locally. Until the local answer model is wired in, `answerer` is None
and answers are written by code from the gated evidence (app/answer/pipeline.py).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from app.answer.pipeline import Prepared, prepare
from app.core.config import Settings


class SearchService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine = None
        self.queryRouter = None
        self.answerer = None

    async def initialize(self) -> bool:
        from app.search.engine import SearchEngine

        print("\nInitializing v2 search (local corpus, BM25 + dense + reranker)...")
        self.engine = await asyncio.to_thread(
            SearchEngine.from_corpus, self.settings,
            dense=self.settings.DENSE_MODEL or None, rerank=self.settings.RERANK_MODEL or None,
        )
        self.queryRouter = self.engine.router
        print(f"v2 search ready: {len(self.engine.chunks)} chunks.")
        return True

    async def aclose(self) -> None:
        pass

    def search(self, query: str, k: int | None = None, domain: str | None = None) -> list[dict]:
        """Synchronous; routes call it through asyncio.to_thread."""
        hits = self.engine.search(query, k=k or self.settings.TOP_K_SEARCH, domain=domain)
        for h in hits:
            h["_rrf_score"] = h["_score"]
        return hits

    def prepare(self, query: str) -> Prepared:
        """Search, gate and evidence for an answer (app/answer/pipeline.py). Synchronous."""
        return prepare(query, self.engine, self.settings.TOP_K_SEARCH)

    async def answer(self, query: str, domain: str | None = None) -> dict[str, Any]:
        p = await asyncio.to_thread(self.prepare, query)
        return {
            "query": query,
            "domain": domain or self.queryRouter.route(query),
            "sources": p.sources,
            "answer": p.fallback,
            "gate": {"outcome": p.outcome, "reason": p.reason, "unknown": p.unknown},
        }

    @property
    def modelLoader(self):  # noqa: N802 — the admin route reads rag.modelLoader.models_loaded
        return self

    @property
    def models_loaded(self) -> bool:
        return self.engine is not None

    def index_stats(self) -> dict[str, int]:
        return dict(Counter(c["_domain"] for c in self.engine.chunks))
