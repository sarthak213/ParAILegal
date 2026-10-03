"""
ingest.py
─────────
One-time script to embed all JSONL corpus data and upload to Qdrant.

Run this ONCE before deploying. After ingestion, the Qdrant Cloud
collection persists permanently — the deployed app just queries it.

Usage:
    python ingest.py                    # ingest all corpora
    python ingest.py --corpus constitution
    python ingest.py --corpus statutes
    python ingest.py --corpus judgements
    python ingest.py --force            # re-ingest even if already uploaded

What it does:
    1. Loads JSONL files via dataset_loader.py (same as before)
    2. Embeds texts via HuggingFace BGE-M3 API in batches
    3. Uploads vectors + metadata to Qdrant Cloud

Run from the project root:
    cd ParAILegal
    python ingest.py

Estimated time (HF free tier, ~32 texts/batch):
    Constitution : ~500 chunks  → ~16 batches  → ~3 min
    Statutes     : ~12000 chunks → ~375 batches → ~60 min
    Judgements   : ~26 chunks   → ~1 batch     → ~1 min

HF free tier allows ~1000 requests/day. If you hit rate limits,
the script will pause and retry automatically. Run it overnight
if needed — it resumes from where it left off (checks Qdrant first).
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from app.core.config import settings
from app.infrastructure.qdrant.qdrant_index import QdrantIndex
from app.infrastructure.llm.model_loader import ModelLoader
from app.infrastructure.loaders.dataset_loader import (
    ConstitutionLoader,
    StatuteLoader,
    JudgementLoader,
)


def ingest_corpus(corpus: str, force: bool = False) -> None:
    """Embed and upload one corpus to Qdrant."""

    index = QdrantIndex(settings, corpus)

    # Skip if already ingested (unless force)
    if not force and index.is_index_available(""):
        count = index.size
        print(f"✅ {corpus.title()}: already ingested ({count} vectors) — skipping")
        print(f"   Use --force to re-ingest")
        return

    # Load JSONL
    print(f"\n{'='*60}")
    print(f"Ingesting: {corpus.upper()}")
    print(f"{'='*60}")

    if corpus == "constitution":
        texts, metadata = ConstitutionLoader(settings).load_dataset()
    elif corpus == "statutes":
        texts, metadata = StatuteLoader(settings).load_dataset()
    elif corpus == "judgements":
        texts, metadata = JudgementLoader(settings).load_dataset()
    else:
        print(f"Unknown corpus: {corpus}")
        return

    if not texts:
        print(f"  No data found for {corpus} — check your data files")
        return

    print(f"  {len(texts)} chunks to embed and upload")

    # Embed via HuggingFace BGE-M3
    loader = ModelLoader(settings)
    print(f"  Embedding via HuggingFace BGE-M3...")
    embeddings = loader.generate_embeddings(
        texts,
        batch_size=settings.BATCH_SIZE,
        show_progress_bar=True,
    )

    print(f"  Embedding complete: {embeddings.shape}")

    # Upload to Qdrant
    print(f"  Uploading to Qdrant...")
    index.build_index(embeddings, texts, metadata)

    print(f"\n✅ {corpus.title()} ingestion complete: {len(texts)} vectors in Qdrant")


async def verify_apis() -> None:
    """Ping HuggingFace and Qdrant before starting ingestion."""
    loader = ModelLoader(settings)
    await loader.load_models()
    await loader.aclose()


def main():
    parser = argparse.ArgumentParser(
        description="Ingest ParAILegal corpus into Qdrant"
    )
    parser.add_argument(
        "--corpus",
        choices=["constitution", "statutes", "judgements", "all"],
        default="all",
        help="Which corpus to ingest (default: all)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-ingest even if vectors already exist in Qdrant",
    )
    args = parser.parse_args()

    print("ParAILegal Ingestion Script")
    print(f"Qdrant: {settings.QDRANT_URL}")
    print(f"Collection: {settings.QDRANT_COLLECTION}")
    print()

    # Verify API connectivity first
    print("Verifying API connectivity...")
    asyncio.run(verify_apis())
    print()

    # Run ingestion
    corpora = (
        ["constitution", "statutes", "judgements"]
        if args.corpus == "all"
        else [args.corpus]
    )

    for corpus in corpora:
        ingest_corpus(corpus, force=args.force)

    print("\n" + "="*60)
    print("Ingestion complete. Your Qdrant collection is ready.")
    print("You can now deploy the app — it will query Qdrant on startup.")
    print("="*60)


if __name__ == "__main__":
    main()