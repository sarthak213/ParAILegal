"""Put together the resources the desktop installer carries (docs/PACKAGING.md, steps 4-6).

    python -m scripts.stage_desktop [--out ../frontend/src-tauri/resources]
    python -m scripts.stage_desktop --no-backend --archive build-resources.zip

Into <out>:
    backend/                the PyInstaller build (dist/parailegal-backend: parailegal-backend.spec)
    data/pack/              the search pack (scripts/build_pack.py)
    data/amendments/        the amendment history ("law at the time", answers' history lines)
    models/engine/          llama.cpp, CPU and Vulkan builds
    models/fastembed/       the two search models only (bge-small, MiniLM reranker), links resolved
    THIRD_PARTY_NOTICES.txt the licences of llama.cpp and the models (backend/desktop/)

Not carried: the corpus sources (the pack is built from them), the answer and Case Builder models
and the judgments (downloaded on first run, app/setup/download.py).

--no-backend stages everything but the backend, and --archive zips it: the build resources the
release workflow (.github/workflows/release.yml) downloads, since the pack, the amendments and the
engine are built or fetched here and are not in the repository (scripts/publish_build_resources.py).

Checks, after copying, that the search models load from the copy with networking off.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEARCH_MODELS = ["models--Qdrant--bge-small-en-v1.5-onnx-Q", "models--Xenova--ms-marco-MiniLM-L-6-v2"]
NOTICES = ROOT / "desktop" / "THIRD_PARTY_NOTICES.txt"
PARTS = ("data/pack", "data/amendments", "models/engine", "models/fastembed")


def copy(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=False)  # links resolved: the install gets real files


def size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.is_dir() else path.stat().st_size


def stage_resources(src: Path, out: Path) -> None:
    """Everything the installer carries except the backend."""
    out.mkdir(parents=True, exist_ok=True)
    copy(src / "data" / "pack", out / "data" / "pack")
    copy(src / "data" / "amendments", out / "data" / "amendments")
    copy(src / "models" / "engine", out / "models" / "engine")
    cache = out / "models" / "fastembed"
    if cache.exists():
        shutil.rmtree(cache)
    for name in SEARCH_MODELS:
        # the snapshot only, its links resolved: the blobs/ beside it hold the same bytes again
        shutil.copytree(src / "models" / "fastembed" / name, cache / name, symlinks=False,
                        ignore=shutil.ignore_patterns("blobs"))
    shutil.copy2(NOTICES, out / NOTICES.name)


def archive(out: Path, zip_path: Path) -> None:
    """The staged resources (not the backend) in one zip, paths relative to <out>."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for part in (*PARTS, NOTICES.name):
            base = out / part
            for f in sorted([base] if base.is_file() else base.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(out).as_posix())
    print(f"  archive {zip_path} ({zip_path.stat().st_size / 1e6:.0f} MB)")


def check_offline(cache: Path) -> int:
    """The search models load from the copy alone, offline."""
    check = ("from fastembed import TextEmbedding; from fastembed.rerank.cross_encoder import TextCrossEncoder; "
             f"c = r'{cache}'; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir=c, local_files_only=True); "
             "TextCrossEncoder('Xenova/ms-marco-MiniLM-L-6-v2', cache_dir=c, local_files_only=True); print('search models load offline')")
    env = {**os.environ, "HF_HUB_OFFLINE": "1"}
    r = subprocess.run([sys.executable, "-c", check], capture_output=True, text=True, env=env)
    print("  " + (r.stdout.strip() or r.stderr.strip().splitlines()[-1]))
    return r.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=ROOT.parent / "frontend" / "src-tauri" / "resources")
    ap.add_argument("--backend", type=Path, default=ROOT / "dist" / "parailegal-backend",
                    help="the PyInstaller build")
    ap.add_argument("--source", type=Path, default=ROOT, help="the backend folder holding data/ and models/")
    ap.add_argument("--no-backend", action="store_true", help="stage everything but the backend")
    ap.add_argument("--archive", type=Path, help="also zip the staged resources (not the backend) to this file")
    args = ap.parse_args()
    out, src = args.out, args.source
    if not args.no_backend and not (args.backend / "parailegal-backend.exe").is_file():
        print(f"! no backend build at {args.backend}: run pyinstaller parailegal-backend.spec first")
        return 1
    stage_resources(src, out)
    parts = list(PARTS)
    if not args.no_backend:
        copy(args.backend, out / "backend")
        parts.insert(0, "backend")
    for part in parts:
        print(f"  {part:20} {size(out / part) / 1e6:7.0f} MB")
    print(f"  {'all':20} {size(out) / 1e6:7.0f} MB -> {out}")
    if args.archive:
        archive(out, args.archive)
    return check_offline(out / "models" / "fastembed")


if __name__ == "__main__":
    sys.exit(main())
