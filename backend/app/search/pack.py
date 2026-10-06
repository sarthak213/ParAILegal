"""The data pack: the search index prebuilt, so the app starts in about two seconds.

Built from the corpus JSONL (data/statutes, data/constitution.jsonl, data/landmarks.jsonl) the
first time search starts, or by `python -m scripts.build_pack`, and loaded on every start after:

    data/pack/corpus.sqlite              FTS5 keyword index ("chunks"), the chunks as JSON
                                         ("chunk_data"), the typo-correction vocabulary ("vocab")
                                         and the manifest ("meta")
    data/pack/vectors-<model>.f16.npy    dense vectors, one per piece, already in chunk order
    data/pack/owner-<model>.npy          the chunk each piece belongs to

Rebuilt automatically when it is stale: the manifest stores a fingerprint of the source files
(names, sizes, modification times) and of the code that shapes the index. A desktop install
ships the pack without the JSONL; with no sources to compare against, the pack is trusted.

Chunks are stored as JSON, not pickle: a downloaded data pack must never be able to run code.
Vectors are stored as float16 (53 MB instead of 106): on the retrieval evaluation that moved one
dev question's supporting provision out of the top 10 (dev nDCG@10 -0.1) and changed nothing on
test. Search starts in about 2 s from the pack against about 7 s from the JSONL.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

PACK_VERSION = 1


@dataclass
class Pack:
    chunks: list[dict]
    db_path: Path
    vocabulary: dict[str, int]
    vectors: np.ndarray | None = None  # float32, for the requested dense model
    owner: np.ndarray | None = None


def sources(settings: Any) -> list[Path]:
    return [Path(settings.CONSTITUTION_FILE), *map(Path, settings.STATUTE_FILES), *map(Path, settings.JUDGEMENT_FILES)]


def fingerprint(settings: Any) -> str | None:
    """None when the corpus sources are not here (a desktop install): the pack is all there is."""
    files = [p for p in sources(settings) if p.exists()]
    if not files:
        return None
    from app.infrastructure.loaders import dataset_loader
    from app.search import bm25, engine, legal_data

    h = hashlib.sha256(f"pack {PACK_VERSION}".encode())
    for p in sorted(files):
        st = p.stat()
        h.update(f"{p.name} {st.st_size} {st.st_mtime_ns}".encode())
    # the code that turns the sources into chunks and index documents
    for code in (inspect.getsource(dataset_loader), inspect.getsource(engine.SearchEngine._index_doc),
                 inspect.getsource(engine.SearchEngine.load_chunks), inspect.getsource(engine._old_aliases),
                 inspect.getsource(legal_data), inspect.getsource(bm25.BM25Index.__init__),
                 inspect.getsource(bm25.BM25Index._vocabulary), bm25.TOKENIZER):
        h.update(code.encode())
    return h.hexdigest()


def _derived_embed_text(c: dict) -> str:
    return f"{(c.get('citation') or '').strip()}\n{(c.get('text') or '').strip()}"


def compact(c: dict) -> dict:
    """A chunk without the fields `expand` can rebuild: every statute's embedding text is its
    citation and text, and `full_text` almost always equals `text`. Halves the pack's chunks."""
    out = dict(c)
    if out.get("full_text") == out.get("text"):
        out.pop("full_text", None)
    if out.get("_embed_text") == _derived_embed_text(c):
        out.pop("_embed_text", None)
    return out


def expand(c: dict) -> dict:
    c.setdefault("full_text", c.get("text"))
    if "_embed_text" not in c:
        c["_embed_text"] = _derived_embed_text(c)
    return c


def _paths(pack_dir: Path, dense: str | None) -> tuple[Path, Path | None, Path | None]:
    db = pack_dir / "corpus.sqlite"
    if not dense:
        return db, None, None
    return db, pack_dir / f"vectors-{dense}.f16.npy", pack_dir / f"owner-{dense}.npy"


def load(pack_dir: Path, settings: Any, dense: str | None) -> Pack | None:
    """The pack, or None when it is missing, stale, or lacks vectors for the dense model."""
    db_path, vec_path, own_path = _paths(pack_dir, dense)
    if not db_path.exists():
        return None
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        meta = dict(con.execute("SELECT key, value FROM meta"))
        expected = fingerprint(settings)
        if int(meta.get("version", 0)) != PACK_VERSION or (expected and meta.get("fingerprint") != expected):
            return None
        # vectors count only if written with this index (a newer index may sit beside old vectors)
        if vec_path and not (meta.get(f"dense:{dense}") == meta.get("built")
                             and vec_path.exists() and own_path.exists()):
            return None
        chunks = [expand(c) for c in json.loads(con.execute("SELECT data FROM chunk_data").fetchone()[0])]
        vocabulary = dict(con.execute("SELECT word, n FROM vocab"))
    except sqlite3.Error:
        return None  # an old or damaged pack: rebuild
    finally:
        con.close()
    pack = Pack(chunks, db_path, vocabulary)
    if vec_path:
        pack.vectors = np.load(vec_path).astype(np.float32)
        pack.owner = np.load(own_path)
        if len(pack.vectors) != len(pack.owner) or int(pack.owner.max()) != len(chunks) - 1:
            return None
    return pack


def write(pack_dir: Path, settings: Any, engine: Any, dense_key: str | None) -> None:
    """Write the pack from a search engine built the slow way. Atomic: a crash leaves the old pack."""
    pack_dir.mkdir(parents=True, exist_ok=True)
    built = time.strftime("%Y-%m-%dT%H:%M:%S")
    db_path, vec_path, own_path = _paths(pack_dir, dense_key)
    dense = engine.dense if vec_path and hasattr(engine.dense, "vectors") else None
    if dense is not None:  # vectors first: the index that names them is written last
        for path, array in ((vec_path, dense.vectors.astype(np.float16)), (own_path, dense.owner.astype(np.int32))):
            part = path.with_suffix(".tmp.npy")
            np.save(part, array)
            part.replace(path)
    tmp = db_path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    engine.bm25.save(str(tmp))
    con = sqlite3.connect(tmp)
    try:
        con.execute("CREATE TABLE chunk_data (data TEXT)")
        con.execute("INSERT INTO chunk_data VALUES (?)",
                    (json.dumps([compact(c) for c in engine.chunks], ensure_ascii=False),))
        con.execute("CREATE TABLE vocab (word TEXT PRIMARY KEY, n INTEGER)")
        con.executemany("INSERT INTO vocab VALUES (?, ?)", engine.bm25.vocabulary.items())
        con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        meta = [("version", str(PACK_VERSION)), ("fingerprint", fingerprint(settings) or ""),
                ("built", built), ("chunks", str(len(engine.chunks)))]
        if dense is not None:
            meta.append((f"dense:{dense_key}", built))
        con.executemany("INSERT INTO meta VALUES (?, ?)", meta)
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()
    tmp.replace(db_path)
