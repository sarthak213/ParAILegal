from typing import Any

from pydantic import BaseModel


class SourceChunk(BaseModel):

    # ── Identity — all checked by citation_in_sources() ──────────────
    citation:       str | None = None
    source_type:    str | None = None
    section:        str | None = None   # article_number (constitution) or section_number (statutes)
    label:          str | None = None   # sub-clause label e.g. "(1)(a)"
    hierarchy:      str | None = None   # full hierarchy string
    chunk_id:       str | None = None

    # ── Content ───────────────────────────────────────────────────────
    text:           str | None = None
    document_title: str | None = None
    chunk_type:     str | None = None   # clause / article / illustration / schedule
    status:         str | None = None   # active / omitted / repealed

    # ── Scoring ───────────────────────────────────────────────────────
    score:          float | None = None
    rrf_score:      float | None = None
    rank:           int | None = None


class SearchResponse(BaseModel):

    query:   str
    domain:  str
    results: list[SourceChunk]


class AnswerResponse(BaseModel):

    query:   str
    domain:  str
    answer:  str
    sources: list[SourceChunk]


class HealthResponse(BaseModel):

    status:  str
    indices: dict[str, Any]