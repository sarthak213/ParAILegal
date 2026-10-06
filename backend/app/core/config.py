from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
MODELS_DIR = BASE_DIR / "models"
STATUTES_DIR = DATA_DIR / "statutes"


class Settings(BaseSettings):

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    # ── v1 cloud services ─────────────────────────────────────────────
    # Used only when SEARCH_ENGINE="v1"; v2 runs offline and needs none of these keys.
    # Sarvam: answer generation
    SARVAM_API_KEY: str = ""
    SARVAM_MODEL:   str = "sarvam-105b"

    # Groq: query rewriter
    GROQ_API_KEY: str = ""
    GROQ_MODEL:   str = "llama-3.1-8b-instant"

    # Cohere: embeddings + reranking
    COHERE_API_KEY:       str = ""
    COHERE_EMBED_MODEL:   str = "embed-multilingual-v3.0"
    COHERE_RERANK_MODEL:  str = "rerank-multilingual-v3.0"

    # Qdrant: vector database
    QDRANT_URL:        str = ""
    QDRANT_API_KEY:    str = ""
    QDRANT_COLLECTION: str = "ParAILegal"

    # ── Retrieval ─────────────────────────────────────────────────────
    # ── Search engine ─────────────────────────────────────────────────
    # "v2": local corpus with BM25 + dense + reranker (app/search); "v1": Qdrant + Cohere + Groq
    SEARCH_ENGINE:       str   = "v2"
    DENSE_MODEL:         str   = "bge-small"     # app/search/dense.py SPECS key; "" for none
    RERANK_MODEL:        str   = "minilm-l6"     # app/search/rerank.py key; "" for none

    # ── Local answer model (llama.cpp's llama-server, app/llm/server.py) ──
    # No model file -> answers are written by code from the evidence (sources only)
    LLM_ENGINE_DIR:      Path  = MODELS_DIR / "engine"  # llama.cpp builds in cpu/ and vulkan/
    LLM_DEVICE:          str   = "auto"   # auto (GPU, then CPU), gpu or cpu: see app/llm/server.py
    LLM_MODEL_PATH:      Path  = MODELS_DIR / "llm" / "Qwen3.5-4B-Q4_K_M.gguf"
    LLM_THREADS:         int   = 6      # generation threads: about half the cores, to stay cool
    LLM_BATCH_THREADS:   int   = 8      # prompt-processing threads
    LLM_CTX:             int   = 8192
    LLM_IDLE_UNLOAD_S:   float = 600    # stop the model after 10 idle minutes, freeing ~3 GB
    LLM_PRELOAD:         bool  = False  # load the model at start-up instead of on the first answer

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
    # Built from the Legislative Department's official PDF by scripts/build_constitution.py
    CONSTITUTION_FILE: Path = DATA_DIR / "constitution.jsonl"

    # One JSONL per Act, built from India Code by scripts/build_corpus.py
    STATUTE_FILES: list[Path] = sorted(STATUTES_DIR.glob("*.jsonl")) if STATUTES_DIR.exists() else []

    # The prebuilt search index (app/search/pack.py): built from the corpus files on first start
    PACK_DIR: Path = DATA_DIR / "pack"

    JUDGEMENT_FILES: list[Path] = [
        DATA_DIR / "landmarks.jsonl",
    ]


settings = Settings()