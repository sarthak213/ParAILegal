"""Answer evaluation: the whole pipeline, with the answer model as the only variable.

    python -m eval.answers --model models/llm/Qwen3.5-4B-Q4_K_M.gguf --name 4b
    python -m eval.answers --model models/llm/Qwen3.5-9B-Q4_K_M.gguf --name 9b
    python -m eval.answers --compare 4b 9b

Search, the gate and the evidence run once per question (they are deterministic), so every model
answers from exactly the same sources. Per answer it records:

  verifier       source numbers that point nowhere, provisions and figures not in the sources,
                 provisions pinned on the wrong source, legal sentences without a source
  essential      the answer cites (by [n]) an essential provision that was in its evidence
  key facts      share of KEY_FACTS present (fixed statutory facts: "not less than seven years")
  old code       for an IPC/CrPC/IEA question, the answer names the new provision's number
  says unknown   for a no-answer question the gate let through, the answer says its sources
                 do not answer it
  speed          time to first token, total time, tokens

Sample: the first PER_CATEGORY test-split questions of each answerable category, plus every
test-split no-answer question (most are declined by the gate and never reach the model).
Results go to eval/results/answers-<name>.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import time
from pathlib import Path

from app.answer import prompts
from app.answer.gate import SOURCES_ONLY
from app.answer.verify import verify
from app.llm.client import ChatResult, stream_chat
from app.llm.server import LlamaServer

RESULTS = Path(__file__).parent / "results"
PER_CATEGORY = 4

# Facts an answer must state, each a list of acceptable spellings. Only where the statute fixes
# the fact exactly; checked case-insensitively.
KEY_FACTS: dict[str, list[list[str]]] = {
    "CIT02": [["High Court"], ["Court of Session", "Sessions Court", "Court of Sessions"], ["non-bailable"]],
    "CIT04": [["certificate"], ["electronic"]],
    "CIT06": [["three years", "3 years"], ["seven years", "7 years"]],
    "CIT08": [["President"], ["Governor"]],
    "PHR04": [["five years", "5 years"], ["negligen"]],
    "PHR06": [["ten years", "10 years"]],
    "PHR08": [["three years", "3 years"]],
    "LAY04": [["twenty-four hours", "24 hours"]],
    "LAY08": [["seven years", "7 years"], ["dowry"]],
    "OLD02": [["three years", "3 years"], ["cruelty"]],
    "OLD04": [["seven years", "7 years"], ["dowry"]],
    "OLD06": [["ten years", "10 years"]],
    "CON02": [["twenty-four hours", "24 hours"], ["legal practitioner", "lawyer", "counsel"]],
    "CON08": [["six weeks"], ["not in session", "is not in session"]],
    "JDG02": [["basic structure"]],
    "JDG04": [["rarest of rare"]],
    "XDM08": [["twenty-four hours", "24 hours"]],
}

_SAYS_UNKNOWN = re.compile(
    r"\b(?:do(?:es)?\s+not|don't|doesn't|cannot|can't)\s+(?:\w+\s+){0,2}"
    r"(?:contain|cover|address|answer|identify|mention|specify|provide|name|list|say|state|include|determine)"
    r"|\bnot\s+(?:covered|addressed|mentioned|specified|available|provided)\b|\bno\s+(?:provision|information)\b",
    re.IGNORECASE)
_CITE = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")


def sample() -> list[dict]:
    from eval.retrieval import load_questions

    rows, seen = [], {}
    for q in load_questions():
        if q["split"] != "test":
            continue
        n = seen.get(q["category"], 0)
        if q["category"] == "no_answer" or n < PER_CATEGORY:
            rows.append(q)
            seen[q["category"]] = n + 1
    return rows


def prepare_all(questions: list[dict]) -> list[tuple[dict, object]]:
    from app.answer.pipeline import prepare
    from app.core.config import settings
    from app.search.engine import SearchEngine

    engine = SearchEngine.from_corpus(dense=settings.DENSE_MODEL, rerank=settings.RERANK_MODEL)
    return [(q, prepare(q["query"], engine, settings.TOP_K_SEARCH)) for q in questions]


def score(q: dict, p, answer: str) -> dict:
    _, v = verify(answer, p.evidence, p.question)
    cited = {int(x) for c in _CITE.findall(answer) for x in re.split(r"\s*[,;]\s*", c)}
    essential = {r for r, g in q["relevant"].items() if g >= 2}
    in_evidence = {e.n for e in p.evidence if e.ref in essential}
    low = answer.lower()
    facts = KEY_FACTS.get(q["id"])
    out = {
        "invalid_ids": len(v.invalid_ids),
        "unsupported": len(v.unsupported_provisions) + len(v.unsupported_figures),
        "misattributed": len(v.misattributed),
        "uncited_share": round(len(v.uncited) / v.claims, 3) if v.claims else 0.0,
        "warning": bool(v.warning),
        "verification": v.to_dict(),
        # None when no essential provision reached the evidence: a retrieval miss, not the model's
        "essential_cited": bool(cited & in_evidence) if in_evidence else None,
        "key_facts": (sum(any(s.lower() in low for s in alts) for alts in facts) / len(facts)) if facts else None,
    }
    if q["category"] == "old_code":
        numbers = {r.rpartition(" ")[2] for r in essential}
        out["names_new_code"] = any(re.search(rf"\b{re.escape(n)}\b", answer) for n in numbers)
    if q["category"] == "no_answer":
        out["says_unknown"] = bool(_SAYS_UNKNOWN.search(answer))
    return out


async def run_model(model: Path, name: str, prepared: list[tuple[dict, object]]) -> list[dict]:
    from app.core.config import settings

    server = LlamaServer(settings.LLM_ENGINE_DIR, model, settings.LLM_THREADS, settings.LLM_BATCH_THREADS,
                         settings.LLM_CTX, idle_unload_s=3600, device=settings.LLM_DEVICE)
    t0 = time.monotonic()
    await server.ensure_running()
    print(f"{name}: {model.name} on {server.mode}, loaded in {time.monotonic() - t0:.1f} s")
    rows = []
    try:
        for i, (q, p) in enumerate(prepared, 1):
            row = {"id": q["id"], "category": q["category"], "query": q["query"], "outcome": p.outcome,
                   "sources": [e.ref for e in p.evidence]}
            if p.outcome == SOURCES_ONLY:
                rows.append(row)
                continue
            result, pieces, first = ChatResult(), [], None
            start = time.monotonic()
            async for text in stream_chat(server.base_url, prompts.messages(p.question, p.context, p.mode, p.outcome, p.read_as),
                                          prompts.MODES[p.mode].max_tokens, settings.TEMPERATURE_ANSWER, result):
                first = first if first is not None else time.monotonic() - start
                pieces.append(text)
            total = time.monotonic() - start
            answer = prompts.lead(p.outcome) + "".join(pieces) + prompts.tail(p.mode)
            row.update(answer=answer, first_token_s=round(first or total, 2), total_s=round(total, 2),
                       prompt_tokens=result.prompt_tokens, answer_tokens=result.completion_tokens,
                       finish=result.finish_reason, **score(q, p, answer))
            rows.append(row)
            print(f"  [{i}/{len(prepared)}] {q['id']:6} {total:5.1f} s  "
                  f"unsupported {row['unsupported']}  essential {row['essential_cited']}  facts {row['key_facts']}")
    finally:
        server.stop()
    return rows


def _mean(xs: list) -> float | None:
    xs = [float(x) for x in xs if x is not None]
    return round(statistics.mean(xs), 3) if xs else None


def summary(rows: list[dict]) -> dict:
    a = [r for r in rows if "answer" in r]
    return {
        "answered": len(a),
        "declined by gate": len(rows) - len(a),
        "answers with an invalid [n]": sum(r["invalid_ids"] > 0 for r in a),
        "answers with an unsupported provision or figure": sum(r["unsupported"] > 0 for r in a),
        "answers with a misattributed provision": sum(r["misattributed"] > 0 for r in a),
        "answers with a verifier warning": sum(r["warning"] for r in a),
        "uncited share of legal sentences": _mean([r["uncited_share"] for r in a]),
        "essential provision cited": _mean([r["essential_cited"] for r in a]),
        "key facts": _mean([r["key_facts"] for r in a]),
        "old code: names the new provision": _mean([r.get("names_new_code") for r in a]),
        "no-answer let through: says so": _mean([r.get("says_unknown") for r in a]),
        "first token s (median)": _mean([statistics.median([r["first_token_s"] for r in a])]) if a else None,
        "total s (median)": _mean([statistics.median([r["total_s"] for r in a])]) if a else None,
        "answer tokens (median)": statistics.median([r["answer_tokens"] or 0 for r in a]) if a else None,
        "cut off at max tokens": sum(r["finish"] == "length" for r in a),
    }


def compare(names: list[str]) -> None:
    sums = {n: json.loads((RESULTS / f"answers-{n}.json").read_text(encoding="utf-8"))["summary"] for n in names}
    keys = list(next(iter(sums.values())))
    print(f"{'':48}" + "".join(f"{n:>12}" for n in names))
    for k in keys:
        print(f"{k:48}" + "".join(f"{str(sums[n][k]):>12}" for n in names))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path, help="GGUF file of the answer model")
    ap.add_argument("--name", help="result name, e.g. 4b")
    ap.add_argument("--compare", nargs="+", help="print saved results side by side")
    args = ap.parse_args()
    if args.compare:
        compare(args.compare)
        return
    prepared = prepare_all(sample())
    rows = asyncio.run(run_model(args.model.resolve(), args.name, prepared))
    out = {"model": args.model.name, "summary": summary(rows), "rows": rows}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"answers-{args.name}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(out["summary"], indent=1))


if __name__ == "__main__":
    main()
