"""
app/services/retrieval/index_manager.py
────────────────────────────────────────
IndexManager owns the lifecycle of all Qdrant indices.

Replaces the FAISS-based version — same external interface,
Qdrant under the hood.

Key change from FAISS version
──────────────────────────────
FAISS needed build → save → load cycle with local files.
Qdrant persists automatically — once vectors are uploaded,
they're available forever without any local files.

So the lifecycle is simpler:
    is_index_available() → check Qdrant for existing vectors
    True  → load_index() → just counts existing vectors, no disk I/O
    False → build and upload → vectors stored in Qdrant Cloud

This means on Oracle Cloud VM startup:
    - First run: embeds corpus and uploads to Qdrant (~20 min for full corpus)
    - Every subsequent run: skips ingestion, queries Qdrant directly (~2s startup)
"""

import asyncio
import numpy as np
from typing import List, Callable

from app.core.config import Settings
from app.infrastructure.qdrant.qdrant_index import QdrantIndex
from app.infrastructure.loaders.dataset_loader import (
    ConstitutionLoader,
    StatuteLoader,
    JudgementLoader,
)


class IndexManager:
    """
    Owns build/load lifecycle for all three Qdrant index instances.
    Indices are public attributes — Retriever reads them directly.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        # Each instance scoped to one source_type but shares one Qdrant collection
        self.constitution_index = QdrantIndex(settings, "constitution")
        self.statutes_index     = QdrantIndex(settings, "statutes")
        self.judgements_index   = QdrantIndex(settings, "judgements")

    # ── Initialization ────────────────────────────────────────────────

    async def initialize(
        self,
        embed_fn: Callable[[List[str], str], np.ndarray],
        force_rebuild: bool = False,
    ) -> None:
        """
        Load or build all indices.

        With Qdrant, "load" just means checking that vectors exist.
        "Build" means embedding the JSONL data and uploading to Qdrant.
        Both happen in threads so the event loop isn't blocked.
        """
        await asyncio.to_thread(
            self._init_index,
            self.constitution_index,
            "constitution",
            embed_fn,
            force_rebuild,
        )
        await asyncio.to_thread(
            self._init_index,
            self.statutes_index,
            "statutes",
            embed_fn,
            force_rebuild,
        )
        await asyncio.to_thread(
            self._init_index,
            self.judgements_index,
            "judgements",
            embed_fn,
            force_rebuild,
        )

        print("\nAll indices ready.")
        print(f"  Constitution : {self.constitution_index}")
        print(f"  Statutes     : {self.statutes_index}")
        print(f"  Judgements   : {self.judgements_index}")

    def _init_index(
        self,
        index: QdrantIndex,
        corpus: str,
        embed_fn: Callable,
        force_rebuild: bool,
    ) -> None:
        """
        Load existing Qdrant data or build from JSONL if not present.
        Passing index_dir="" because Qdrant doesn't use local paths.
        """
        if not force_rebuild and index.is_index_available(""):
            print(f"\n✅ {corpus.title()} index already in Qdrant — skipping ingestion")
            index.load_index("")
            return

        print(f"\nBuilding {corpus} index...")
        texts, metadata = self._load_corpus(corpus)
        if not texts:
            print(f"  No {corpus} data found — skipping")
            return

        print(f"  Embedding {len(texts)} {corpus} chunks...")
        embeddings = embed_fn(texts, corpus.title())
        index.build_index(embeddings, texts, metadata)

    def _load_corpus(self, corpus: str):
        if corpus == "constitution":
            return ConstitutionLoader(self.settings).load_dataset()
        elif corpus == "statutes":
            return StatuteLoader(self.settings).load_dataset()
        elif corpus == "judgements":
            return JudgementLoader(self.settings).load_dataset()
        return [], []

    # ── Rebuild helpers ───────────────────────────────────────────────

    def rebuild_constitution(self, embed_fn: Callable) -> None:
        self._init_index(self.constitution_index, "constitution", embed_fn, True)

    def rebuild_statutes(self, embed_fn: Callable) -> None:
        self._init_index(self.statutes_index, "statutes", embed_fn, True)

    def rebuild_judgements(self, embed_fn: Callable) -> None:
        self._init_index(self.judgements_index, "judgements", embed_fn, True)

    # ── Stats ─────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "constitution": self.constitution_index.size,
            "statutes":     self.statutes_index.size,
            "judgements":   self.judgements_index.size,
        }