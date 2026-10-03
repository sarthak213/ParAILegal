from fastapi import APIRouter, Depends

from app.api.deps import get_rag_system
from app.schemas.requests import SearchRequest
from app.schemas.responses import SearchResponse, SourceChunk

router = APIRouter(
    prefix="/api/v1/search",
    tags=["search"],
)


@router.post(
    "",
    response_model=SearchResponse,
)
async def search(
    request: SearchRequest,
    rag=Depends(get_rag_system),
):
    import asyncio

    results = await asyncio.to_thread(
        rag.search,
        request.query,
        request.k,
        request.domain,
    )

    return SearchResponse(
        query=request.query,
        domain=request.domain or rag.queryRouter.route(request.query),
        results=[
            SourceChunk(
                # ── Identity fields used by citation_in_sources() ──────
                # All five fields are checked: citation, hierarchy,
                # chunk_id, section, label. We must send all of them
                # so the test suite (and any front-end) can locate a
                # chunk by whichever identifier is present.
                citation=r.get("citation"),
                source_type=r.get("source_type"),
                section=r.get("section") or r.get("article_number"),
                label=r.get("label"),
                hierarchy=r.get("hierarchy"),
                chunk_id=r.get("chunk_id"),

                # ── Content ───────────────────────────────────────────
                text=r.get("text"),
                document_title=r.get("document_title"),
                chunk_type=r.get("chunk_type"),
                status=r.get("status"),

                # ── Scoring ───────────────────────────────────────────
                # `score` is always the raw FAISS cosine similarity (0–1).
                # This is the meaningful number for humans and the UI —
                # it tells you how semantically similar the chunk is to
                # the query, regardless of whether RRF fusion ran.
                #
                # `rrf_score` is kept separately for transparency. It is
                # used internally for ranking when multiple result lists
                # are fused (original + rewritten query × multiple indices),
                # but is not meaningful as a relevance indicator on its own
                # since it is mathematically bounded to ~0.016–0.033.
                score=r.get("_score"),
                rrf_score=r.get("_rrf_score"),
                rank=r.get("_rank"),
            )
            for r in results
        ],
    )