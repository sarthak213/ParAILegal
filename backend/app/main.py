from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.lifecycle import lifespan
from app.core.logging import setup_logging
from app.api.routes.search import router as search_router
from app.api.routes.answer import router as answer_router
from app.api.routes.admin import router as admin_router
from app.api.routes.case import router as case_router
from app.middleware.error_handler import global_exception_handler
from app.middleware.request_logger import log_requests

logger = setup_logging()

app = FastAPI(
    title="ParAILegal API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search_router)
app.include_router(answer_router)
app.include_router(admin_router)
app.include_router(case_router)
app.add_exception_handler(Exception, global_exception_handler)
app.middleware("http")(log_requests)
@app.get("/health")
async def health():

    return {
        "status": "ok"
    }


@app.get("/ready")
async def ready():

    if not app.state.ready:

        if app.state.error:
            return {
                "status": "failed",
                "error": app.state.error,
            }

        return {
            "status": "starting"
        }

    rag = app.state.rag_system

    out = {
        "status": "ready",
        "indices": rag.index_stats(),
    }
    if hasattr(rag, "model_status"):
        out["model"] = rag.model_status()
    return out