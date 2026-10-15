"""Publish the first-run downloads to Hugging Face and point the app's manifest at them.

    hf auth login                      # once, as the owner (a token with write access)
    python -m scripts.publish_downloads --owner <user-or-org> [--dry-run]

Two repositories under the owner:

    <owner>/parailegal-models          (model)    the answer and Case Builder models, Apache-2.0
    <owner>/parailegal-sc-judgments    (dataset)  the judgments index and citator, CC-BY-4.0

Each gets a card with its licence and, for the judgments, the attribution CC-BY-4.0 requires.
The files uploaded are the ones app/setup/manifest.json describes: each is checked against the
manifest's size and SHA-256 first, so the app's check after download passes. Then the manifest's
URLs are filled in ("https://huggingface.co/<repo>/resolve/main/<file>").

Needs huggingface_hub (Apache-2.0): a build-time tool, not part of the app.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "app" / "setup" / "manifest.json"

REPOS = {  # manifest item -> (repo name, repo type)
    "answer-model": ("parailegal-models", "model"),
    "case-model": ("parailegal-models", "model"),
    "judgments": ("parailegal-sc-judgments", "dataset"),
}

MODEL_CARD = """---
license: apache-2.0
base_model:
- Qwen/Qwen3.5-4B
- Qwen/Qwen3.5-9B
tags: [gguf, llama.cpp]
---
# ParAILegal models

The local models the ParAILegal desktop app downloads on first run, as GGUF files for llama.cpp
(Q4_K_M quantisation):

| File | Base model | Used for |
|---|---|---|
| Qwen3.5-4B-Q4_K_M.gguf | [Qwen/Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B) | answers |
| Qwen3.5-9B-Q4_K_M.gguf | [Qwen/Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B) | the Case Builder |

The Qwen3.5 models are by the Qwen team (Alibaba Cloud) and licensed under the Apache License 2.0;
these files are quantised copies, redistributed under the same licence. The app checks each file's
SHA-256 after download.
"""

DATASET_CARD = """---
license: cc-by-4.0
language: [en]
tags: [legal, india, supreme-court]
---
# ParAILegal: Supreme Court of India judgments index

The search index and citator the ParAILegal desktop app downloads on first run.

- `index/judgments.sqlite`: {n} English judgments of the Supreme Court of India, 1950-2025, with
  their headnotes, the provisions each cites, and a full-text index
- `index/vectors.npz`: sentence vectors of each judgment's catchline and holdings (bge-small)
- `citator.json`: how later judgments treated each one (followed, distinguished, overruled, ...)

