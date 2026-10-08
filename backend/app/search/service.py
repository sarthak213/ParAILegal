"""The v2 search engine behind the interface the API routes already use.

The routes call `search(query, k, domain)`, read `queryRouter`, `answerer` and `settings`, and
`index_stats()` for /ready; RAGSystem (v1: Qdrant, Cohere, Groq, Sarvam) offers the same.
Everything here runs locally: search, then the gate and evidence (app/answer/pipeline.py),
then the local answer model (app/answer/local.py). Without a model file, `answerer` is None and
answers are written by code from the evidence.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from app.answer.followup import Previous
from app.answer.gate import SOURCES_ONLY
from app.answer.local import LocalAnswerer, finish
from app.answer.pipeline import Prepared, prepare
from app.answer.verify import citations, verify
from app.case.schedule import Schedule
from app.judgments.law_at_time import History
from app.judgments.store import JudgmentStore
from app.llm.server import LlamaServer
from app.core.config import Settings


class SearchService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine = None
        self.queryRouter = None
        self.answerer = None
        self.case_server: LlamaServer | None = None  # the Case Builder's model, when it differs
        self.judgments: JudgmentStore | None = None
        self.history: History | None = None

    async def initialize(self) -> bool:
        from app.search.engine import SearchEngine

        print("\nInitializing v2 search (local corpus, BM25 + dense + reranker)...")
        self.engine = await asyncio.to_thread(
            SearchEngine.from_corpus, self.settings,
            dense=self.settings.DENSE_MODEL or None, rerank=self.settings.RERANK_MODEL or None,
        )
        self.queryRouter = self.engine.router
        # the BNSS First Schedule, for the Case Builder's procedure (read from the corpus)
        self.schedule = Schedule.from_engine(self.engine)
        print(f"v2 search ready: {len(self.engine.chunks)} chunks.")
        # Supreme Court judgments and the amendment history, for judgments search and the Case
        # Builder's precedents; both optional data (scripts/build_judgments_index.py, build_corpus.py)
        self.judgments = await asyncio.to_thread(JudgmentStore.open, dense_key=self.settings.DENSE_MODEL or None)
        self.history = await asyncio.to_thread(History)
        if self.judgments:
            print(f"Judgments: {self.judgments.size} Supreme Court judgments.")

        s = self.settings
        server = LlamaServer(s.LLM_ENGINE_DIR, s.LLM_MODEL_PATH, s.LLM_THREADS, s.LLM_BATCH_THREADS,
                             s.LLM_CTX, s.LLM_IDLE_UNLOAD_S, s.LLM_DEVICE)
        if server.installed:
            self.answerer = LocalAnswerer(server, s.TEMPERATURE_ANSWER)
            print(f"Answer model: {server.model.name} (loads on the first answer)")
            if s.LLM_PRELOAD:
                asyncio.create_task(self._preload())
        else:
            print(f"No answer model at {server.model}: answers list the matching provisions.")
        case = LlamaServer(s.LLM_ENGINE_DIR, s.CASE_MODEL_PATH, s.LLM_THREADS, s.LLM_BATCH_THREADS,
                           s.LLM_CTX, s.LLM_IDLE_UNLOAD_S, s.LLM_DEVICE)
        if case.installed and case.model != server.model:
            self.case_server = case
            print(f"Case Builder model: {case.model.name}")
        return True

    async def case_model(self) -> LlamaServer:
        """The Case Builder's model, running; the answer model is stopped first (one at a time).
        Raises RuntimeError when no model is installed or it cannot start."""
        server = self.case_server or (self.answerer.server if self.answerer else None)
        if server is None:
            raise RuntimeError("No answer model is installed; the Case Builder needs one for this step")
        if server is self.case_server and self.answerer is not None:
            self.answerer.server.stop()
        await server.ensure_running()
        server.touch()
        return server

    def free_for_answers(self) -> None:
        """Stop the Case Builder's model before an answer loads the answer model (one at a time)."""
        if self.case_server is not None:
            self.case_server.stop()

    async def _preload(self) -> None:
        try:
            await self.answerer.server.ensure_running()
        except RuntimeError as e:
            print(f"Answer model failed to preload: {e}")

    def model_status(self) -> dict:
        if self.answerer is None:
            return {"state": "not_installed"}
        status = self.answerer.server.status()
        if self.case_server is not None:
            status["case_model"] = self.case_server.status()
        return status

    async def aclose(self) -> None:
        if self.answerer:
            await self.answerer.aclose()
        if self.case_server is not None:
            self.case_server.stop()

    def search(self, query: str, k: int | None = None, domain: str | None = None) -> list[dict]:
        """Synchronous; routes call it through asyncio.to_thread."""
        hits = self.engine.search(query, k=k or self.settings.TOP_K_SEARCH, domain=domain)
        for h in hits:
            h["_rrf_score"] = h["_score"]
        return hits

    def prepare(self, query: str, mode: str | None = None, previous: Previous | None = None) -> Prepared:
        """Search, gate and evidence for an answer (app/answer/pipeline.py). Synchronous."""
        return prepare(query, self.engine, self.settings.TOP_K_SEARCH, mode, previous)

    async def answer(self, query: str, domain: str | None = None, mode: str | None = None,
                     previous: Previous | None = None) -> dict[str, Any]:
        p = await asyncio.to_thread(self.prepare, query, mode, previous)
        verification = None
        if p.outcome == SOURCES_ONLY or self.answerer is None:
            answer = p.fallback
        else:
            self.free_for_answers()
            text, v = verify("".join([t async for t in self.answerer.stream(p)]), p.evidence, p.question)
            answer, verification = finish(text), v.to_dict()
        return {
            "query": query,
            "domain": domain or self.queryRouter.route(query),
            "sources": p.sources,
            "answer": answer,
            "gate": {"outcome": p.outcome, "reason": p.reason, "unknown": p.unknown, "follow_up": p.earlier},
            "citations": citations(p.evidence),
            "verification": verification,
        }

    @property
    def modelLoader(self):  # noqa: N802 — the admin route reads rag.modelLoader.models_loaded
        return self

    @property
    def models_loaded(self) -> bool:
        return self.engine is not None

    def index_stats(self) -> dict[str, int]:
        return dict(Counter(c["_domain"] for c in self.engine.chunks))
