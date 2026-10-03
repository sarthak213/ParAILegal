"""
app/api/routes/answer.py
────────────────────────
Two endpoints:

POST /api/v1/answer
    Full RAG pipeline, returns complete JSON when LLM finishes.
    Used by automated clients, the test suite, and batch processing.

POST /api/v1/answer/stream
    Same pipeline, but streams the LLM answer token-by-token as
    Server-Sent Events (SSE). Used by the front-end for live display.

SSE format (each line is one event):
    data: {"type": "sources", "sources": [...]}   ← sent first, before LLM starts
    data: {"type": "token",   "token": "The "}    ← one per token
    data: {"type": "token",   "token": "death "}
    data: {"type": "done",    "answer": "..."}     ← full answer on completion
    data: {"type": "error",   "detail": "..."}     ← on failure

SSE reliability
───────────────
Some reverse proxies (nginx, Cloudflare, AWS ALB) close idle SSE connections
after 60–120 seconds. Long LLM generations (J05 took 230s in testing) will
be killed mid-stream without a keepalive mechanism.

Fix: a background heartbeat task sends an SSE comment line every 15 seconds:
    : ping

SSE comment lines (starting with ":") are ignored by EventSource clients
but reset the proxy idle timer. This is the standard keepalive technique.

The heartbeat runs as an asyncio.Task alongside the main generator and is
cancelled cleanly when the stream ends (success, error, or disconnect).

Client disconnect detection
───────────────────────────
Without disconnect detection, the LLM keeps generating and Sarvam keeps
billing even after the user closes the browser tab. We check
request.is_disconnected() on each token — if True, we break early.
"""

import json
import httpx
import asyncio
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_rag_system
from app.schemas.requests import AnswerRequest
from app.schemas.responses import AnswerResponse, SourceChunk


router = APIRouter(
    prefix="/api/v1/answer",
    tags=["answer"],
)

# How often to send a keepalive ping to reset proxy idle timers.
# 15s is conservative — most proxies timeout at 60s+.
_HEARTBEAT_INTERVAL_S = 15


# ── Shared helper — builds a full SourceChunk from a metadata dict ────

def _to_source_chunk(s: dict) -> SourceChunk:
    """
    Maps all citation-bearing fields from the metadata dict into SourceChunk.
    Matches the fix already applied to search.py — both routes must be identical
    so citation_in_sources() in the test suite works for both endpoints.
    """
    return SourceChunk(
        # ── Identity fields checked by citation_in_sources() ──────────
        citation=s.get("citation"),
        source_type=s.get("source_type"),
        section=s.get("section") or s.get("article_number"),
        label=s.get("label"),
        hierarchy=s.get("hierarchy"),
        chunk_id=s.get("chunk_id"),

        # ── Content ───────────────────────────────────────────────────
        text=s.get("text"),
        document_title=s.get("document_title"),
        chunk_type=s.get("chunk_type"),
        status=s.get("status"),

        # ── Scoring ───────────────────────────────────────────────────
        # score is always raw FAISS cosine similarity (0-1).
        # rrf_score kept for transparency — used internally for ranking.
        score=s.get("_score"),
        rrf_score=s.get("_rrf_score"),
        rank=s.get("_rank"),
    )


# ── POST /api/v1/answer — blocking JSON response ──────────────────────

@router.post(
    "",
    response_model=AnswerResponse,
    summary="Answer a legal query",
    description=(
        "Full RAG pipeline: retrieve → rerank → generate. "
        "Returns the complete answer when the LLM finishes. "
        "For streaming token-by-token output use POST /api/v1/answer/stream."
    ),
)
async def answer(
    request: AnswerRequest,
    rag=Depends(get_rag_system),
) -> AnswerResponse:
    result = await rag.answer(request.query, request.domain)

    return AnswerResponse(
        query=result["query"],
        domain=result["domain"],
        answer=result["answer"],
        sources=[_to_source_chunk(s) for s in result["sources"]],
    )


# ── POST /api/v1/answer/stream — SSE streaming response ──────────────

