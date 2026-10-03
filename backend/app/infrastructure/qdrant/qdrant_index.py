"""
qdrant_index.py
───────────────
Qdrant vector store wrapper — replaces faiss_index.py entirely.

Keeps the same external interface as FaissIndex so that Retriever,
IndexManager, and fusion.py require zero changes:

    search(query_embedding, k, source_filter) → List[Dict]
    size                                       → int

Key differences from FaissIndex
────────────────────────────────
- No local files — all data lives in Qdrant Cloud (or self-hosted Qdrant)
- build_index() uploads vectors + metadata to Qdrant collection
- search() queries Qdrant instead of FAISS
- is_index_available() checks if collection exists and has vectors
- save_index() / load_index() are no-ops (Qdrant persists automatically)
- source_filter uses Qdrant payload filtering (fast, server-side)

Collection strategy
────────────────────
All three corpora (constitution, statutes, judgements) live in ONE
Qdrant collection ("parailegal") with a `source_type` payload field.
This is simpler than three collections and allows cross-domain search
in a single query. Domain filtering uses Qdrant's payload filter.

The collection is created with:
    vectors: dim=1024 (BGE-M3), distance=Cosine
    on_disk_payload: True (saves RAM in Qdrant Cloud free tier)
"""

import numpy as np
from typing import List, Dict, Optional, Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
)

from app.core.config import Settings


# BGE-M3 embedding dimension
_VECTOR_DIM = 1024


