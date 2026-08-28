"""
app/services/retrieval/retriever.py
────────────────────────────────────
Retriever owns the search pipeline:
    route → rewrite → embed → search indices → RRF fuse → return top-k

Change from original
─────────────────────
Added query_embed_fn alongside embed_fn.

Cohere uses asymmetric embedding — documents are embedded with
input_type="search_document" and queries with input_type="search_query".
Using the wrong type degrades retrieval quality.

    embed_fn       → used by IndexManager for corpus ingestion (documents)
    query_embed_fn → used by Retriever at search time (queries)

When using BGE-M3 locally both were the same function. With Cohere
they must be different. query_embed_fn defaults to embed_fn if not
provided, so old callers continue to work unchanged.
"""

import asyncio
import numpy as np
from typing import List, Dict, Optional, Callable

from app.core.config import Settings
from app.services.routing_service import QueryRouter
from app.services.retrieval.fusion import rrf_fuse, search_index


class Retriever:
    """
    Stateless search pipeline. All state lives in the injected dependencies.

    Constructor args:
        settings:         App settings (TOP_K_SEARCH, etc.)
        query_router:     Domain classifier
        query_rewriter:   Legal vocabulary enricher (async rewrite_query())
        embed_fn:         Sync (texts, label) -> np.ndarray  [document embedding]
        query_embed_fn:   Sync (texts, label) -> np.ndarray  [query embedding]
                          Defaults to embed_fn if not provided.
        constitution_index, statutes_index, judgements_index: QdrantIndex instances
        loop:             Running asyncio event loop, captured at startup.
    """

    def __init__(
        self,
        settings: Settings,
        query_router: QueryRouter,
        query_rewriter,
        embed_fn: Callable[[List[str], str], np.ndarray],
        constitution_index,
        statutes_index,
        judgements_index,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        query_embed_fn: Optional[Callable[[List[str], str], np.ndarray]] = None,
    ):
        self.settings           = settings
        self.query_router       = query_router
        self.query_rewriter     = query_rewriter
        self.embed_fn           = embed_fn
        self.query_embed_fn     = query_embed_fn or embed_fn
        self.constitution_index = constitution_index
        self.statutes_index     = statutes_index
        self.judgements_index   = judgements_index
        self._loop              = loop

    # ── Public API ────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        k: Optional[int] = None,
        domain: Optional[str] = None,
    ) -> List[Dict]:
        """
        Full retrieval pipeline. Runs synchronously — call via asyncio.to_thread.

        Steps:
            1. Route query to domain
            2. Rewrite query with legal vocabulary (via Groq)
            3. Embed original + rewritten query (via Cohere search_query type)
            4. Search relevant Qdrant indices with both embeddings
            5. RRF-fuse all result lists
            6. Assign ranks and return top-k
        """
        k = k or self.settings.TOP_K_SEARCH

        # Step 1: Route
        detected_domain = domain or self.query_router.route(query)
        print(f"  Domain: {detected_domain}")

        # Step 2: Rewrite
        rewritten = self._rewrite_sync(query, detected_domain)

        # Step 3: Embed using query-specific embed function
        # Cohere needs input_type="search_query" for queries
        queries_to_embed = [query, rewritten] if rewritten != query else [query]
        embs          = self._embed_query(queries_to_embed)
        emb_original  = embs[0:1]
        emb_rewritten = embs[1:2] if len(embs) > 1 else emb_original

        # Step 4: Search relevant indices
        indices = self._indices_for_domain(detected_domain)
        result_lists: List[List[Dict]] = []

        for index in indices:
            res = search_index(index, emb_original, k)
            if res:
                result_lists.append(res)
            if rewritten != query:
                res_rew = search_index(index, emb_rewritten, k)
                if res_rew:
                    result_lists.append(res_rew)

        if not result_lists:
            print("  WARNING: no results from any index.")
            return []

        # Step 5: RRF fusion
        fused   = rrf_fuse(result_lists) if len(result_lists) > 1 else result_lists[0]
        results = fused[:k]

        # Debug log
        print("  Top sources (Qdrant + RRF):")
        for r in results[:5]:
            cit   = (r.get("citation") or r.get("hierarchy") or "?")
            line1 = cit.splitlines()[0][:65]
            print(f"    {r.get('_score', 0):>6.4f}  {line1}")

        # Step 6: Assign ranks
        for i, r in enumerate(results):
            r["_rank"] = i + 1

        return results

    # ── Private helpers ───────────────────────────────────────────────

    def _rewrite_sync(self, query: str, domain: str) -> str:
        """
        Run async rewrite_query() from a synchronous thread.
        search() runs inside asyncio.to_thread — we can't await directly.
        run_coroutine_threadsafe schedules on the loop and blocks this thread.
        """
        if self._loop and self._loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self.query_rewriter.rewrite_query(query, domain=domain),
                    self._loop,
                )
                return future.result(timeout=15)
            except Exception as e:
                print(f"  QueryRewriter failed, using original: {e}")
                return query
        return query

    def _embed_query(self, texts: List[str]) -> np.ndarray:
        """
        Embed query texts using the query-specific embed function.
        For Cohere this uses input_type='search_query'.
        """
        return self.query_embed_fn(texts, "").astype("float32")

    def _embed(self, texts: List[str]) -> np.ndarray:
        """Document embedding — used by IndexManager, kept for compatibility."""
        return self.embed_fn(texts, "").astype("float32")

    def _indices_for_domain(self, domain: str) -> list:
        indices = []
        if domain in ("constitution", "all"):
            indices.append(self.constitution_index)
        if domain in ("statutes", "all"):
            indices.append(self.statutes_index)
        if domain in ("judgements", "all"):
            indices.append(self.judgements_index)
        return indices