"""Cross-encoder reranking of the fused top candidates (small ONNX model via fastembed).

A cross-encoder reads the question and a provision together, so it judges relevance more
precisely than either retriever; it is too slow to run over the whole corpus, so it only
re-orders the top few dozen.
"""

from __future__ import annotations

from app.search.dense import MODELS_DIR

RERANKERS = {"minilm-l6": "Xenova/ms-marco-MiniLM-L-6-v2"}
THREADS = 4  # keep a laptop usable while it works


class Reranker:
    def __init__(self, key: str = "minilm-l6", max_words: int = 160) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self.model = TextCrossEncoder(RERANKERS[key], cache_dir=str(MODELS_DIR / "fastembed"), threads=THREADS)
        self.max_words = max_words

    def __call__(self, query: str, documents: list[str]) -> list[float]:
        docs = [" ".join(d.split()[: self.max_words]) for d in documents]
        return [float(s) for s in self.model.rerank(query, docs)]
