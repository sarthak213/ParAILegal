from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"


class Settings(BaseSettings):

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    # ── Sarvam (answer generation — unchanged) ────────────────────────
    SARVAM_API_KEY: str
    SARVAM_MODEL:   str = "sarvam-105b"

    # ── Groq (query rewriter — Llama 3.1 8B, free tier) ──────────────
    GROQ_API_KEY: str
    GROQ_MODEL:   str = "llama-3.1-8b-instant"

    # ── Cohere (embeddings + reranking — free trial tier) ─────────────
    # embed-multilingual-v3.0: 1024-dim, same as BGE-M3, supports 100+ languages
    # rerank-multilingual-v3.0: added later for retrieval quality improvement
    COHERE_API_KEY:       str
    COHERE_EMBED_MODEL:   str = "embed-multilingual-v3.0"
    COHERE_RERANK_MODEL:  str = "rerank-multilingual-v3.0"

    # ── Qdrant (vector database — cloud hosted) ───────────────────────
    QDRANT_URL:        str
    QDRANT_API_KEY:    str
    QDRANT_COLLECTION: str = "ParAILegal"

    # ── Retrieval ─────────────────────────────────────────────────────
    TOP_K_SEARCH:        int   = 15
    TOP_K_ANSWER:        int   = 5
    TEMPERATURE_REWRITE: float = 0.1
    TEMPERATURE_ANSWER:  float = 0.2
    MIN_SCORE_THRESHOLD: float = 0.1
    BATCH_SIZE:          int   = 32   # Cohere: 32 chunks/batch with 15s sleep = safe under trial limits

    # ── Retry ─────────────────────────────────────────────────────────
    MAX_RETRIES: int   = 3
    RETRY_DELAY: float = 1.0

    # ── Data paths (used only during ingestion, not at query time) ────
    CONSTITUTION_FILE: Path = DATA_DIR / "constitution_final.jsonl"

    STATUTE_FILES: list[Path] = [
        DATA_DIR / "bns_clean.jsonl",
        DATA_DIR / "bnss_clean.jsonl",
        DATA_DIR / "bsa_clean.jsonl",
    ]

    JUDGEMENT_FILES: list[Path] = [
        DATA_DIR / "landmarks.jsonl",
    ]


settings = Settings()