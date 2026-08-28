"""
faissIndex.py
─────────────
FAISS index wrapper that stores metadata alongside vectors.

Key differences from the original:
  - build_index() takes (embeddings, texts, metadata) — not just texts
  - search() returns List[Dict] with full metadata + score, not raw indices
  - save_index() / load_index() persist both the FAISS binary and the
    metadata store to the index directory
  - is_index_available() lets ragSystem decide whether to build or load
  - Three separate instances of this class back the three indices:
      constitution_index, statutes_index, judgements_index
"""

import os
import pickle
from typing import List, Dict, Any, Optional

import faiss
import numpy as np

from app.core.config import Settings


class FaissIndex:

    def __init__(self, settings: Settings):
        self.settings    = settings
        self.index       = None          # faiss.Index
        self.texts       = []            # List[str]  — embed strings (not shown to user)
        self.metadata    = []            # List[Dict] — normalised chunk dicts

    # ──────────────────────────────────────────────────────────────────
    # Build
    # ──────────────────────────────────────────────────────────────────

    def build_index(
        self,
        embeddings: np.ndarray,
        texts: List[str],
        metadata: List[Dict],
    ) -> None:
        """
        Build a FAISS IndexFlatIP (inner product = cosine when vectors are
        L2-normalised, which BGE embeddings are by default).

        Args:
            embeddings: (N, dim) float32 array, already L2-normalised
            texts:      embed strings, parallel to embeddings
            metadata:   normalised chunk dicts, parallel to embeddings
        """
        if len(embeddings) != len(texts) or len(texts) != len(metadata):
            raise ValueError(
                f"Lengths must match: embeddings={len(embeddings)}, "
                f"texts={len(texts)}, metadata={len(metadata)}"
            )

        print(f"Building FAISS index ({len(texts)} chunks, dim={embeddings.shape[1]})...")

        self.texts    = texts
        self.metadata = metadata
        self.index    = faiss.IndexFlatIP(embeddings.shape[1])
        self.index.add(embeddings)

        print(f"  Index built with {self.index.ntotal} vectors")

    # ──────────────────────────────────────────────────────────────────
    # Search
    # ──────────────────────────────────────────────────────────────────

    def search(
        self,
        query_embedding: np.ndarray,
        k: int = 10,
        source_filter: Optional[str] = None,
    ) -> List[Dict]:
        """
        Search for the top-k most similar chunks.

        Args:
            query_embedding: (1, dim) float32 array
            k:               number of results to return
            source_filter:   if set, only return chunks where
                             metadata['source_type'] == source_filter.
                             To handle filtering, we over-fetch (k * 4)
                             then trim.

        Returns:
            List of metadata dicts, each enriched with:
                "_score":     float  — inner-product similarity score
                "_rank":      int    — 1-based rank in this result set
        """
        if self.index is None:
            raise RuntimeError("Index not built. Call build_index() first.")
        if self.index.ntotal == 0:
            return []

        # Over-fetch when filtering so we still return k results after trim
        fetch_k = k * 4 if source_filter else k
        fetch_k = min(fetch_k, self.index.ntotal)

        scores, indices = self.index.search(query_embedding, fetch_k)
        scores  = scores[0].tolist()
        indices = indices[0].tolist()

        results = []
        for score, idx in zip(scores, indices):
            if idx < 0 or idx >= len(self.metadata):
                continue
            meta = dict(self.metadata[idx])         # shallow copy — never mutate store
            meta["_score"] = round(float(score), 6)

            if source_filter and meta.get("source_type") != source_filter:
                continue
            if score < self.settings.MIN_SCORE_THRESHOLD:
                continue

            results.append(meta)
            if len(results) >= k:
                break

        for i, r in enumerate(results):
            r["_rank"] = i + 1

        return results

    def get_results_by_indices(
        self,
        indices: np.ndarray,
        scores:  np.ndarray,
    ) -> List[Dict]:
        """
        Used by RRF fusion — retrieve results for a set of raw FAISS indices.
        Returns metadata dicts enriched with _score.
        """
        flat_idx    = indices.flatten().tolist()
        flat_scores = scores.flatten().tolist()
        results = []
        for idx, score in zip(flat_idx, flat_scores):
            if idx < 0 or idx >= len(self.metadata):
                continue
            meta = dict(self.metadata[idx])
            meta["_score"] = round(float(score), 6)
            results.append(meta)
        return results

    # ──────────────────────────────────────────────────────────────────
    # Persistence
    # ──────────────────────────────────────────────────────────────────

    def save_index(self, index_dir: str) -> None:
        """
        Save the FAISS index binary and the metadata store to index_dir.

        Files written:
            <index_dir>/index.faiss   — FAISS binary
            <index_dir>/store.pkl     — (texts, metadata) pickle
        """
        os.makedirs(index_dir, exist_ok=True)

        faiss_path = os.path.join(index_dir, "index.faiss")
        store_path = os.path.join(index_dir, "store.pkl")

        faiss.write_index(self.index, faiss_path)
        with open(store_path, "wb") as f:
            pickle.dump({"texts": self.texts, "metadata": self.metadata}, f)

        print(f"  Saved index → {index_dir}  "
              f"({self.index.ntotal} vectors, {len(self.metadata)} chunks)")

    def load_index(self, index_dir: str) -> None:
        """
        Load a previously saved index from index_dir.
        Raises FileNotFoundError if either file is missing.
        """
        faiss_path = os.path.join(index_dir, "index.faiss")
        store_path = os.path.join(index_dir, "store.pkl")

        if not os.path.exists(faiss_path) or not os.path.exists(store_path):
            raise FileNotFoundError(
                f"Index not found in {index_dir}. "
                f"Run ragSystem.build_indices() first."
            )

        self.index = faiss.read_index(faiss_path)
        with open(store_path, "rb") as f:
            store = pickle.load(f)
        self.texts    = store["texts"]
        self.metadata = store["metadata"]

        print(f"  Loaded index ← {index_dir}  "
              f"({self.index.ntotal} vectors, {len(self.metadata)} chunks)")

    def is_index_available(self, index_dir: str) -> bool:
        """True if both index files exist in index_dir."""
        return (
            os.path.exists(os.path.join(index_dir, "index.faiss")) and
            os.path.exists(os.path.join(index_dir, "store.pkl"))
        )

    # ──────────────────────────────────────────────────────────────────
    # Info
    # ──────────────────────────────────────────────────────────────────

    @property
    def size(self) -> int:
        """Number of vectors currently in the index."""
        return self.index.ntotal if self.index else 0

    def __repr__(self) -> str:
        return (
            f"FaissIndex(vectors={self.size}, "
            f"chunks={len(self.metadata)})"
        )
