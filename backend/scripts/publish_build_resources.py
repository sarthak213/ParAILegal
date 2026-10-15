"""Publish the desktop build resources for the release workflow, and record where they are.

    python -m scripts.stage_desktop --no-backend --out <tmp> --archive build-resources.zip
    python -m scripts.publish_build_resources build-resources.zip --owner <user-or-org> [--ipv4]

The release workflow (.github/workflows/release.yml) builds the installer on GitHub's machines,
which have the repository but not what it is built from here: the search pack, the amendments,
llama.cpp and the search models (scripts/stage_desktop.py --no-backend). This uploads that archive
to <owner>/parailegal-build-resources (a dataset repository: it is what the public installer already
carries, with THIRD_PARTY_NOTICES.txt) under a name with its SHA-256, so each version stays
reachable, and writes backend/desktop/build-resources.json: {url, size, sha256}. Commit that file:
the workflow downloads from its URL and checks the SHA-256.

Run it again after rebuilding the corpus or the pack (scripts/build_corpus.py, build_pack.py).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from scripts.publish_downloads import _ipv4_only, retrying

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "desktop" / "build-resources.json"
REPO = "parailegal-build-resources"

CARD = """---
license: other
tags: [legal, india, build-resources]
---
# ParAILegal: desktop build resources

What the ParAILegal Windows installer carries besides the app, for the release workflow that builds
it (https://github.com/sarthak213/ParAILegal/blob/master/.github/workflows/release.yml): the search
pack built from India Code and the Constitution of India, the amendment history, the llama.cpp
engine (CPU and Vulkan) and the two search models.

Each component keeps its own licence: see THIRD_PARTY_NOTICES.txt inside the archive (llama.cpp:
MIT; bge-small-en-v1.5: MIT; ms-marco-MiniLM-L-6-v2: Apache-2.0). ParAILegal itself is AGPL-3.0.
"""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("archive", type=Path, help="from scripts/stage_desktop.py --no-backend --archive")
    ap.add_argument("--owner", required=True, help="Hugging Face user or organisation")
    ap.add_argument("--ipv4", action="store_true", help="connect over IPv4 only")
    args = ap.parse_args()
    if args.ipv4:
        _ipv4_only()
    digest, size = sha256(args.archive), args.archive.stat().st_size
    name = f"build-resources-{digest[:12]}.zip"
    repo = f"{args.owner}/{REPO}"
    url = f"https://huggingface.co/datasets/{repo}/resolve/main/{name}"

    from huggingface_hub import HfApi

    api = HfApi()
    retrying(lambda: api.create_repo(repo, repo_type="dataset", exist_ok=True), f"create {repo}")
    retrying(lambda: api.upload_file(path_or_fileobj=CARD.encode("utf-8"), path_in_repo="README.md", repo_id=repo,
                                     repo_type="dataset", commit_message="Card"), "card")
    print(f"uploading {name} ({size / 1e6:.0f} MB) ...", flush=True)
    retrying(lambda: api.upload_file(path_or_fileobj=str(args.archive), path_in_repo=name, repo_id=repo,
                                     repo_type="dataset", commit_message=name), name)
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    RECORD.write_text(json.dumps({"url": url, "size": size, "sha256": digest}, indent=1) + "\n", encoding="utf-8")
    print(f"{RECORD} -> {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
