"""Fit the answer gate's thresholds (app/answer/gate.py) on the dev split; report the test split.

    python -m eval.calibrate_gate            # search every question once, cache the signals, fit
    python -m eval.calibrate_gate --cached   # refit from eval/results/gate-signals.json

What is fitted, on questions the gate's rules leave to the thresholds (no named provision, no
unknown citation, not foreign law):
  low, cos_low  decline when the best cross-encoder logit < low and the best dense cosine
                < cos_low; chosen to minimise DECLINE_COST x answerable questions declined
                + no-answer questions answered
  high          the lowest logit at which the top hit is relevant for >= HIGH_PRECISION of the
                dev questions above it: above it the answer carries no caveat
It also reports what the logit alone, and retriever agreement, would have scored.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from app.answer.gate import ANSWER, CAVEAT, SOURCES_ONLY, Thresholds, decide, foreign

RESULTS = Path(__file__).parent / "results"
SIGNALS = RESULTS / "gate-signals.json"
K = 15                 # hits the answer route receives (settings.TOP_K_SEARCH)
HIGH_PRECISION = 0.9
DECLINE_COST = 3       # declining an answerable question vs answering an unanswerable one
SIGNAL_KEYS = ("_ref", "_ce", "_cos", "_exact", "_bm25_rank", "_dense_rank")


def collect() -> list[dict]:
    from app.core.config import settings
    from app.search.engine import SearchEngine
    from eval.retrieval import load_questions

    engine = SearchEngine.from_corpus(dense=settings.DENSE_MODEL, rerank=settings.RERANK_MODEL)
    rows = []
    for q in load_questions():
        hits = engine.search(q["query"], k=K)
        rows.append({
            "id": q["id"], "category": q["category"], "split": q["split"], "query": q["query"],
            "relevant": q["relevant"], "unknown": engine.unknown_citations(q["query"]),
            "hits": [{k: h[k] for k in SIGNAL_KEYS} for h in hits],
        })
    RESULTS.mkdir(exist_ok=True)
    SIGNALS.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return rows


def answerable(r: dict) -> bool:
    return bool(r["relevant"])


def top_logit(r: dict) -> float | None:
    s = [h["_ce"] for h in r["hits"] if not h["_exact"] and h["_ce"] is not None]
    return max(s) if s else None


def top_hit(r: dict) -> dict:
    return max((h for h in r["hits"] if not h["_exact"] and h["_ce"] is not None), key=lambda h: h["_ce"])


def cos(r: dict) -> float:
    return r["hits"][0]["_cos"] if r["hits"] and r["hits"][0]["_cos"] is not None else 0.0


def gated(r: dict) -> bool:
    """Questions left to the thresholds by the gate's rules."""
    return (not any(h["_exact"] for h in r["hits"]) and not r["unknown"] and not foreign(r["query"])
            and top_logit(r) is not None)


def cost(rows: list[dict], declines) -> int:
    return sum(DECLINE_COST if answerable(r) else 0 for r in rows if declines(r)) \
        + sum(0 if answerable(r) else 1 for r in rows if not declines(r))


def grid(values: list[float]) -> list[float]:
    v = sorted({round(x, 2) for x in values})
    return v + [v[-1] + 0.01] if v else [0.0]


def fit_low(rows: list[dict]) -> tuple[float, float, int]:
    rows = [r for r in rows if gated(r)]
    best = None
    for low in grid([top_logit(r) for r in rows]):
        for cl in grid([cos(r) for r in rows]):
            c = cost(rows, lambda r: top_logit(r) < low and cos(r) < cl)
            # ties: decline as little as possible
            if best is None or c < best[2] or (c == best[2] and (low, cl) < (best[0], best[1])):
                best = (low, cl, c)
    return best


def fit_logit_only(rows: list[dict]) -> tuple[float, int]:
    rows = [r for r in rows if gated(r)]
    return min(((t, cost(rows, lambda r: top_logit(r) < t)) for t in grid([top_logit(r) for r in rows])),
               key=lambda x: (x[1], x[0]))


def fit_agreement(rows: list[dict]) -> tuple[float, int]:
    def agrees(r: dict) -> bool:
        h = top_hit(r)
        return (h["_bm25_rank"] or 99) <= 10 and (h["_dense_rank"] or 99) <= 10
    rows = [r for r in rows if gated(r)]
    return min(((t, cost(rows, lambda r: top_logit(r) < t and not agrees(r)))
                for t in grid([top_logit(r) for r in rows])), key=lambda x: (x[1], x[0]))


def fit_high(rows: list[dict], low: float) -> float:
    rows = [r for r in rows if gated(r) and answerable(r)]
    for t in grid([top_logit(r) for r in rows]):
        above = [r for r in rows if top_logit(r) >= t]
        if t >= low and above and \
                sum(r["relevant"].get(top_hit(r)["_ref"], 0) >= 1 for r in above) / len(above) >= HIGH_PRECISION:
            return t
    return max(top_logit(r) for r in rows)


def report(rows: list[dict], t: Thresholds, split: str) -> dict:
    rows = [r for r in rows if r["split"] == split]
    out = {ANSWER: 0, CAVEAT: 0, SOURCES_ONLY: 0}
    na = [0, 0]; ans = [0, 0]; ess = [0, 0]
    for r in rows:
        d = decide(r["query"], r["hits"], r["unknown"], t)
        out[d.outcome] += 1
        declined = d.outcome == SOURCES_ONLY
        if answerable(r):
            ans[0] += declined; ans[1] += 1
            refs = {h["_ref"] for h in d.evidence}
            essential = [x for x, g in r["relevant"].items() if g >= 2]
            ess[0] += sum(x in refs for x in essential); ess[1] += len(essential)
        else:
            na[0] += declined; na[1] += 1
    return {"split": split, "outcomes": out,
            "no_answer declined": f"{na[0]}/{na[1]}", "answerable declined": f"{ans[0]}/{ans[1]}",
            "essential provisions in evidence": f"{ess[0]}/{ess[1]} ({ess[0] / max(ess[1], 1):.1%})"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cached", action="store_true", help="refit from the saved signals")
    args = ap.parse_args()
    rows = json.loads(SIGNALS.read_text(encoding="utf-8")) if args.cached else collect()
    dev = [r for r in rows if r["split"] == "dev"]

    low, cos_low, c = fit_low(dev)
    lo_only, c_only = fit_logit_only(dev)
    lo_agree, c_agree = fit_agreement(dev)
    high = fit_high(dev, low)
    t = replace(Thresholds(), low=low, cos_low=cos_low, high=high)

    print(f"low {low:.2f}, cos_low {cos_low:.2f}: dev cost {c}")
    print(f"  logit alone:          low {lo_only:.2f}, dev cost {c_only}")
    print(f"  logit + agreement:    low {lo_agree:.2f}, dev cost {c_agree}")
    print(f"high {high:.2f} (top hit relevant for >= {HIGH_PRECISION:.0%} of dev questions above it)")
    for split in ("dev", "test"):
        print(json.dumps(report(rows, t, split)))
    print("\nMisjudged on test:")
    for r in rows:
        if r["split"] == "test":
            d = decide(r["query"], r["hits"], r["unknown"], t)
            if (d.outcome == SOURCES_ONLY) == answerable(r):
                print(f"  {r['id']:7} {d.outcome:12} logit {top_logit(r)} cos {cos(r):.3f}  {r['query']}")


if __name__ == "__main__":
    main()