**Source and attribution.** Built from the "Indian Supreme Court Judgments" dataset on the
Registry of Open Data on AWS (https://registry.opendata.aws/indian-supreme-court-judgments/),
licensed under the Creative Commons Attribution 4.0 International licence (CC-BY-4.0). Changes:
text extracted from the PDFs, cleaned, split into paragraphs, indexed; citations between
judgments resolved and classified by ParAILegal. This index is shared under the same licence.
"""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(1 << 20):
            h.update(block)
    return h.hexdigest()


def url(owner: str, item_id: str, path: str) -> tuple[str, str, str, str]:
    """(repo id, repo type, path in the repo, download URL) for a manifest file."""
    name, kind = REPOS[item_id]
    repo = f"{owner}/{name}"
    in_repo = Path(path).name if kind == "model" else path.removeprefix("data/judgments/")
    prefix = "" if kind == "model" else "datasets/"
    return repo, kind, in_repo, f"https://huggingface.co/{prefix}{repo}/resolve/main/{in_repo}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--owner", required=True, help="Hugging Face user or organisation")
    ap.add_argument("--home", type=Path, default=ROOT, help="where the files are (default: backend/)")
    ap.add_argument("--dry-run", action="store_true", help="check the files and print the plan; upload nothing")
    ap.add_argument("--ipv4", action="store_true",
                    help="connect over IPv4 only (a network whose IPv6 route to the Hub resets connections)")
    args = ap.parse_args()
    if args.ipv4:
        _ipv4_only()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    plan = []
    for item in manifest["items"]:
        for f in item["files"]:
            local = args.home / f["path"]
            if not local.is_file() or local.stat().st_size != f["size"]:
                print(f"! {local}: missing or not {f['size']} bytes")
                return 1
            plan.append((item["id"], f, local, *url(args.owner, item["id"], f["path"])))
    print("Checking SHA-256 of each file (a minute or two) ...")
    for _, f, local, *_ in plan:
        if sha256(local) != f["sha256"]:
            print(f"! {local}: SHA-256 differs from the manifest")
            return 1
    for item_id, f, local, repo, kind, in_repo, link in plan:
        print(f"{local.name:28} -> {kind} {repo}: {in_repo}")
    if args.dry_run:
        return 0

    from huggingface_hub import HfApi

    api = HfApi()
    n = _judgments_count(args.home)
    for repo, kind, card in sorted({(r, k, MODEL_CARD if k == "model" else DATASET_CARD.format(n=n))
                                    for _, _, _, r, k, _, _ in plan}):
        retrying(lambda: api.create_repo(repo, repo_type=kind, exist_ok=True), f"create {repo}")
        retrying(lambda: api.upload_file(path_or_fileobj=card.encode("utf-8"), path_in_repo="README.md", repo_id=repo,
                                         repo_type=kind, commit_message="Card: licence and attribution"),
                 f"card of {repo}")
    for item_id, f, local, repo, kind, in_repo, link in plan:
        if already_there(api, repo, kind, in_repo, f["sha256"]):
            print(f"{local.name}: already uploaded", flush=True)
        else:
            print(f"uploading {local.name} ({f['size'] / 1e9:.1f} GB) ...", flush=True)
            retrying(lambda: api.upload_file(path_or_fileobj=str(local), path_in_repo=in_repo, repo_id=repo,
                                             repo_type=kind, commit_message=in_repo), local.name)
        f["url"] = link
        # written after each file, so a run that stops keeps the URLs of what it finished
        MANIFEST.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Manifest updated: {MANIFEST}")
    return 0


def _ipv4_only() -> None:
    """Resolve names to IPv4 addresses only, for this process's Python connections."""
    import socket

    resolve = socket.getaddrinfo

    def ipv4(host, port, family=0, *rest, **kw):
        return resolve(host, port, socket.AF_INET, *rest, **kw)

    socket.getaddrinfo = ipv4


# tries per step in one run. The Hub's upload client (hf_xet) stays failed after a network error
# for the rest of the process, so a flaky line is better served by rerunning the script, which
# skips what is already up: PUBLISH_TRIES=2 in a loop
TRIES = int(os.environ.get("PUBLISH_TRIES", "12"))


def retrying(step, what: str):
    """A step that a dropped connection must not end (this line resets many connections): tried
    again with a growing pause. A large upload resumes: the Hub keeps the chunks it already has."""
    import time

    for attempt in range(1, TRIES + 1):
        try:
            return step()
        except Exception as e:  # connection resets, timeouts, 5xx: all worth another try
            if attempt == TRIES:
                raise
            wait = min(10 * attempt, 120)
            print(f"  {what}: {type(e).__name__}: {str(e)[:300]}; trying again in {wait} s ({attempt}/{TRIES})",
                  flush=True)
            time.sleep(wait)


def already_there(api, repo: str, kind: str, in_repo: str, sha: str) -> bool:
    """The file is in the repo with this SHA-256 (a rerun skips it)."""
    try:
        info = retrying(lambda: api.get_paths_info(repo, [in_repo], repo_type=kind, expand=True), f"look up {in_repo}")
    except Exception:
        return False
    return bool(info) and getattr(getattr(info[0], "lfs", None), "sha256", None) == sha


def _judgments_count(home: Path) -> str:
    import sqlite3

    db = sqlite3.connect(f"file:{home / 'data/judgments/index/judgments.sqlite'}?mode=ro", uri=True)
    return f"{db.execute('SELECT COUNT(*) FROM judgments').fetchone()[0]:,}"


if __name__ == "__main__":
    sys.exit(main())
