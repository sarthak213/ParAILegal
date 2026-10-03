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

    rag = RAGSystem(settings)

    try:
        # initialize() is now async — awaited directly.
        # Internally it:
        #   - awaits load_models()  (async httpx calls to LM Studio)
        #   - runs index init in threads via asyncio.to_thread (disk + embedding)
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