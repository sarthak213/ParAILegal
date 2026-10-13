"""How well the Case Builder finds the offences a set of facts discloses (app/case/builder.py offences).

    python -m eval.case_offences [--cases eval/case_offences_heldout.json] [--baseline] [--names] [--verbose]

eval/case_offences.json holds fact patterns, each with the acts as the facts step lists them and:
    expect   the offences a lawyer would charge first: each must be among the candidates
    also     offences that are reasonable to list as well (not counted against precision)

Reports, over all cases: recall (expected offences among the candidates), the rank of the first
expected offence, and precision (candidates that are expected or acceptable). No model is used:
the acts are given, so this measures the ranking alone. --names adds the offence names the Case
Builder's model gives each act (builder.name_offences), as the app does; the model runs once per act
and its names are kept in data/eval/case_offence_names.json for later runs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CASES = Path(__file__).with_name("case_offences.json")
NAMES_CACHE = Path(__file__).resolve().parents[1] / "data" / "eval" / "case_offence_names.json"


def evaluate(cases: list[dict], find, verbose: bool = False) -> dict:
    found_total = expected_total = relevant_total = shown_total = 0
    first_ranks = []
    for c in cases:
        refs = [o["ref"] for o in find(c["facts"], c["acts"])]
        got = [r for r in c["expect"] if r in refs]
        found_total += len(got)
        expected_total += len(c["expect"])
        ok = set(c["expect"]) | set(c.get("also", []))
        relevant_total += sum(r in ok for r in refs)
        shown_total += len(refs)
        first = next((i + 1 for i, r in enumerate(refs) if r in c["expect"]), None)
        first_ranks.append(first)
        if verbose or len(got) < len(c["expect"]):
            missing = [r for r in c["expect"] if r not in refs]
            print(f"{c['id']:20} first={first}  missing={missing}  top={refs[:5]}")
    return {
        "recall": round(found_total / expected_total, 3),
        "first_at_1": round(sum(r == 1 for r in first_ranks) / len(cases), 3),
        "first_in_3": round(sum(r is not None and r <= 3 for r in first_ranks) / len(cases), 3),
        "precision": round(relevant_total / max(shown_total, 1), 3),
        "cases": len(cases),
    }


async def model_names(service, cases: list[dict]) -> dict[str, list[str]]:
    """{act: names}, from the cache, the rest from the Case Builder's model (one call per case)."""
    from app.case import builder

    cache = json.loads(NAMES_CACHE.read_text(encoding="utf-8")) if NAMES_CACHE.exists() else {}
    todo = [c for c in cases if any(a not in cache for a in c["acts"])]
    if todo:
        url = (await service.case_model()).base_url
        for n, c in enumerate(todo, 1):
            named = await builder.name_offences(c["acts"], url, c["facts"])
            cache.update({a: named.get(a, []) for a in c["acts"]})
            print(f"  named {n}/{len(todo)} {c['id']}: {[named.get(a, []) for a in c['acts']]}", flush=True)
        NAMES_CACHE.parent.mkdir(parents=True, exist_ok=True)
        NAMES_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return cache


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--cases", type=Path, default=CASES, help="case_offences_heldout.json: the set not tuned on")
    ap.add_argument("--baseline", action="store_true", help="the corpus search instead of the offence index")
    ap.add_argument("--names", action="store_true", help="with the model's offence names for each act")
    args = ap.parse_args()
    import asyncio

    from app.case import builder
    from app.core.config import settings
    from app.search.service import SearchService

    service = SearchService(settings)
    asyncio.run(service.initialize())
    builder.USE_OFFENCE_INDEX = not args.baseline
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    names = asyncio.run(model_names(service, cases)) if args.names else {}
    result = evaluate(cases, lambda facts, acts: builder.offences(facts, service.engine, service.schedule, acts,
                                                                  names={a: names[a] for a in acts if a in names}),
                      args.verbose)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
