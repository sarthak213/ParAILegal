from fastapi import APIRouter, Depends

from app.api.deps import get_rag_system


router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
)


@router.get("/stats")
async def stats(
    rag = Depends(get_rag_system),
):

    return {
        "indices": rag.index_stats(),
        "models_loaded": rag.modelLoader.models_loaded,
    }