@router.post(
    "/stream",
    summary="Stream a legal answer (SSE)",
    description=(
        "Same RAG pipeline as POST /answer, but streams the LLM response "
        "token-by-token using Server-Sent Events. "
        "First event contains the retrieved sources. "
        "Subsequent events contain individual tokens. "
        "Final event contains the complete assembled answer."
    ),
    response_class=StreamingResponse,
)
async def answer_stream(
    request: Request,
    body: AnswerRequest,
    rag=Depends(get_rag_system),
) -> StreamingResponse:
    return StreamingResponse(
        _stream_answer(rag, body.query, body.domain, request),
        media_type="text/event-stream",
        headers={
            # Prevent proxies and browsers from buffering SSE
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Heartbeat helper ──────────────────────────────────────────────────

async def _heartbeat(queue: asyncio.Queue, interval: float = _HEARTBEAT_INTERVAL_S):
    """
    Runs as a background task alongside the main stream generator.
    Puts SSE comment lines into the queue every `interval` seconds.
    SSE comments (": ping\\n\\n") are invisible to EventSource clients
    but reset the idle timer on nginx / Cloudflare / AWS ALB proxies,
    preventing premature connection closure on long generations.
    """
    try:
        while True:
            await asyncio.sleep(interval)
            await queue.put(": ping\n\n")
    except asyncio.CancelledError:
        pass


async def _stream_answer(
    rag,
    query: str,
    domain: str | None,
    request: Request,
) -> AsyncIterator[str]:
    """
    Core streaming generator with heartbeats and disconnect detection.

    Step 1 — Retrieve (sync, in thread): FAISS search + RRF fusion.
              Sources are sent to the client immediately so the UI can
              display citations while the LLM is still generating.

    Step 2 — Stream from Sarvam (async): httpx streaming POST.
              Each token chunk is forwarded as an SSE event as it arrives.
              A background heartbeat task keeps the connection alive.
              Client disconnect is checked on each token.

    Step 3 — Done event: full assembled answer sent for client-side storage.
    """

    def _sse(payload: dict) -> str:
        """Format a dict as an SSE data line."""
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    # ── Step 1: Retrieve sources (sync → thread) ──────────────────────
    # Start heartbeat immediately — retrieval can take 5-10s on cold start
    # and some proxies will close the connection before the first byte.
    heartbeat_queue: asyncio.Queue = asyncio.Queue()
    heartbeat_task = asyncio.create_task(
        _heartbeat(heartbeat_queue, _HEARTBEAT_INTERVAL_S)
    )

    try:
        results = await asyncio.to_thread(
            rag.search, query, None, domain
        )
        top = results[:rag.settings.TOP_K_ANSWER]
    except Exception as e:
        heartbeat_task.cancel()
        yield _sse({"type": "error", "detail": f"Retrieval failed: {e}"})
        return

    # Drain any heartbeat pings that fired during retrieval
    while not heartbeat_queue.empty():
        yield heartbeat_queue.get_nowait()

    # Send sources immediately — front-end can render citations now
    sources_payload = [
        _to_source_chunk(s).model_dump(exclude_none=True) for s in top
    ]
    detected_domain = domain or rag.queryRouter.route(query)
    yield _sse({
        "type":    "sources",
        "domain":  detected_domain,
        "sources": sources_payload,
    })

    if not top:
        heartbeat_task.cancel()
        yield _sse({
            "type":   "done",
            "answer": (
                "No relevant provisions were retrieved for this query. "
                "Please try rephrasing or narrowing the question.\n\n"
                "⚖ This is a research tool. Verify all provisions against "
                "the official Gazette. This is not legal advice."
            ),
        })
        return

    # ── Step 2: Build context and stream Sarvam ───────────────────────
    from app.infrastructure.llm.answerer import (
        _build_context, _detect_mode, _SYSTEM_PROMPT
    )

    mode, clean_query = _detect_mode(query)
    context           = _build_context(top)
    user_message      = rag.answerer._build_user_message(clean_query, mode, context)

    assembled_tokens = []
    thinking_announced = False   # reasoning models stream hidden reasoning before the answer
    finish_reason = None

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
        ) as client:
            async with client.stream(
                "POST",
                "https://api.sarvam.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {rag.settings.SARVAM_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":  rag.settings.SARVAM_MODEL,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user",   "content": user_message},
                    ],
                    "temperature": rag.settings.TEMPERATURE_ANSWER,
                    # Reasoning models spend part of this on hidden reasoning before
                    # the answer; 4096 was too few for a five-source context.
                    "max_tokens":  8192,
                    "stream":      True,
                },
            ) as response:

                if response.status_code != 200:
                    body = await response.aread()
                    heartbeat_task.cancel()
                    yield _sse({
                        "type":   "error",
                        "detail": (
                            f"Sarvam API error {response.status_code}: "
                            f"{body.decode()[:200]}"
                        ),
                    })
                    return

                async for line in response.aiter_lines():

                    # ── Client disconnect check ───────────────────────
                    # Stop generating if the user closed the tab.
                    # Saves Sarvam API tokens and server resources.
                    if await request.is_disconnected():
                        break

                    # ── Drain heartbeat pings ─────────────────────────
                    # Forward any keepalive pings that fired while we
                    # were waiting for the next token from Sarvam.
                    while not heartbeat_queue.empty():
                        yield heartbeat_queue.get_nowait()

                    if not line.startswith("data:"):
                        continue

                    raw = line[len("data:"):].strip()

                    if raw == "[DONE]":
                        break

                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices", [])
                    if not choices:
                        # Sarvam sends an empty choices list on the final
                        # chunk before [DONE] — skip it silently
                        continue

                    choice = choices[0]
                    finish_reason = choice.get("finish_reason") or finish_reason
                    delta_obj = choice.get("delta", {})

                    # Sarvam-105b streams `reasoning_content` (often for 15-30 s) before any
                    # `content`. Tell the UI once, so it can say what is happening.
                    if delta_obj.get("reasoning_content") and not thinking_announced:
                        thinking_announced = True
                        yield _sse({"type": "status", "stage": "thinking"})

                    delta = delta_obj.get("content")
                    if delta:
                        assembled_tokens.append(delta)
                        yield _sse({"type": "token", "token": delta})

    except httpx.TimeoutException:
        heartbeat_task.cancel()
        yield _sse({
            "type":   "error",
            "detail": "The request to the language model timed out. Please try again.",
        })
        return
    except Exception as e:
        heartbeat_task.cancel()
        yield _sse({"type": "error", "detail": f"Streaming error: {e}"})
        return
    finally:
        # Always cancel the heartbeat task — whether success, error, or disconnect
        heartbeat_task.cancel()

    # ── Step 3: Done — send full assembled answer ─────────────────────
    full_answer = "".join(assembled_tokens)

    if not full_answer.strip():
        # Never present an empty answer as a success (v1 showed only the disclaimer).
        reason = (
            "it ran out of its token budget while reasoning"
            if finish_reason == "length" else f"finish reason: {finish_reason or 'unknown'}"
        )
        yield _sse({
            "type":   "error",
            "detail": f"The language model returned no answer ({reason}). Please try again.",
        })
        return

    disclaimer = (
        "⚖ This is a research tool. Verify all provisions against "
        "the official Gazette. This is not legal advice."
    )
    if "⚖" not in full_answer:
        full_answer = full_answer.rstrip() + "\n\n" + disclaimer

    yield _sse({"type": "done", "answer": full_answer})