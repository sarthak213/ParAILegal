"""Build (or rebuild) the search data pack, app/search/pack.py, from the corpus JSONL.

    python -m scripts.build_pack            # rebuild only if stale
    python -m scripts.build_pack --force    # rebuild regardless

Search builds the pack on its own the first time it starts; this is for packaging the desktop
app, or to pay the one-off cost ahead of time.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="rebuild even if the pack is current")
    args = ap.parse_args()

    from app.core.config import settings
    from app.search.engine import SearchEngine

    pack_dir = Path(settings.PACK_DIR)
    if args.force:
        for f in pack_dir.glob("*"):
            f.unlink()
    t0 = time.perf_counter()
    engine = SearchEngine.from_corpus(settings, dense=settings.DENSE_MODEL or None)
    size = sum(f.stat().st_size for f in pack_dir.glob("*")) / 2**20
    print(f"Pack in {pack_dir}: {len(engine.chunks)} chunks, {size:.0f} MB, {time.perf_counter() - t0:.1f} s")


if __name__ == "__main__":
    main()
