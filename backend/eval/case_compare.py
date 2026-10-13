"""How the Case Builder's precedent comparisons hold up (app/case/precedents.py compare).

    python -m eval.case_compare [--limit 12] [--check] [--fresh] [--show]

For each fact pattern in eval/case_offences*.json: the top Supreme Court precedent for its expected
offences, compared by the Case Builder's model. The model's replies are kept in data/eval/
(compare_replies.jsonl, and compare_checks.jsonl for the point-by-point check), so later runs
check them again without the model (--fresh asks again). Reports the points kept and dropped;
--show prints every point, for reading against both texts: a slip is a point that says something
one of them does not.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
SETS = ["case_offences.json", "case_offences_heldout.json", "case_offences_final.json", "case_offences_fresh.json"]
REPLIES = HERE.parent / "data" / "eval" / "compare_replies.jsonl"
CHECKS = HERE.parent / "data" / "eval" / "compare_checks.jsonl"  # the check pass, by the points it checked


def load(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {r["key"]: r for r in rows}


def append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


async def main_async(args) -> int:
    from app.case import precedents
    from app.core.config import settings
    from app.search.service import SearchService

    service = SearchService(settings)
    await service.initialize()
    if service.judgments is None:
        print("The Supreme Court judgments are not installed")
        return 1
    cases = [c for s in SETS for c in json.loads((HERE / s).read_text(encoding="utf-8"))]
    # spread over the sets: every third case, so the run covers many kinds of offence
    cases = cases[::3][:args.limit]
    replies = {} if args.fresh else load(REPLIES)
    checks = {} if args.fresh else load(CHECKS)
    url = None
    totals = {"comparisons": 0, "points": 0, "kept": 0, "dropped_quotes_or_procedure": 0, "dropped_by_check": 0}
    for c in cases:
        found = precedents.find(c["facts"], c["expect"], c["acts"], service.judgments, service.engine,
                                service.history, k=1)
        if not found:
            print(f"{c['id']}: no precedent")
            continue
        record = service.judgments.record(found[0]["id"])
        key = f"{c['id']}|{record['id']}"
        if key not in replies:
            url = url or (await service.case_model()).base_url
            replies[key] = {"key": key, "reply": await precedents.compare_reply(c["facts"], record, url)}
            append(REPLIES, replies[key])
        raw = replies[key]["reply"]
        reviewed = precedents.review(raw, c["facts"], record)
        result = reviewed
        if args.check:
            points = [p["point"] for kind in ("similar", "different") for p in reviewed[kind]]
            check_key = f"{key}|{json.dumps(points, ensure_ascii=False)}"
            if points and check_key not in checks:
                url = url or (await service.case_model()).base_url
                checks[check_key] = {"key": check_key,
                                     "reply": await precedents.check_reply(reviewed, c["facts"], record, url)}
                append(CHECKS, checks[check_key])
            result = precedents.apply_checks(reviewed, checks.get(check_key, {}).get("reply", {}))
        n_raw = len(raw.get("similar", [])[:3]) + len(raw.get("different", [])[:3])
        n_reviewed = len(reviewed["similar"]) + len(reviewed["different"])
        n_kept = len(result["similar"]) + len(result["different"])
        totals["comparisons"] += 1
        totals["points"] += n_raw
        totals["kept"] += n_kept
        totals["dropped_quotes_or_procedure"] += n_raw - n_reviewed
        totals["dropped_by_check"] += n_reviewed - n_kept
        print(f"{c['id']:20} {record.get('title', '')[:60]:60} kept {n_kept} of {n_raw}", flush=True)
        if args.show:
            theirs, _ = precedents._their_text(record)
            print(f"  OUR FACTS: {c['facts']}\n  THEIRS: {' '.join(theirs.split())[:1500]}")
            for kind in ("similar", "different"):
                kept_points = {p["point"] for p in result[kind]}
                quotes_ok = {p["point"] for p in reviewed[kind]}
                for p in raw.get(kind, [])[:3]:
                    point = (p.get("point") or "").strip()
                    mark = ("kept" if point in kept_points else
                            "DROPPED by the check" if point in quotes_ok else "DROPPED: quotes or procedure")
                    print(f"    [{kind} {mark}] {point}\n        ours: {p.get('our_fact')!r}\n"
                          f"        theirs: {p.get('their_fact')!r}")
            print(f"    bearing: {result['bearing']}\n", flush=True)
    print(json.dumps(totals))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--check", action="store_true", help="with the model's point-by-point check (precedents.check_reply)")
    ap.add_argument("--fresh", action="store_true", help="ask the model again instead of reading kept replies")
    ap.add_argument("--show", action="store_true", help="print every point with both texts")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
