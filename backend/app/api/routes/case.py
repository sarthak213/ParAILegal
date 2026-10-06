"""Case Builder (criminal, CB-1): typed facts -> structured facts -> offences -> elements -> brief.

    POST /api/v1/case/facts      {facts}                      -> structured facts (model)
    POST /api/v1/case/offences   {facts, acts}                -> candidate offences with procedure (no model)
    POST /api/v1/case/elements   {facts, ref}                 -> elements checklist for one offence (model)
    POST /api/v1/case/brief      {facts, refs, checklists}    -> the brief, streamed (SSE, as /answer/stream)

Each step's output is meant to be shown and edited before the next is called.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.answer import prompts
from app.answer.evidence import render
from app.answer.local import finish
from app.answer.verify import citations, verify
from app.api.deps import get_rag_system
from app.api.routes.answer import _sse, _to_source_chunk
from app.case import builder
from app.case.elements import OFFENCES
from app.llm.client import stream_chat

router = APIRouter(prefix="/api/v1/case", tags=["case builder"])


class FactsRequest(BaseModel):
    facts: str = Field(..., min_length=20, max_length=8000)


class OffencesRequest(FactsRequest):
    # the structured facts' "acts" (from /facts, as the lawyer edited them); better queries than the narrative
    acts: list[str] = Field(default_factory=list, max_length=20)


class ElementsRequest(FactsRequest):
    ref: str = Field(..., max_length=120)


class BriefRequest(FactsRequest):
    refs: list[str] = Field(..., min_length=1, max_length=6)
    checklists: dict[str, list[dict]] = Field(default_factory=dict)


def _v2(rag):
    if not hasattr(rag, "prepare"):
        raise HTTPException(501, "The Case Builder needs the offline (v2) engine")
    return rag


async def _model_url(rag) -> str:
    if rag.answerer is None:
        raise HTTPException(503, "No answer model is installed; the Case Builder needs one for this step")
    try:
        await rag.answerer.server.ensure_running()
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    rag.answerer.server.touch()
    return rag.answerer.server.base_url


@router.post("/facts", summary="Structure the facts")
async def facts(body: FactsRequest, rag=Depends(get_rag_system)) -> dict:
    rag = _v2(rag)
    return {"facts": await builder.structure_facts(body.facts, await _model_url(rag))}


@router.post("/offences", summary="Candidate offences for the facts")
async def offences(body: OffencesRequest, rag=Depends(get_rag_system)) -> dict:
    rag = _v2(rag)
    found = await asyncio.to_thread(builder.offences, body.facts, rag.engine, rag.schedule, body.acts)
    return {"offences": found}


@router.post("/elements", summary="Elements checklist for one offence")
async def elements(body: ElementsRequest, rag=Depends(get_rag_system)) -> dict:
    rag = _v2(rag)
    offence = OFFENCES.get(body.ref)
    if offence is None:
        raise HTTPException(404, f"No elements checklist for {body.ref} yet")
    checks = await builder.check_elements(body.facts, offence, await _model_url(rag))
    return {"ref": body.ref, "name": offence.name, "summary": builder.element_summary(checks), "elements": checks}


@router.post("/brief", summary="Write the case brief (SSE)", response_class=StreamingResponse)
async def brief(request: Request, body: BriefRequest, rag=Depends(get_rag_system)) -> StreamingResponse:
    rag = _v2(rag)
    base_url = await _model_url(rag)
    return StreamingResponse(_stream_brief(rag, body, base_url, request), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _stream_brief(rag, body: BriefRequest, base_url: str, request: Request) -> AsyncIterator[str]:
    evidence = await asyncio.to_thread(builder.brief_evidence, body.refs, rag.engine)
    if not evidence:
        yield _sse({"type": "error", "detail": "None of those provisions is in the corpus"})
        return
    yield _sse({"type": "sources", "domain": "statutes",
                "sources": [_to_source_chunk(e.hit).model_dump(exclude_none=True) for e in evidence]})
    # only the confirmed offences' checklists: a ruled-out offence must not reach the brief
    confirmed = {ref: checks for ref, checks in body.checklists.items() if ref in body.refs}
    messages = prompts.brief_messages(body.facts, render(evidence), builder.checklist_text(confirmed))
    pieces: list[str] = []
    try:
        async for text in stream_chat(base_url, messages, prompts.BRIEF_MAX_TOKENS, rag.settings.TEMPERATURE_ANSWER):
            if await request.is_disconnected():
                return
            rag.answerer.server.touch()
            pieces.append(text)
            yield _sse({"type": "token", "token": text})
    except Exception as e:
        yield _sse({"type": "error", "detail": str(e)})
        return
    fixed, v = verify("".join(pieces), evidence, body.facts)
    table = builder.procedure_table(body.refs, rag.schedule)
    if table:
        yield _sse({"type": "token", "token": table})
    yield _sse({"type": "done", "answer": finish(fixed + table), "citations": citations(evidence),
                "verification": v.to_dict()})

