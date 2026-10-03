# Evaluation

Two harnesses, described in `docs/RAG-v2.md` §3.

## Retrieval (`eval/retrieval.py`)

```powershell
python -m eval.build_questions                       # regenerate questions.jsonl
python -m eval.retrieval run --target http://127.0.0.1:8000 --name v1-baseline
python -m eval.retrieval show v1-baseline
python -m eval.retrieval compare v1-baseline <new-run>   # applies the gate; exit code 1 on failure
```

- `questions.jsonl`: 231 questions in 11 categories. Relevant provisions are labelled **by
  citation** (`BNS 103`, `BNSS 482`, `BSA 63`, `ART 21`, `CASE maneka_gandhi`) with grade
  2 = essential, 1 = supporting, so the set survives re-chunking and corpus changes.
  `no_answer` and `corpus_gap` questions are not scored for ranking.
- Splits alternate within each category: `dev` (116) for tuning, `test` (115) for reporting.
- Every BNSS label was checked against the official India Code text, because the corpus's
  BNSS section titles are unreliable.
- `results/<name>.json`: per-question rankings and metrics.

## Baseline: v1 (2026-10-03)

Cloud pipeline: Groq rewrite, Cohere embeddings, Qdrant, RRF, hard domain routing.

| | n | hit@5 | recall@10 | MRR@10 | nDCG@10 |
|---|---:|---:|---:|---:|---:|
| overall | 217 | 82.0 | 79.3 | 61.6 | 65.5 |
| test split | 108 | 80.6 | 78.9 | 60.2 | 64.4 |
| hinglish | 10 | 40.0 | 40.0 | 24.0 | 27.7 |
| old_code | 28 | 60.7 | 64.3 | 48.5 | 52.1 |
| lay | 40 | 82.5 | 77.5 | 52.5 | 58.6 |
| citation | 22 | 81.8 | 81.8 | 70.8 | 73.6 |

Latency: p50 2.2 s, p95 4.0 s per search. Typical failure: citations are treated as fuzzy text
(`Section 103 of the BNS` → BNSS 103, `Section 302 IPC` → BNS 30).
