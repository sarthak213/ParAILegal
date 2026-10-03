from fastapi import HTTPException, Request


def get_rag_system(request: Request):

    if not request.app.state.ready:

        raise HTTPException(
            status_code=503,
            detail="RAG system still initializing",
        )

    return request.app.state.rag_system