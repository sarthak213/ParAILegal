import logging

from fastapi import Request
from fastapi.responses import JSONResponse


logger = logging.getLogger("parai-legal")


async def global_exception_handler(
    request: Request,
    exc: Exception,
):

    logger.exception(
        f"Unhandled exception on {request.method} {request.url.path}"
    )

    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error"
        },
    )