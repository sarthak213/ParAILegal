"""Build the judgments store (app/judgments/store.py) from data/judgments/<year>.jsonl.

    python -m scripts.build_judgments_index [--dense bge-small] [--threads 4]

Vectors are embedded in batches and saved as they go (data/judgments/index/vectors.partial.npz),
so a stopped run resumes; only judgments whose text changed are embedded again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np

from app.judgments.store import INDEX_DIR, JudgmentStore, embed_text
from app.search.dense import DenseIndex, windows

JUDGMENTS = INDEX_DIR.parent


def load_records(directory: Path) -> list[dict]:
    records, seen = [], set()
    for f in sorted(directory.glob("[0-9][0-9][0-9][0-9].jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if "error" in r or r["id"] in seen:
                continue
            seen.add(r["id"])
            records.append(r)
    records.sort(key=lambda r: (r.get("decided") or "", r["id"]))
    return records


def embed(records: list[dict], key: str, threads: int, out: Path) -> None:
    index = DenseIndex.__new__(DenseIndex)
    index._load_model(key, threads)
    pieces, owner = [], []
    for i, r in enumerate(records):
        # one window: title, catchline and the first holdings (about 300 words); ~9 windows a second on
        # 4 CPU threads, so 43,000 judgments take about 80 minutes
        for piece in windows(embed_text(r), index.spec.max_words)[:1]:
            pieces.append(index.spec.doc_prefix + piece)
            owner.append(i)
    hashes = [hashlib.sha256(p.encode("utf-8")).hexdigest()[:24] for p in pieces]
    partial = out.with_name("vectors.partial.npz")
    cache: dict[str, np.ndarray] = {}
    for path in (out, partial):
        if path.exists():
            with np.load(path) as v:
                if "hashes" in v:
                    cache.update(zip(v["hashes"].tolist(), v["vectors"], strict=True))
    todo = [i for i, h in enumerate(hashes) if h not in cache]
    print(f"{len(pieces)} pieces, {len(todo)} to embed", flush=True)
    started, step = time.time(), 1024
    for at in range(0, len(todo), step):
        batch = todo[at:at + step]
        vecs = np.array(list(index.model.embed([pieces[i] for i in batch], batch_size=16)), dtype=np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12
        cache.update(zip((hashes[i] for i in batch), vecs.astype(np.float16), strict=True))
        np.savez(partial, hashes=np.array(list(cache)), vectors=np.stack(list(cache.values())))
        done = at + len(batch)
        rate = done / (time.time() - started)
        print(f"  {done}/{len(todo)}  ({rate:.0f}/s, {(len(todo) - done) / rate / 60:.0f} min left)", flush=True)
    vectors = np.stack([cache[h] for h in hashes]).astype(np.float16)
    np.savez(out, vectors=vectors, owner=np.array(owner, dtype=np.int32), n_docs=len(records),
             hashes=np.array(hashes))
    partial.unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dense", default="bge-small", help='app/search/dense.py SPECS key; "" for none')
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args()
    records = load_records(JUDGMENTS)
    print(f"{len(records)} judgments", flush=True)
    JudgmentStore.build(records, INDEX_DIR / "judgments.sqlite")
    print(f"store -> {INDEX_DIR / 'judgments.sqlite'} ({(INDEX_DIR / 'judgments.sqlite').stat().st_size / 1e6:.0f} MB)")
    if args.dense:
        embed(records, args.dense, args.threads, INDEX_DIR / "vectors.npz")


if __name__ == "__main__":
    main()
