import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings


logger = logging.getLogger("parai-legal")


@asynccontextmanager
async def lifespan(app: FastAPI):

    logger.info("Starting ParAILegal API...")

    app.state.ready = False
    app.state.error = None
    app.state.rag_system = None

    try:
        if settings.SEARCH_ENGINE == "v2":
            # offline: local corpus, BM25 + dense + reranker; no API keys
            from app.search.service import SearchService

            rag = SearchService(settings)
            await rag.initialize()
        else:
            # v1: Qdrant + Cohere + Groq + Sarvam. Imported only here so v2 never loads
            # the cloud clients. initialize() checks the APIs, then builds or loads the
            # Qdrant indices in threads.
            from app.services.rag_services import RAGSystem

            rag = RAGSystem(settings)
            await rag.initialize(force_rebuild=False)

        app.state.rag_system = rag
        app.state.ready = True

        logger.info("RAG system initialized successfully")

    except Exception as e:
        logger.exception("Failed to initialize RAG system")
        # Don't re-raise: let the app start so /ready can report the error.
        # Routes protected by get_rag_system return 503 until ready=True.
        app.state.error = f"{type(e).__name__}: {e}"

    yield

    # ── Shutdown ──────────────────────────────────────────────────────
    logger.info("Shutting down ParAILegal API...")

    if app.state.rag_system is not None:
        await app.state.rag_system.aclose()
        logger.info("RAG system closed")
