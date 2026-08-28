"""
model_loader.py
───────────────
Cloud API inference — replaces LM Studio entirely.

Embeddings  → Cohere API (embed-multilingual-v3.0, 1024-dim)
Rewriter    → Groq API (Llama 3.1 8B Instant, free tier)

Why Cohere for embeddings
──────────────────────────
- embed-multilingual-v3.0 produces 1024-dim vectors (same as BGE-M3)
- Supports Hindi, Tamil, and other Indian languages — future-proof
  for when the corpus expands beyond English legal text
- Clean Python SDK, no URL fiddling
- Rerank model (rerank-multilingual-v3.0) uses the same API key —
  when we add reranking later, zero new dependencies needed
- Free trial: 1,000 calls/month across all endpoints

Trial tier limits
──────────────────
Embed:  100 req/min, 1,000 calls/month total
Rerank: 10 req/min,  1,000 calls/month total (shared)

At 2 embed calls per user query: ~500 queries/month on free tier.
Sufficient for POC. Upgrade to production key when users arrive.

Ingestion cost (one-time, does NOT count against monthly limit
since ingestion uses the same trial key but is a one-time batch):
~12,000 statute chunks / 96 per batch = ~125 embed requests
~500 constitution chunks / 96 per batch = ~6 requests
~26 judgement chunks / 96 per batch = ~1 request
Total: ~132 requests

Async boundary
──────────────
load_models()         → async (ping checks on startup)
generate_embeddings() → sync, called via asyncio.to_thread
aclose()              → async (drains httpx pool for Groq)
"""

import asyncio
import numpy as np
import httpx
import cohere
from typing import List

from app.core.config import Settings

_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


class ModelLoader:

    def __init__(self, settings: Settings):
        self.settings      = settings
        self.models_loaded = False
        # Cohere sync client — used in generate_embeddings (sync thread)
        self._cohere = cohere.Client(api_key=settings.COHERE_API_KEY)
        # httpx async client — used for Groq ping on startup
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ── Startup ──────────────────────────────────────────────────────

    async def load_models(self) -> None:
        """
        Ping Cohere and Groq to verify keys are valid and reachable.
        No model loading needed — cloud APIs are always warm.
        """
        print("Verifying cloud API connectivity...")
        await self._ping_cohere()
        await self._ping_groq()
        self.models_loaded = True
        print("✅ Cloud APIs ready")
        print(f"   Embedder : Cohere {self.settings.COHERE_EMBED_MODEL}")
        print(f"   Rewriter : Groq {self.settings.GROQ_MODEL}")
        print(f"   Answerer : Sarvam {self.settings.SARVAM_MODEL}")

    async def _ping_cohere(self) -> None:
        """Verify Cohere API key with a minimal single-text embed call."""
        try:
            response = await asyncio.to_thread(
                self._cohere.embed,
                texts=["ping"],
                model=self.settings.COHERE_EMBED_MODEL,
                input_type="search_document",
                embedding_types=["float"],
            )
            dim = len(response.embeddings.float[0])
            print(f"  ✅ Cohere {self.settings.COHERE_EMBED_MODEL} OK (dim={dim})")
        except cohere.errors.UnauthorizedError:
            raise RuntimeError(
                "Cohere API key invalid — check COHERE_API_KEY in .env"
            )
        except Exception as e:
            raise RuntimeError(f"Cohere ping failed: {e}")

    async def _ping_groq(self) -> None:
        """Verify Groq API key with a minimal chat completion call."""
        try:
            resp = await self._http.post(
                _GROQ_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {self.settings.GROQ_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":      self.settings.GROQ_MODEL,
                    "messages":   [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
            )
            if resp.status_code == 200:
                print(f"  ✅ Groq {self.settings.GROQ_MODEL} OK")
            elif resp.status_code == 401:
                raise RuntimeError(
                    "Groq API key invalid — check GROQ_API_KEY in .env"
                )
            else:
                raise RuntimeError(
                    f"Groq ping failed: HTTP {resp.status_code} {resp.text[:100]}"
                )
        except httpx.ConnectError:
            raise RuntimeError("Cannot reach Groq API — check network")

    # ── Embedding ─────────────────────────────────────────────────────

    def generate_embeddings(
        self,
        texts: List[str],
        batch_size: int = 32,
        show_progress_bar: bool = True,
        input_type: str = "search_document",
    ) -> np.ndarray:
        """
        Generate L2-normalised float32 embeddings via Cohere Embed API.

        Cohere distinguishes input_type for asymmetric retrieval:
            "search_document" — for corpus ingestion
            "search_query"    — for user queries at search time

        Always use generate_query_embedding() for user queries.
        This method defaults to "search_document" for ingestion.

        Batch size 96: Cohere's maximum texts per embed request.
        Deliberately synchronous — called via asyncio.to_thread.
        """
        batches = [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]

        if show_progress_bar:
            try:
                from tqdm import tqdm
                batches = tqdm(batches, desc="Embedding")
            except ImportError:
                pass

        all_embeddings = []
        import time

        for i, batch in enumerate(batches):
            # Cohere trial: 100,000 tokens/min limit.
            # Legal text averages ~150 tokens/chunk × 96 chunks = ~14,400 tokens/batch.
            # Sleep 15s between batches → ~57,600 tokens/min — safely under limit.
            # Adds ~2-3 min to total ingestion time, eliminates 429 errors.
            if i > 0:
                time.sleep(15)

            try:
                response = self._cohere.embed(
                    texts=batch,
                    model=self.settings.COHERE_EMBED_MODEL,
                    input_type=input_type,
                    embedding_types=["float"],
                )
            except Exception as e:
                if "429" in str(e) or "rate limit" in str(e).lower():
                    print(f"\n  Rate limit hit — waiting 60s before retry...")
                    time.sleep(60)
                    response = self._cohere.embed(
                        texts=batch,
                        model=self.settings.COHERE_EMBED_MODEL,
                        input_type=input_type,
                        embedding_types=["float"],
                    )
                else:
                    raise

            batch_embeddings = np.array(
                response.embeddings.float, dtype="float32"
            )
            all_embeddings.append(batch_embeddings)

        embeddings = np.vstack(all_embeddings)

        # L2-normalise so cosine similarity = dot product
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        return (embeddings / norms).astype("float32")

    def generate_query_embedding(self, text: str) -> np.ndarray:
        """
        Embed a single user query with input_type='search_query'.

        Cohere's asymmetric embedding means query vectors and document
        vectors are in the same space but optimised differently.
        Always use this for user queries — never generate_embeddings().
        """
        return self.generate_embeddings(
            [text],
            batch_size=1,
            show_progress_bar=False,
            input_type="search_query",
        )

    # ── Interface compatibility ────────────────────────────────────────

    def get_embedding_model(self):
        return self

    def get_reranker_model(self):
        return self