"""Tune the fusion weights on the DEV split only, then report both splits.

    python -m eval.tune --dense arctic-s

Each retriever's ranked list is computed once per question; trying a weight setting is then
pure arithmetic, so a few thousand settings take seconds. The test split is never looked at
during the search, so its score stays an honest estimate.
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
from dataclasses import replace

from app.search.engine import SearchEngine, Weights, ref_of
from eval.retrieval import UNSCORED, load_questions, score_question

GRID = {
    "exact": [2.0, 4.0, 8.0],
    "case_name": [1.5, 3.0, 6.0],
    "bm25": [0.5, 0.75, 1.0, 1.5],
    "dense": [0.5, 1.0, 1.5, 2.0, 3.0],
    "domain_boost": [0.0, 0.15, 0.3],
}


def evaluate(engine: SearchEngine, comps: list, questions: list[dict], weights: Weights) -> dict:
    per_split: dict[str, list[float]] = {"dev": [], "test": []}
    per_cat: dict[str, list[float]] = {}
    for q, comp in zip(questions, comps, strict=True):
        refs = [ref_of(engine.chunks[i]) for i, _ in engine.fuse(comp, weights, k=10)]
        ndcg = score_question(q["relevant"], refs)["ndcg@10"]
        per_split[q["split"]].append(ndcg)
        per_cat.setdefault(f"{q['split']}:{q['category']}", []).append(ndcg)
    return {"dev": statistics.mean(per_split["dev"]), "test": statistics.mean(per_split["test"]),
            "cats": {c: statistics.mean(v) for c, v in per_cat.items()}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dense", default=None)
    args = parser.parse_args()

    engine = SearchEngine.from_corpus(dense=args.dense)
    questions = [q for q in load_questions() if q["category"] not in UNSCORED]
    comps = [engine.components(q["query"]) for q in questions]

    base = evaluate(engine, comps, questions, Weights())
    best_w, best = Weights(), base
    keys = list(GRID)
    for values in itertools.product(*GRID.values()):
        w = replace(Weights(), **dict(zip(keys, values, strict=True)))
        w.exact_uncertain = w.exact / 2
        result = evaluate(engine, comps, questions, w)
        if result["dev"] > best["dev"] + 1e-9:
            best_w, best = w, result

    chosen = {k: getattr(best_w, k) for k in keys}
    print(f"embedder: {args.dense}")
    print(f"default weights  dev {100 * base['dev']:.1f}  test {100 * base['test']:.1f}")
    print(f"tuned on dev     dev {100 * best['dev']:.1f}  test {100 * best['test']:.1f}   {json.dumps(chosen)}")
    print("per category (test split), default -> tuned:")
    for cat in sorted(c for c in best["cats"] if c.startswith("test:")):
        print(f"  {cat[5:]:16} {100 * base['cats'][cat]:5.1f} -> {100 * best['cats'][cat]:5.1f}")


if __name__ == "__main__":
    main()
