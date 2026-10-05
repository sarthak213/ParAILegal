"""Dense (meaning-based) retrieval with a small local embedding model, run through ONNX (fastembed).

Document vectors are computed once per (model, corpus) and cached on disk; a query costs one
forward pass plus a NumPy dot product over ~5k vectors (about a millisecond).

Long chunks (some Constitution articles run to 11,000 words) are split into overlapping
windows, each embedded separately; a chunk scores as its best window. Without this, anything
past the model's input limit would be invisible to search.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

MODELS_DIR = Path(__file__).resolve().parents[2] / "models"


@dataclass(frozen=True)
class EmbedderSpec:
    name: str
    query_prefix: str = ""
    doc_prefix: str = ""
    max_words: int = 300  # window size; ~400 tokens, inside a 512-token limit


# Prefixes are the ones each model was trained with; leaving them out costs accuracy.
SPECS: dict[str, EmbedderSpec] = {
    "bge-small": EmbedderSpec("BAAI/bge-small-en-v1.5",
                              query_prefix="Represent this sentence for searching relevant passages: "),
    "bge-base": EmbedderSpec("BAAI/bge-base-en-v1.5",
                             query_prefix="Represent this sentence for searching relevant passages: "),
    "arctic-s": EmbedderSpec("snowflake/snowflake-arctic-embed-s",
                             query_prefix="Represent this sentence for searching relevant passages: "),
    "nomic-q": EmbedderSpec("nomic-ai/nomic-embed-text-v1.5-Q", query_prefix="search_query: ",
                            doc_prefix="search_document: ", max_words=1500),
}


def windows(text: str, size: int, stride_ratio: float = 0.8) -> list[str]:
    words = text.split()
    if len(words) <= size:
        return [text]
    stride = max(1, int(size * stride_ratio))
    return [" ".join(words[i:i + size]) for i in range(0, max(1, len(words) - size + stride), stride)]


class DenseIndex:
    def __init__(self, key: str, documents: list[str], batch_size: int = 16, threads: int | None = None) -> None:
        from fastembed import TextEmbedding

        self.spec = SPECS[key]
        # 4 threads by default: fast enough, and leaves the laptop usable (EMBED_THREADS overrides)
        threads = threads or int(os.environ.get("EMBED_THREADS", "4"))
        self.model = TextEmbedding(self.spec.name, cache_dir=str(MODELS_DIR / "fastembed"), threads=threads)
        pieces: list[str] = []
        owner: list[int] = []
        for i, doc in enumerate(documents):
            for piece in windows(doc, self.spec.max_words):
                pieces.append(self.spec.doc_prefix + piece)
                owner.append(i)
        self.owner = np.array(owner)
        self.n_docs = len(documents)
        self.vectors = self._load_or_embed(key, pieces, batch_size)

    def _load_or_embed(self, key: str, pieces: list[str], batch_size: int) -> np.ndarray:
        """Vectors for the pieces, cached per piece (by the hash of its text), so a corpus edit
        re-embeds only the pieces it changed, and an interrupted run resumes where it stopped."""
        path = MODELS_DIR / "embeddings" / key / "pieces.npz"
        cache: dict[bytes, np.ndarray] = {}
        if path.exists():
            with np.load(path) as stored:
                cache = dict(zip((bytes(h) for h in stored["hashes"]), stored["vectors"], strict=True))
        hashes = [hashlib.sha256(p.encode("utf-8")).digest()[:16] for p in pieces]
        todo = list(dict.fromkeys(h for h in hashes if h not in cache))
        if todo:
            text_of = {h: p for h, p in zip(hashes, pieces, strict=True)}
            step = 2048  # save every so often: a long run that stops loses little
            for start in range(0, len(todo), step):
                chunk = todo[start:start + step]
                vecs = np.array(list(self.model.embed([text_of[h] for h in chunk], batch_size=batch_size)),
                                dtype=np.float32)
                vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12
                cache.update(zip(chunk, vecs, strict=True))
                self._save(path, cache)
                print(f"  embedded {min(start + step, len(todo))}/{len(todo)} new pieces", flush=True)
        return np.stack([cache[h] for h in hashes])

    @staticmethod
    def _save(path: Path, cache: dict[bytes, np.ndarray]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp.npz")
        np.savez(tmp, hashes=np.array([np.frombuffer(h, dtype=np.uint8) for h in cache]),
                 vectors=np.stack(list(cache.values())))
        tmp.replace(path)

    def __call__(self, query: str, limit: int) -> list[tuple[int, float]]:
        q = np.array(next(iter(self.model.embed([self.spec.query_prefix + query]))), dtype=np.float32)
        q /= np.linalg.norm(q) + 1e-12
        sims = self.vectors @ q
        best = np.full(self.n_docs, -np.inf, dtype=np.float32)
        np.maximum.at(best, self.owner, sims)  # a chunk scores as its best window
        top = np.argsort(-best)[:limit]
        return [(int(i), float(best[i])) for i in top]
