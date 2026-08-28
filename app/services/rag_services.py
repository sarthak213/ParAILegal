"""
app/services/rag_services.py
─────────────────────────────
RAGSystem — thin orchestrator. Wires dependencies, delegates everything.

Changes from original
──────────────────────
1. IndexManager now uses QdrantIndex instead of FaissIndex — no local files,
   data persists in Qdrant Cloud automatically.

2. Two embed functions are now passed to Retriever:
     embed_fn       — document embedding (input_type="search_document")
                      used by IndexManager for ingestion
     query_embed_fn — query embedding (input_type="search_query")
                      used by Retriever at search time

   Cohere's asymmetric embedding requires different input_types for
   documents vs queries. Using the wrong type degrades retrieval quality.

3. FAISS import removed — no local index files, no pickle files.
"""

import asyncio
import numpy as np
from typing import List, Dict, Any, Optional

from app.core.config import Settings
from app.services.routing_service import QueryRouter
from app.services.retrieval.index_manager import IndexManager
from app.services.retrieval.retriever import Retriever


class RAGSystem:

    def __init__(self, settings: Settings):
        self.settings      = settings
        self.modelLoader   = None
        self.queryRewriter = None
        self.queryRouter   = None
        self.answerer      = None
        self.indexManager  = IndexManager(settings)
        self._retriever    = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── Initialization ────────────────────────────────────────────────

    async def initialize(self, force_rebuild: bool = False) -> bool:
        print("\nInitializing RAG System...")

        self._loop = asyncio.get_running_loop()

        # Load and verify cloud APIs
        from app.infrastructure.llm.model_loader import ModelLoader
        self.modelLoader = ModelLoader(self.settings)
        await self.modelLoader.load_models()

        # Construct stateless services
        from app.infrastructure.llm.query_rewriter import QueryRewriter
        from app.infrastructure.llm.answerer import QuestionAnswerer

        self.queryRewriter = QueryRewriter(self.settings, self.settings)
        self.queryRouter   = QueryRouter(self.settings)
        self.answerer      = QuestionAnswerer(
            self.settings, self.settings,
            self.modelLoader, None, self.queryRewriter,
        )

        # ── Document embed function (for ingestion) ───────────────────
        # Uses Cohere input_type="search_document"
        def embed_fn(texts: List[str], label: str) -> np.ndarray:
            if label:
                print(f"  Embedding {len(texts)} {label} chunks...")
            return self.modelLoader.generate_embeddings(
                texts,
                batch_size=self.settings.BATCH_SIZE,
                show_progress_bar=bool(label),
                input_type="search_document",
            ).astype("float32")

        # ── Query embed function (for search time) ────────────────────
        # Uses Cohere input_type="search_query" — critical for quality
        def query_embed_fn(texts: List[str], label: str) -> np.ndarray:
            return self.modelLoader.generate_embeddings(
                texts,
                batch_size=len(texts),
                show_progress_bar=False,
                input_type="search_query",
            ).astype("float32")

        # Build / load Qdrant indices
        await self.indexManager.initialize(embed_fn, force_rebuild)

        # Wire Retriever with both embed functions
        self._retriever = Retriever(
            settings           = self.settings,
            query_router       = self.queryRouter,
            query_rewriter     = self.queryRewriter,
            embed_fn           = embed_fn,
            query_embed_fn     = query_embed_fn,
            constitution_index = self.indexManager.constitution_index,
            statutes_index     = self.indexManager.statutes_index,
            judgements_index   = self.indexManager.judgements_index,
            loop               = self._loop,
        )

        print("\nRAG System ready.")
        return True

    async def aclose(self) -> None:
        """Drain connection pools on shutdown."""
        if self.modelLoader:
            await self.modelLoader.aclose()
        if self.queryRewriter:
            await self.queryRewriter.aclose()
        if self.answerer:
            await self.answerer.aclose()

    # ── Public API ────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        k: Optional[int] = None,
        domain: Optional[str] = None,
    ) -> List[Dict]:
        """Synchronous retrieval. Call via asyncio.to_thread from routes."""
        return self._retriever.search(query, k=k, domain=domain)

    async def answer(
        self,
        query: str,
        domain: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Full RAG pipeline: retrieve (in thread) → generate (async)."""
        results = await asyncio.to_thread(self.search, query, None, domain)
        top     = results[:self.settings.TOP_K_ANSWER]
        answer  = await self.answerer.generate_answer(query, top)

        return {
            "query":   query,
            "domain":  domain or self.queryRouter.route(query),
            "sources": top,
            "answer":  answer,
        }

    # ── Admin helpers ─────────────────────────────────────────────────

    def index_stats(self) -> Dict[str, int]:
        return self.indexManager.stats()

    async def rebuild_index(self, index: str) -> None:
        """Trigger a force-rebuild of one or all indices without restart."""
        def embed_fn(texts, label):
            return self.modelLoader.generate_embeddings(
                texts,
                batch_size=self.settings.BATCH_SIZE,
                show_progress_bar=True,
                input_type="search_document",
            ).astype("float32")

        if index in ("constitution", "all"):
            await asyncio.to_thread(self.indexManager.rebuild_constitution, embed_fn)
        if index in ("statutes", "all"):
            await asyncio.to_thread(self.indexManager.rebuild_statutes, embed_fn)
        if index in ("judgements", "all"):
            await asyncio.to_thread(self.indexManager.rebuild_judgements, embed_fn)

        self._retriever.constitution_index = self.indexManager.constitution_index
        self._retriever.statutes_index     = self.indexManager.statutes_index
        self._retriever.judgements_index   = self.indexManager.judgements_index

    # ── Compatibility ─────────────────────────────────────────────────

    def _detect_domain(self, query: str) -> str:
        return self.queryRouter.route(query)

    @property
    def models_loaded(self) -> bool:
        return self.modelLoader.models_loaded if self.modelLoader else False