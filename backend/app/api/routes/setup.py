"""First-run setup: what is installed, and the downloads (app/setup/download.py).

    GET  /api/v1/setup             each download: size, installed, available, progress; and what is locked
    POST /api/v1/setup/download    {item}: start one (one at a time); progress is read back from GET
    POST /api/v1/setup/cancel      stop the one running (its part is kept, and resumes next time)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_rag_system

router = APIRouter(prefix="/api/v1/setup", tags=["setup"])


class DownloadRequest(BaseModel):
    item: str = Field(..., max_length=40)


def _service(rag):
    if not hasattr(rag, "downloads"):
        raise HTTPException(501, "Setup needs the offline (v2) engine")
    return rag


@router.get("", summary="What is installed and what can be downloaded")
async def status(rag=Depends(get_rag_system)) -> dict:
    rag = _service(rag)
    return {"items": rag.downloads.status(), "case_builder_locked": rag.case_locked,
            "answers": rag.answerer is not None, "judgments": rag.judgments is not None}


@router.post("/download", summary="Start a download")
async def download(body: DownloadRequest, rag=Depends(get_rag_system)) -> dict:
    rag = _service(rag)
    try:
        rag.downloads.start(body.item)
    except KeyError:
        raise HTTPException(404, f"No download called {body.item}") from None
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"started": body.item}


@router.post("/cancel", summary="Stop the running download")
async def cancel(rag=Depends(get_rag_system)) -> dict:
    _service(rag).downloads.cancel()
    return {"cancelled": True}
