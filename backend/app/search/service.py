"""The v2 search engine behind the interface the API routes already use.

The routes call `search(query, k, domain)`, read `queryRouter`, `answerer` and `settings`, and
`index_stats()` for /ready; RAGSystem (v1: Qdrant, Cohere, Groq) offers the same. Everything
here runs locally except answer generation, which still calls the configured LLM API.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from app.core.config import Settings


class SearchService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine = None
        self.queryRouter = None
        self.answerer = None

    async def initialize(self) -> bool:
        from app.infrastructure.llm.answerer import QuestionAnswerer
        from app.search.engine import SearchEngine

        print("\nInitializing v2 search (local corpus, BM25 + dense + reranker)...")
        self.engine = await asyncio.to_thread(
            SearchEngine.from_corpus, self.settings,
            dense=self.settings.DENSE_MODEL or None, rerank=self.settings.RERANK_MODEL or None,
        )
        self.queryRouter = self.engine.router
        # the answerer needs only the API settings; v1's model loader and rewriter are not used
        self.answerer = QuestionAnswerer(self.settings, self.settings, None, None, None)
        print(f"v2 search ready: {len(self.engine.chunks)} chunks.")
        return True

    async def aclose(self) -> None:
        if self.answerer:
            await self.answerer.aclose()

    def search(self, query: str, k: int | None = None, domain: str | None = None) -> list[dict]:
        """Synchronous; routes call it through asyncio.to_thread."""
        hits = self.engine.search(query, k=k or self.settings.TOP_K_SEARCH, domain=domain)
        for h in hits:
            h["_rrf_score"] = h["_score"]
        return hits

    async def answer(self, query: str, domain: str | None = None) -> dict[str, Any]:
        results = await asyncio.to_thread(self.search, query, None, domain)
        top = results[:self.settings.TOP_K_ANSWER]
        return {
            "query": query,
            "domain": domain or self.queryRouter.route(query),
            "sources": top,
            "answer": await self.answerer.generate_answer(query, top),
        }

    @property
    def modelLoader(self):  # noqa: N802 — the admin route reads rag.modelLoader.models_loaded
        return self

    @property
    def models_loaded(self) -> bool:
        return self.engine is not None

    def index_stats(self) -> dict[str, int]:
        return dict(Counter(c["_domain"] for c in self.engine.chunks))
