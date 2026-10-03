"""Retrieval evaluation: how well does a retriever rank the provisions that answer each question?

    python -m eval.retrieval run --target http://127.0.0.1:8000 --name v1-baseline
    python -m eval.retrieval compare v1-baseline v2-bm25

Metrics (k = 10 results per question; no_answer and corpus_gap questions are excluded):

    hit@5 / hit@10   an essential provision (grade 2) is in the top 5 / 10
    recall@5 / @10   share of all relevant provisions (grade >= 1) found in the top 5 / 10
    mrr@10           1 / rank of the first essential provision
    ndcg@10          graded ranking quality (gain 2^grade - 1), each provision counted once

Results go to eval/results/<name>.json. `compare` prints the deltas and applies the gate from
docs/RAG-v2.md: no category may drop by more than 2 points on nDCG@10.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
QUESTIONS = HERE / "questions.jsonl"
RESULTS = HERE / "results"
K = 10
UNSCORED = {"no_answer", "corpus_gap"}
GATE_DROP = 0.02

Retriever = Callable[[str, int], list[dict]]  # (query, k) -> hits with "ref" and "score"


# ── Turning a hit into a citation ref ───────────────────────────────────


def ref_of(hit: dict) -> str | None:
    """'BNS 103', 'ART 21', 'CASE maneka_gandhi' — the same scheme as questions.jsonl."""
    source = (hit.get("source_type") or "").lower()
    chunk_id = hit.get("chunk_id") or ""
    section = str(hit.get("section") or "").strip()
    if source == "constitution":
        return f"ART {section}" if section else None
    if source in ("judgement", "judgements"):
        return "CASE " + re.sub(r"_(ratio|held)_\d+$", "", chunk_id) if chunk_id else None
    act = chunk_id.split("_", 1)[0].upper()
    if act in ("BNS", "BNSS", "BSA") and section:
        return f"{act} {section}"
    return None


# ── Retrievers ──────────────────────────────────────────────────────────


def http_retriever(base_url: str) -> Retriever:
    """The v1 API: POST /api/v1/search."""
    import httpx

    client = httpx.Client(base_url=base_url, timeout=120)

    def search(query: str, k: int) -> list[dict]:
        resp = client.post("/api/v1/search", json={"query": query, "k": k})
        resp.raise_for_status()
        return [
            {"ref": ref_of(r), "score": r.get("score"), "chunk_id": r.get("chunk_id")}
            for r in resp.json()["results"]
        ]

    return search


def v2_retriever(variant: str = "v2") -> Retriever:
    """The v2 engine in-process. Variants switch components off to measure each one:

        v2          everything available (exact lookup + case names + BM25 [+ dense])
        v2-bm25     BM25 only (no exact lookup, no case-name matching)
        v2-exact    exact lookup + case names only
    """
    from app.search.engine import SearchEngine, Weights

    weights = Weights()
    if variant == "v2-bm25":
        weights.exact = weights.exact_uncertain = weights.case_name = 0.0
    elif variant == "v2-exact":
        weights.bm25 = 0.0
    engine = SearchEngine.from_corpus(weights=weights)

    def search(query: str, k: int) -> list[dict]:
        return [{"ref": h["_ref"], "score": h["_score"], "chunk_id": h.get("chunk_id")}
                for h in engine.search(query, k=k)]

    return search


# ── Metrics ─────────────────────────────────────────────────────────────


def score_question(relevant: dict[str, int], refs: list[str | None]) -> dict:
    ranked: list[str] = []
    for ref in refs:  # each provision counts once, at its best rank
        if ref and ref not in ranked:
            ranked.append(ref)
    essential = {r for r, g in relevant.items() if g >= 2}

    def found(k: int, grade: int) -> set[str]:
        return {r for r in ranked[:k] if relevant.get(r, 0) >= grade}

    first = next((i for i, r in enumerate(ranked[:K], 1) if r in essential), None)
    dcg = sum((2 ** relevant.get(r, 0) - 1) / math.log2(i + 1) for i, r in enumerate(ranked[:K], 1))
    ideal = sorted(relevant.values(), reverse=True)[:K]
    idcg = sum((2**g - 1) / math.log2(i + 1) for i, g in enumerate(ideal, 1))
    return {
        "hit@5": bool(found(5, 2)),
        "hit@10": bool(found(10, 2)),
        "recall@5": len(found(5, 1)) / len(relevant),
        "recall@10": len(found(10, 1)) / len(relevant),
        "mrr@10": 1 / first if first else 0.0,
        "ndcg@10": dcg / idcg if idcg else 0.0,
        "first_essential_rank": first,
    }


METRICS = ("hit@5", "hit@10", "recall@5", "recall@10", "mrr@10", "ndcg@10")


def aggregate(rows: list[dict]) -> dict:
    scored = [r for r in rows if r["category"] not in UNSCORED and "error" not in r]
    if not scored:
        return {}
    out = {"n": len(scored)}
    for m in METRICS:
        out[m] = round(statistics.mean(float(r["metrics"][m]) for r in scored), 4)
    return out


def summarise(rows: list[dict]) -> dict:
    cats = sorted({r["category"] for r in rows if r["category"] not in UNSCORED})
    latencies = sorted(r["seconds"] for r in rows if "seconds" in r)
    return {
        "overall": aggregate(rows),
        "by_split": {s: aggregate([r for r in rows if r["split"] == s]) for s in ("dev", "test")},
        "by_category": {c: aggregate([r for r in rows if r["category"] == c]) for c in cats},
        "errors": sum("error" in r for r in rows),
        "latency_p50": latencies[len(latencies) // 2] if latencies else None,
        "latency_p95": latencies[min(int(0.95 * len(latencies)), len(latencies) - 1)] if latencies else None,
    }


# ── Running ─────────────────────────────────────────────────────────────


def load_questions() -> list[dict]:
    with QUESTIONS.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def evaluate(retriever: Retriever, name: str, notes: str = "") -> dict:
    rows = []
    questions = load_questions()
    for i, q in enumerate(questions, 1):
        row = {k: q[k] for k in ("id", "category", "split", "query", "relevant")}
        started = time.perf_counter()
        try:
            hits = retriever(q["query"], K)
        except Exception as exc:  # one failed query must not stop the run
            row["error"] = f"{type(exc).__name__}: {exc}"[:300]
        else:
            row["seconds"] = round(time.perf_counter() - started, 4)
            row["retrieved"] = [h["ref"] for h in hits]
            row["top_score"] = hits[0]["score"] if hits else None
            if q["category"] not in UNSCORED:
                row["metrics"] = score_question(q["relevant"], row["retrieved"])
        rows.append(row)
        print(f"\r  {i}/{len(questions)}", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    result = {
        "name": name,
        "notes": notes,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "questions": len(questions),
        "summary": summarise(rows),
        "rows": rows,
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"{name}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return result


# ── Reporting ───────────────────────────────────────────────────────────


def fmt(x: float | None) -> str:
    return "  -  " if x is None else f"{100 * x:5.1f}"


def print_summary(result: dict) -> None:
    s = result["summary"]
    print(f"\n{result['name']}  ({result['questions']} questions, {s['errors']} errors, "
          f"latency p50 {s['latency_p50']}s p95 {s['latency_p95']}s)")
    header = f"{'':16}{'n':>4} " + " ".join(f"{m:>9}" for m in METRICS)
    print(header)
    print("-" * len(header))

    def line(label: str, agg: dict) -> None:
        if agg:
            print(f"{label:16}{agg['n']:>4} " + " ".join(f"{fmt(agg[m]):>9}" for m in METRICS))

    line("overall", s["overall"])
    for split, agg in s["by_split"].items():
        line(f"  {split}", agg)
    print()
    for cat, agg in s["by_category"].items():
        line(cat, agg)


def load_result(name: str) -> dict:
    return json.loads((RESULTS / f"{name}.json").read_text(encoding="utf-8"))


def compare(base_name: str, new_name: str, split: str = "test") -> bool:
    base, new = load_result(base_name), load_result(new_name)
    b, n = base["summary"], new["summary"]
    print(f"\n{new_name}  vs  {base_name}   (split: {split}; points = percentage points)")
    print(f"{'':16} " + " ".join(f"{m:>9}" for m in METRICS))

    def delta_line(label: str, a: dict, c: dict) -> None:
        if a and c:
            print(f"{label:16} " + " ".join(f"{100 * (c[m] - a[m]):+9.1f}" for m in METRICS))

    delta_line("overall", b["overall"], n["overall"])
    delta_line(split, b["by_split"][split], n["by_split"][split])
    failures = []
    for cat in b["by_category"]:
        a, c = b["by_category"][cat], n["by_category"].get(cat)
        delta_line(cat, a, c)
        if c and c["ndcg@10"] < a["ndcg@10"] - GATE_DROP:
            failures.append(cat)
    improved = n["by_split"][split]["ndcg@10"] > b["by_split"][split]["ndcg@10"]
    print()
    if failures:
        print(f"GATE: FAIL, nDCG@10 dropped more than {100 * GATE_DROP:.0f} points in: {', '.join(failures)}")
    elif not improved:
        print(f"GATE: NEUTRAL, no category regressed, but {split} nDCG@10 did not improve")
    else:
        print("GATE: PASS")
    return not failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--target", required=True,
                     help="v1 API base URL (http://127.0.0.1:8000), or v2 / v2-bm25 / v2-exact (in-process)")
    run.add_argument("--name", required=True)
    run.add_argument("--notes", default="")
    show = sub.add_parser("show")
    show.add_argument("name")
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("base")
    cmp_.add_argument("new")
    cmp_.add_argument("--split", default="test")
    args = parser.parse_args()

    if args.cmd == "run":
        retriever = (v2_retriever(args.target) if args.target.startswith("v2")
                     else http_retriever(args.target))
        print_summary(evaluate(retriever, args.name, args.notes))
    elif args.cmd == "show":
        print_summary(load_result(args.name))
    else:
        sys.exit(0 if compare(args.base, args.new, args.split) else 1)


if __name__ == "__main__":
    main()
