"""Old <-> new section links in retrieval and in the evidence the model reads.

    python -m eval.links [--per-code 25]

Questions are made from the links scripts/link_new_codes.py added (not the well-known ones in
app/search/legal_data.py), both ways:

    forward   "Which section of the BNSS replaced section 145 of the CrPC?"   needs BNSS 164
    backward  "What did section 164 of the BNSS replace in the CrPC?"         needs CrPC 145

For each: is the needed section in the top 5 search results, and is it given to the answer model,
as a source or named in a source's context line ("Formerly Section 145 of the CrPC", app/answer/
context.py)? A fixed sample (seeded), so runs compare.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAIRS = [("IPC", "BNS", "the IPC", "the BNS"), ("CRPC", "BNSS", "the CrPC", "the BNSS"),
         ("IEA", "BSA", "the Indian Evidence Act", "the BSA")]


def questions(statutes: Path, per_code: int, seed: int = 11) -> list[dict]:
    from app.search.legal_data import OLD_CODES

    rng = random.Random(seed)
    out = []
    for old, new, old_name, new_name in PAIRS:
        hand = OLD_CODES[old][1]
        links = []
        for line in (statutes / f"{old.lower()}.jsonl").open(encoding="utf-8"):
            r = json.loads(line)
            sec = r["section_number"]
            target = next((t for t in r.get("corresponds_to") or [] if t.startswith(new + " ")), None)
            if target and sec not in hand and (sec, target) not in links:
                links.append((sec, target))
        for sec, target in rng.sample(links, min(per_code, len(links))):
            n = target.split()[1]
            out.append({"dir": "forward", "code": old, "query": f"Which section of {new_name} replaced section {sec} of {old_name}?",
                        "need": target})
            out.append({"dir": "backward", "code": old, "query": f"What did section {n} of {new_name} replace in {old_name}?",
                        "need": f"{old} {sec}"})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-code", type=int, default=25)
    ap.add_argument("--statutes", type=Path, default=ROOT / "data" / "statutes",
                    help="the corpus the questions are made from (the linked one)")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--context", action="store_true", help="with the amendment history and judgments notes")
    args = ap.parse_args()
    from app.answer.context import Context
    from app.answer.evidence import assemble
    from app.answer.sources_only import heading
    from app.judgments.law_at_time import History
    from app.judgments.store import JudgmentStore
    from app.search.engine import SearchEngine

    engine = SearchEngine.from_corpus(dense="bge-small", rerank="minilm-l6")
    context = Context(History(), JudgmentStore.open(dense_key=None)) if args.context else None
    qs = questions(args.statutes, args.per_code)
    tally: Counter = Counter()
    for q in qs:
        hits = engine.search(q["query"], k=10)
        refs = [h["_ref"] for h in hits]
        evidence = assemble(hits[:5], engine, context)
        need = engine.provision(q["need"])[0]
        named, number = heading(need), q["need"].rpartition(" ")[2]
        act = need.get("act_title") or need.get("document_title") or ""
        top5 = q["need"] in refs[:5]
        # given as a source, or named in a source's context line: by heading, or by number in the
        # list of a merged section ("Formerly Sections 499, 500, 501 and 502 of The Indian Penal Code")
        given = any(e.ref == q["need"] or named in e.context()
                    or (act and act in e.context() and re.search(r"Sections [^;]*\b" + re.escape(number) + r"\b", e.context()))
                    for e in evidence)
        key = (q["code"], q["dir"])
        tally[key + ("n",)] += 1
        tally[key + ("top5",)] += top5
        tally[key + ("evidence",)] += given
        if args.show and not given:
            print(f"  MISS {q['query']} need {q['need']} | top5 {refs[:5]} | evidence {[e.ref for e in evidence]}")
    print(f"{'code':6} {'direction':9} {'n':>3} {'in top 5':>9} {'given':>12}")
    for old, *_ in PAIRS:
        for d in ("forward", "backward"):
            n = tally[(old, d, "n")]
            print(f"{old:6} {d:9} {n:3} {100 * tally[(old, d, 'top5')] / n:8.0f}% {100 * tally[(old, d, 'evidence')] / n:11.0f}%")
    total = sum(tally[(o, d, 'n')] for o, *_ in PAIRS for d in ("forward", "backward"))
    print(f"all: in top 5 {100 * sum(v for k, v in tally.items() if k[2] == 'top5') / total:.0f}%, "
          f"in evidence {100 * sum(v for k, v in tally.items() if k[2] == 'evidence') / total:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