class QdrantIndex:
    """
    Qdrant-backed vector store with the same interface as FaissIndex.

    One instance of this class is created per corpus domain in IndexManager,
    but all three instances point to the same Qdrant collection and filter
    by source_type at search time.
    """

    def __init__(self, settings: Settings, source_type: str):
        """
        Args:
            settings:    App settings (QDRANT_URL, QDRANT_API_KEY, etc.)
            source_type: "constitution" | "statutes" | "judgements"
                         Used to namespace vectors within the shared collection.
        """
        self.settings    = settings
        self.source_type = source_type
        self._size       = 0   # cached count, updated after build/load check

        self._client = QdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY,
        )

    # ── Build ─────────────────────────────────────────────────────────

    def build_index(
        self,
        embeddings: np.ndarray,
        texts: List[str],
        metadata: List[Dict],
    ) -> None:
        """
        Upload vectors + metadata to Qdrant.

        Creates the collection if it doesn't exist.
        Upserts in batches of 100 to stay within Qdrant Cloud free tier
        request size limits.

        Each point payload contains the full metadata dict so search()
        can return it directly — no separate metadata store needed.
        """
        if len(embeddings) != len(metadata):
            raise ValueError(
                f"Lengths must match: embeddings={len(embeddings)}, "
                f"metadata={len(metadata)}"
            )

        self._ensure_collection()

        print(f"Uploading {len(embeddings)} {self.source_type} vectors to Qdrant...")

        # Generate stable integer IDs from chunk position + source type
        # We offset by source_type to avoid ID collisions across corpora
        offset = {"constitution": 0, "statutes": 1_000_000, "judgements": 2_000_000}
        base   = offset.get(self.source_type, 3_000_000)

        batch_size = 100
        for i in range(0, len(embeddings), batch_size):
            batch_emb  = embeddings[i:i + batch_size]
            batch_meta = metadata[i:i + batch_size]

            points = []
            for j, (emb, meta) in enumerate(zip(batch_emb, batch_meta)):
                point_id = base + i + j
                payload  = dict(meta)
                # Ensure source_type is always in payload for filtering
                payload["source_type"] = self.source_type
                # Remove numpy types that Qdrant can't serialize
                payload = _sanitize_payload(payload)

                points.append(PointStruct(
                    id=point_id,
                    vector=emb.tolist(),
                    payload=payload,
                ))

            self._client.upsert(
                collection_name=self.settings.QDRANT_COLLECTION,
                points=points,
            )

        self._size = len(embeddings)
        print(f"  ✅ Uploaded {len(embeddings)} {self.source_type} vectors")

    def _ensure_collection(self) -> None:
        """Create the Qdrant collection if it doesn't exist."""
        collections = [
            c.name for c in self._client.get_collections().collections
        ]
        if self.settings.QDRANT_COLLECTION not in collections:
            print(f"Creating Qdrant collection '{self.settings.QDRANT_COLLECTION}'...")
            self._client.create_collection(
                collection_name=self.settings.QDRANT_COLLECTION,
                vectors_config=VectorParams(
                    size=_VECTOR_DIM,
                    distance=Distance.COSINE,
                ),
            )
            self._client.create_payload_index(
                collection_name=self.settings.QDRANT_COLLECTION,
                field_name="source_type",
                field_schema="keyword",
            )
            print(f"  ✅ Collection created with source_type index")

    # ── Search ────────────────────────────────────────────────────────

    # Maps logical corpus name to actual source_type values stored in Qdrant payload.
    # Constitution: stored as "constitution"
    # Statutes: stored as "statutes" (IndexManager used logical name, not per-file type)
    # Judgements: stored as "judgement" (singular, from dataset_loader.py)
    _SOURCE_TYPE_MAP = {
        "constitution": ["constitution"],
        "statutes":     ["statutes"],
        "judgements":   ["judgements"],
    }

    def search(
        self,
        query_embedding: np.ndarray,
        k: int = 10,
        source_filter: Optional[str] = None,
    ) -> List[Dict]:
        """
        Search Qdrant for the top-k most similar chunks.

        Args:
            query_embedding: (1, 1024) float32 array
            k:               number of results
            source_filter:   if set, restrict to this source_type.
                             When None, restricts to self.source_type
                             (each index instance only searches its corpus).

        Returns:
            List of metadata dicts enriched with _score and _rank.
        """
        filter_corpus = source_filter or self.source_type
        # Resolve logical corpus name to actual stored source_type values
        source_types = self._SOURCE_TYPE_MAP.get(filter_corpus, [filter_corpus])

        # Build filter: match any of the source_type values for this corpus
        if len(source_types) == 1:
            query_filter = Filter(
                must=[
                    FieldCondition(
                        key="source_type",
                        match=MatchValue(value=source_types[0]),
                    )
                ]
            )
        else:
            # Use should (OR) for multiple source types
            query_filter = Filter(
                should=[
                    FieldCondition(
                        key="source_type",
                        match=MatchValue(value=st),
                    )
                    for st in source_types
                ]
            )

        try:
            results = self._client.query_points(
                collection_name=self.settings.QDRANT_COLLECTION,
                query=query_embedding.flatten().tolist(),
                query_filter=query_filter,
                limit=k,
                with_payload=True,
            ).points
        except Exception as e:
            print(f"  Qdrant search error ({self.source_type}): {e}")
            return []

        output = []
        for rank, point in enumerate(results, start=1):
            if point.score < self.settings.MIN_SCORE_THRESHOLD:
                continue
            meta = dict(point.payload or {})
            meta["_score"] = round(float(point.score), 6)
            meta["_rank"]  = rank
            output.append(meta)

        return output

    def get_results_by_indices(
        self,
        indices: np.ndarray,
        scores: np.ndarray,
    ) -> List[Dict]:
        """
        Not used with Qdrant — kept for interface compatibility with fusion.py.
        fusion.py calls rrf_fuse() which works on the output of search(),
        not on raw indices, so this method is never called in practice.
        """
        return []

    # ── Persistence ───────────────────────────────────────────────────

    def save_index(self, index_dir: str) -> None:
        """No-op — Qdrant persists automatically."""
        print(f"  Qdrant: data persisted automatically (no local save needed)")

    def load_index(self, index_dir: str) -> None:
        """No-op — data is always in Qdrant Cloud."""
        count = self._get_count()
        self._size = count
        print(f"  Qdrant: {count} {self.source_type} vectors available")

    def is_index_available(self, index_dir: str) -> bool:
        """
        Returns True if this source_type has vectors in Qdrant.
        Also ensures payload index exists on source_type field —
        this handles collections created before the index was added.
        """
        try:
            # Ensure payload index exists (idempotent — safe to call repeatedly)
            self._ensure_payload_index()
            count = self._get_count()
            return count > 0
        except Exception:
            return False

    def _ensure_payload_index(self) -> None:
        """
        Create payload index on source_type if it doesn't exist.
        Without this index, filtered count/search requests return 400
        on Qdrant Cloud. Safe to call multiple times — Qdrant ignores
        duplicate index creation requests.
        """
        try:
            self._client.create_payload_index(
                collection_name=self.settings.QDRANT_COLLECTION,
                field_name="source_type",
                field_schema="keyword",
            )
        except Exception:
            pass  # Already exists — that's fine

    def _get_count(self) -> int:
        """Count vectors for this corpus in the shared collection."""
        source_types = self._SOURCE_TYPE_MAP.get(self.source_type, [self.source_type])
        total = 0
        for st in source_types:
            try:
                result = self._client.count(
                    collection_name=self.settings.QDRANT_COLLECTION,
                    count_filter=Filter(
                        must=[
                            FieldCondition(
                                key="source_type",
                                match=MatchValue(value=st),
                            )
                        ]
                    ),
                    exact=True,
                )
                total += result.count
            except Exception:
                pass
        return total
    # ── Info ─────────────────────────────────────────────────────────

    @property
    def size(self) -> int:
        if self._size == 0:
            self._size = self._get_count()
        return self._size

    def __repr__(self) -> str:
        return f"QdrantIndex(source={self.source_type}, vectors={self.size})"


# ── Helpers ───────────────────────────────────────────────────────────

def _sanitize_payload(payload: Dict) -> Dict:
    """
    Convert numpy types and Path objects to Python primitives.
    Qdrant's JSON serializer can't handle numpy int64, float32, etc.
    """
    clean = {}
    for k, v in payload.items():
        if v is None:
            continue   # skip None values — Qdrant stores them but wastes space
        elif isinstance(v, (np.integer,)):
            clean[k] = int(v)
        elif isinstance(v, (np.floating,)):
            clean[k] = float(v)
        elif isinstance(v, np.ndarray):
            clean[k] = v.tolist()
        elif hasattr(v, '__fspath__'):
            clean[k] = str(v)   # Path objects
        elif isinstance(v, (list, tuple)):
            clean[k] = [str(i) if hasattr(i, '__fspath__') else i for i in v]
        else:
            clean[k] = v
    return clean