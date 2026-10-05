from fastapi import HTTPException, Request


def get_rag_system(request: Request):

    if not request.app.state.ready:

        error = getattr(request.app.state, "error", None)
        raise HTTPException(
            status_code=503,
            detail=f"Search failed to start: {error}" if error else "RAG system still initializing",
        )

    return request.app.state.rag_system