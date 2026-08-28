"""
app/services/retrieval/fusion.py
─────────────────────────────────
Reciprocal Rank Fusion — pure stateless functions, no class needed.

RRF is the only ranking algorithm used post-retrieval. It merges
multiple result lists (one per query × one per index) into a single
ranked list without needing a reranker model.

Formula: score(d) = Σ 1 / (k + rank_i(d))
    k=60 is standard RRF constant (from the original Cormack et al. paper).
    Higher k → less weight on top ranks, more uniform fusion.
    60 is the empirically validated default for legal retrieval.

These functions are imported by Retriever — nothing else should need them.
If a reranker is added later, it slots in after _rrf_fuse() in retriever.py.
"""

import numpy as np
from typing import List, Dict

from app.infrastructure.faiss.faiss_index import FaissIndex


def rrf_fuse(result_lists: List[List[Dict]], k: int = 60) -> List[Dict]:
    """
    Merge multiple ranked result lists using Reciprocal Rank Fusion.

    Args:
        result_lists: Each inner list is one ranked result set
                      (e.g. original query on constitution index,
                       rewritten query on statutes index, etc.)
        k:            RRF constant. Default 60.

    Returns:
        Single list sorted by descending RRF score.
        Each dict has _rrf_score added; original _score is preserved.

    Merge key is chunk_id, falling back to hierarchy string.
    When the same chunk appears in multiple lists, the best _score
    (highest cosine similarity) is kept alongside the fused RRF score.
    """
    rrf_scores: Dict[str, float] = {}
    best_chunk: Dict[str, Dict]  = {}

    for result_list in result_lists:
        for rank, chunk in enumerate(result_list, start=1):
            cid = chunk.get("chunk_id") or chunk.get("hierarchy") or str(rank)
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (k + rank)
            if cid not in best_chunk or chunk.get("_score", 0) > best_chunk[cid].get("_score", 0):
                best_chunk[cid] = chunk

    fused = []
    for cid, score in sorted(rrf_scores.items(), key=lambda x: -x[1]):
        chunk = dict(best_chunk[cid])
        chunk["_rrf_score"] = round(score, 8)
        fused.append(chunk)
    return fused


def search_index(index: FaissIndex, emb: np.ndarray, k: int) -> List[Dict]:
    """
    Search a single FaissIndex. Returns empty list if index is empty.
    Thin wrapper so callers don't need to guard against empty indices.
    """
    if index.size == 0:
        return []
    return index.search(emb, k=k) or []