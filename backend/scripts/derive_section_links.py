"""Derive old -> new section links where no official table exists (the four Labour Codes).

    python scripts/derive_section_links.py --check            # accuracy against IPC -> BNS
    python scripts/derive_section_links.py --out links.json   # labour laws -> the Codes

Each section of an old Act is compared with every section of the Act that replaced it, by its
heading (fuzzy word overlap) and its wording (TF-IDF cosine). A link is kept only when the best
match is both strong and clearly ahead of the runner-up. These links are derived, not official:
build_corpus.py stores them apart from the official correspondence ("derived_links", with
their score), and --check measures them against the official IPC -> BNS table first.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.search.legal_data import IPC_TO_BNS, SPLIT_TARGETS, STOPWORDS  # noqa: E402

# Headings that say nothing about the subject: matched only to each other
GENERIC = re.compile(r"^(?:short title|definitions?|interpretation|power to make rules|repeal|savings?|"
                     r"power to remove difficulties|application of|act to have overriding|protection of action)",
                     re.IGNORECASE)


def load(statutes: Path, code: str) -> dict[str, dict]:
    """section number -> {title, text} for one Act (parts joined)."""
    sections: dict[str, dict] = {}
    for line in (statutes / f"{code}.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        s = sections.setdefault(r["section_number"], {"title": r["section_title"], "text": ""})
        s["text"] += " " + r["text"]
    return {k: v for k, v in sections.items() if k not in ("SCHEDULE", "FULL")}


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z]{3,}", text.lower())
    return [w[:-1] if w.endswith("s") and len(w) > 4 else w for w in words if w not in STOPWORDS]


def tfidf(docs: list[list[str]]) -> np.ndarray:
    vocab: dict[str, int] = {}
    for d in docs:
        for w in d:
            vocab.setdefault(w, len(vocab))
    df = Counter(w for d in docs for w in set(d))
    idf = {w: math.log((1 + len(docs)) / (1 + n)) + 1 for w, n in df.items()}
    m = np.zeros((len(docs), len(vocab)), dtype=np.float32)
    for i, d in enumerate(docs):
        for w, n in Counter(d).items():
            m[i, vocab[w]] = (1 + math.log(n)) * idf[w]
    m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
    return m


def derive(old: dict[str, dict], new: dict[str, dict], title_weight: float = 0.5,
           threshold: float = 0.45, margin: float = 0.05) -> dict[str, tuple[str, float]]:
    """old section -> (new section, score), for confident matches only."""
    old_keys, new_keys = list(old), list(new)
    docs = [tokens(old[k]["title"] * 2 + " " + old[k]["text"]) for k in old_keys] + \
           [tokens(new[k]["title"] * 2 + " " + new[k]["text"]) for k in new_keys]
    m = tfidf(docs)
    body = m[:len(old_keys)] @ m[len(old_keys):].T
    out: dict[str, tuple[str, float]] = {}
    for i, ok in enumerate(old_keys):
        ot = old[ok]["title"]
        if re.search(r"\[?(?:repealed|omitted)\b", ot, re.IGNORECASE) or len(old[ok]["text"].split()) < 8:
            continue
        scores = []
        for j, nk in enumerate(new_keys):
            nt = new[nk]["title"]
            if bool(GENERIC.match(ot)) != bool(GENERIC.match(nt)):
                continue
            title = fuzz.token_set_ratio(ot.lower(), nt.lower()) / 100
            scores.append((title_weight * title + (1 - title_weight) * float(body[i, j]), nk))
        if not scores:
            continue
        scores.sort(reverse=True)
        best, second = scores[0], scores[1] if len(scores) > 1 else (0.0, None)
        if best[0] >= threshold and best[0] - second[0] >= margin:
            out[ok] = (best[1], round(best[0], 3))
    return out


def check(statutes: Path) -> None:
    """Precision and coverage of derived IPC -> BNS links against the official table."""
    ipc, bns = load(statutes, "IPC"), load(statutes, "BNS")
    official = {old: {new} | {t.split()[1] for t in SPLIT_TARGETS.get(f"IPC {old}", [])}
                for old, new in IPC_TO_BNS.items() if old in ipc}
    print(f"{'title_w':>7} {'thresh':>6} {'margin':>6}  {'links':>5} {'precision':>9} {'coverage':>8}")
    for tw in (0.3, 0.5, 0.7):
        for th in (0.35, 0.45, 0.55, 0.65):
            for mg in (0.0, 0.05, 0.1):
                got = derive(ipc, bns, tw, th, mg)
                judged = [o for o in got if o in official]
                right = sum(got[o][0] in official[o] for o in judged)
                print(f"{tw:7} {th:6} {mg:6}  {len(judged):5} {100 * right / max(1, len(judged)):8.1f}% "
                      f"{100 * len(judged) / len(official):7.1f}%")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--statutes", type=Path, default=ROOT / "data" / "statutes")
    ap.add_argument("--check", action="store_true", help="measure against the official IPC -> BNS table")
    ap.add_argument("--out", type=Path, help="write derived links for every Act replaced by a Code")
    ap.add_argument("--title-weight", type=float, default=0.5)
    ap.add_argument("--threshold", type=float, default=0.45)
    ap.add_argument("--margin", type=float, default=0.05)
    args = ap.parse_args()
    if args.check:
        check(args.statutes)
        return 0

    acts = {}
    for path in args.statutes.glob("*.jsonl"):
        r = json.loads(path.open(encoding="utf-8").readline())
        acts[r["act_title"]] = r
    tables = []
    for title, r in sorted(acts.items()):
        new_title = r.get("replaced_by")
        if not new_title or new_title not in acts or r["act_code"] in ("IPC", "CRPC", "IEA") \
                or not re.search(r"\bCode\b", new_title):
            continue
        new_code = acts[new_title]["act_code"]
        got = derive(load(args.statutes, r["act_code"]), load(args.statutes, new_code),
                     args.title_weight, args.threshold, args.margin)
        tables.append({"old_act": title, "new_act": new_title, "derived": True,
                       "sections": [[o, n, s] for o, (n, s) in got.items()]})
        print(f"  {title} -> {new_title}: {len(got)} sections linked")
    args.out.write_text(json.dumps({"method": __doc__.split("\n")[0], "tables": tables}, indent=0,
                                   ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
