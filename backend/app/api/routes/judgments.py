"""Supreme Court judgments (offline; app/judgments/store.py).

    POST /api/v1/judgments/search   {query, refs, before, k}  -> judgments, each with holdings and passages
    GET  /api/v1/judgments/{id}                               -> the whole judgment, with its standing
                                                                 and the law it applied as it was then
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_rag_system
from app.case.precedents import older_refs, standing
from app.judgments.law_at_time import law_at_time
from app.judgments.store import catchline, holdings, passages

router = APIRouter(prefix="/api/v1/judgments", tags=["judgments"])


class JudgmentSearch(BaseModel):
    query: str = Field("", max_length=2000)
    refs: list[str] = Field(default_factory=list, max_length=20)  # "BNS 103": its old-code twin is searched too
    before: str | None = Field(None, pattern=r"^\d{4}(-\d{2}-\d{2})?$")
    k: int = Field(10, ge=1, le=50)


def _store(rag):
    store = getattr(rag, "judgments", None)
    if store is None:
        raise HTTPException(503, "The Supreme Court judgments are not installed (scripts/build_judgments_index.py)")
    return store


@router.post("/search", summary="Search Supreme Court judgments")
async def search(body: JudgmentSearch, rag=Depends(get_rag_system)) -> dict:
    store = _store(rag)
    if not body.query.strip() and not body.refs:
        raise HTTPException(422, "Give a query, provisions, or both")

    def run() -> list[dict]:
        refs = older_refs(body.refs, rag.engine) if body.refs else []
        before = body.before if body.before is None or len(body.before) == 10 else f"{body.before}-12-31"
        out = []
        for hit in store.search(body.query, k=body.k, refs=refs, before=before):
            record = store.record(hit.id) or {}
            out.append({**hit.to_dict(), "status": standing(hit.status), "holdings": holdings(record)[:3],
                        "passages": passages(record, body.query) if body.query else []})
        return out

    return {"judgments": await asyncio.to_thread(run)}


@router.get("/{judgment_id}", summary="One judgment")
async def judgment(judgment_id: str, rag=Depends(get_rag_system)) -> dict:
    store = _store(rag)
    record = await asyncio.to_thread(store.record, judgment_id)
    if record is None:
        raise HTTPException(404, f"No judgment {judgment_id}")
    then = []
    if rag.history is not None:
        then = [{"ref": p.ref, "note": p.note, "text_then": p.text_then, "complete": p.complete,
                 "amended_since": p.amended_since, "now": p.now}
                for p in await asyncio.to_thread(law_at_time, record, rag.engine, rag.history)]
    return {**record, "catchline": catchline(record), "holdings": holdings(record, limit=20),
            "status": standing(store.status.get(judgment_id, {})), "law_at_time": then}
