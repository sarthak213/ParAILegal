import time
import logging

from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.middleware.base import BaseHTTPMiddleware


logger = logging.getLogger("parai-legal")


async def log_requests(request: Request, call_next):
    """
    HTTP request logger.

    For streaming responses (SSE, chunked transfer) we must NOT await
    the full body — doing so buffers the entire stream in memory and
    defeats the purpose of streaming entirely.

    Strategy:
      - Non-streaming: log start + end with duration and status (original behaviour)
      - Streaming:     log start immediately, log end from a wrapper that
                       fires after the last chunk is sent to the client
    """
    start_time = time.time()

    logger.info(f"Incoming request: {request.method} {request.url.path}")

    response = await call_next(request)

    # Detect streaming responses by content-type or response class.
    # StreamingResponse sets transfer-encoding: chunked or content-type: text/event-stream.
    is_streaming = (
        response.headers.get("content-type", "").startswith("text/event-stream")
        or response.headers.get("transfer-encoding") == "chunked"
        or isinstance(response, StreamingResponse)
    )

    if is_streaming:
        # Log immediately — we can't know duration until the stream ends,
        # and waiting for it would buffer the entire response.
        # Duration is logged as -1 to signal "stream in progress".
        logger.info(
            f"Streaming response: {request.method} {request.url.path} "
            f"Status={response.status_code} [streaming]"
        )
        # Wrap the body iterator to log when the stream actually completes
        response.body_iterator = _log_stream_completion(
            response.body_iterator,
            request.method,
            request.url.path,
            response.status_code,
            start_time,
        )
        return response

    # Non-streaming: original behaviour
    duration = round((time.time() - start_time) * 1000, 2)
    logger.info(
        f"Completed request: {request.method} {request.url.path} "
        f"Status={response.status_code} Duration={duration}ms"
    )
    response.headers["X-Process-Time-MS"] = str(duration)
    return response


async def _log_stream_completion(body_iterator, method, path, status, start_time):
    """
    Transparently passes chunks through while tracking when the stream ends.
    Logs the total duration once the last chunk has been sent to the client.
    """
    try:
        async for chunk in body_iterator:
            yield chunk
    finally:
        duration = round((time.time() - start_time) * 1000, 2)
        logger.info(
            f"Stream complete: {method} {path} "
            f"Status={status} Duration={duration}ms"
        )