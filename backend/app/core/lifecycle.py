import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.services.rag_services import RAGSystem


logger = logging.getLogger("parai-legal")


@asynccontextmanager
async def lifespan(app: FastAPI):

    logger.info("Starting ParAILegal API...")

    app.state.ready = False
    app.state.rag_system = None

    if settings.SEARCH_ENGINE == "v2":
        from app.search.service import SearchService

        rag = SearchService(settings)
    else:
        rag = RAGSystem(settings)

    try:
        if settings.SEARCH_ENGINE == "v2":
            await rag.initialize()
        else:
            # v1: awaits load_models() (cloud API checks), then builds or loads the Qdrant
            # indices in threads via asyncio.to_thread
            await rag.initialize(force_rebuild=False)

        app.state.rag_system = rag
        app.state.ready = True

        logger.info("RAG system initialized successfully")

    except Exception:
        logger.exception("Failed to initialize RAG system")
        # Don't re-raise — let the app start so /health can report the error.
        # Routes protected by get_rag_system will return 503 until ready=True.

    yield

    # ── Shutdown ──────────────────────────────────────────────────────
    logger.info("Shutting down ParAILegal API...")

    if app.state.rag_system is not None:
        await app.state.rag_system.aclose()
        logger.info("httpx connection pools closed")