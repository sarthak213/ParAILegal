"""
tests/test_suite.py
────────────────────
ParAILegal test suite — migrated for the FastAPI backend.

What changed from the original testSuite.py
────────────────────────────────────────────
OLD: imported RAGSystem and QueryRouter directly, called Python methods in-process.
NEW: hits the live FastAPI app over HTTP using httpx.AsyncClient.
     Tests the full production stack — routing → DI → RAG pipeline → JSON response.

The --router mode is the one exception: QueryRouter is pure Python with no I/O,
so it's still tested directly. Importing it here does not spin up any models.

Usage
─────
    # Run against a locally running server (default: http://127.0.0.1:8000)
    python -m tests.test_suite --retrieval-only
    python -m tests.test_suite --router
    python -m tests.test_suite --category statutes
    python -m tests.test_suite --id S01
    python -m tests.test_suite                     # full suite with LLM answers

    # Run against a different server
    python -m tests.test_suite --base-url http://192.168.1.10:8000

    # Or via pytest (marks the slow tests so you can skip them in CI)
    pytest tests/test_suite.py -v
    pytest tests/test_suite.py -v -m "not slow"

Prerequisites
─────────────
    pip install httpx pytest pytest-asyncio

The server must be running and /ready must return 200 before tests begin.
The suite waits up to STARTUP_TIMEOUT_S seconds for readiness on startup.
"""

import sys
import time
import asyncio
import argparse
import traceback
from typing import List, Dict, Optional

import httpx
import pytest

# ── QueryRouter imported directly for --router mode (no HTTP needed) ──
from app.services.routing_service import QueryRouter
from app.core.config import settings as _settings

router = QueryRouter(_settings)

# ── Base URL — override with --base-url or BASE_URL env var ──────────
import os
DEFAULT_BASE_URL   = os.getenv("PARALEGAL_TEST_URL", "http://127.0.0.1:8000")
STARTUP_TIMEOUT_S  = 120     # max seconds to wait for /ready on startup
REQUEST_TIMEOUT_S  = 120     # per-request timeout (answer calls can be slow)


# ══════════════════════════════════════════════════════════════════════
# Test definitions — identical to the original testSuite.py
# ══════════════════════════════════════════════════════════════════════

ROUTER_TESTS = [
    # statutes
    ("What is the punishment for murder?",                             "statutes"),
    ("What constitutes theft under Indian law?",                       "statutes"),
    ("When can bail be granted for a non-bailable offence?",           "statutes"),
    ("Is a confession to police admissible?",                          "statutes"),
    ("Who bears the burden of proof in a criminal trial under BSA?",   "statutes"),
    ("What is a dying declaration?",                                   "statutes"),
    ("Can a person of unsound mind be criminally liable under BNS?",   "statutes"),
    ("What is Section 103 of BNS?",                                    "statutes"),
    ("FIR under BNSS",                                                 "statutes"),
    ("What is anticipatory bail?",                                     "statutes"),
    # constitution
    ("What does Article 21 guarantee?",                                "constitution"),
    ("What is Article 14 of the Constitution?",                        "constitution"),
    ("What writs can be issued under Article 32?",                     "constitution"),
    ("Explain the amendment procedure under Article 368",              "constitution"),
    ("What are fundamental rights?",                                   "constitution"),
    ("What is the preamble of the Constitution?",                      "constitution"),
    # judgements
    ("Supreme Court held that privacy is a fundamental right",         "judgements"),
    ("What did the Supreme Court hold in Bachan Singh v State of Punjab?",  "judgements"),
    ("What is the ratio in Maneka Gandhi v Union of India?",                "judgements"),
    ("What did the court hold in Kesavananda Bharati?",                     "judgements"),
    ("Is right to privacy a fundamental right after Puttaswamy?",           "judgements"),
    ("Vishaka guidelines sexual harassment workplace",                       "judgements"),
    # all
    ("What is Indian law on murder and capital punishment?",           "all"),
    ("Is the death penalty constitutional and what does BNS say?",     "all"),
]


TESTS = [
    # ── statutes ──────────────────────────────────────────────────────
    {
        "id": "S01", "category": "statutes",
        "description": "Punishment for murder (BNS Section 103)",
        "query": "What is the punishment for murder?",
        "expected_domain": "statutes",
        "expected_citations": ["103"],
    },
    {
        "id": "S02", "category": "statutes",
        "description": "Definition of theft (BNS Section 303)",
        "query": "What constitutes theft under Indian law?",
        "expected_domain": "statutes",
        "expected_citations": ["303"],
    },
    {
        "id": "S03", "category": "statutes",
        "description": "Punishment for rape (BNS Section 64)",
        "query": "What is the punishment for rape under BNS 2023?",
        "expected_domain": "statutes",
        "expected_citations": ["64"],
    },
    {
        "id": "S04", "category": "statutes",
        "description": "Culpable homicide vs murder distinction",
        "query": "What is the difference between culpable homicide and murder under BNS?",
        "expected_domain": "statutes",
        "expected_citations": ["101", "100"],
    },
    {
        "id": "S05", "category": "statutes",
        "description": "Dacoity definition and punishment (BNS Section 310)",
        "query": "How many persons are required for dacoity and what is the punishment?",
        "expected_domain": "statutes",
        "expected_citations": ["310"],
    },
    {
        "id": "S06", "category": "statutes",
        "description": "Dowry death (BNS Section 80)",
        "query": "What is the punishment for dowry death?",
        "expected_domain": "statutes",
        "expected_citations": ["80"],
    },
    {
        "id": "S07", "category": "statutes",
        "description": "Organised crime (BNS Section 111)",
        "query": "What does organised crime mean under BNS and what is the punishment?",
        "expected_domain": "statutes",
        "expected_citations": ["111"],
    },
    {
        "id": "S08", "category": "statutes",
        "description": "Kidnapping for ransom (BNS Section 140)",
        "query": "What is the punishment for kidnapping for ransom?",
        "expected_domain": "statutes",
        "expected_citations": ["140"],
    },
    # ── procedure ─────────────────────────────────────────────────────
    {
        "id": "P01", "category": "procedure",
        "description": "FIR filing procedure (BNSS Section 173)",
        "query": "How is an FIR filed and what information must it contain under BNSS?",
        "expected_domain": "statutes",
        "expected_citations": ["173"],
    },
    {
        "id": "P02", "category": "procedure",
        "description": "Bail for non-bailable offences (BNSS Section 480)",
        "query": "When can bail be granted for a non-bailable offence?",
        "expected_domain": "statutes",
        "expected_citations": ["480"],
    },
    {
        "id": "P03", "category": "procedure",
        "description": "Anticipatory bail (BNSS Section 482 or 483)",
        "query": "What is anticipatory bail and who can grant it under BNSS?",
        "expected_domain": "statutes",
        "expected_citations": [],
        "expected_citations_any": [["482"], ["483"]],
    },
    {
        "id": "P04", "category": "procedure",
        "description": "Police powers of arrest without warrant (BNSS Section 35)",
        "query": "When can police arrest a person without a warrant?",
        "expected_domain": "statutes",
        "expected_citations": ["35"],
    },
    # ── evidence ──────────────────────────────────────────────────────
    {
        "id": "E01", "category": "evidence",
        "description": "Admissibility of confession to police (BSA Section 23)",
        "query": "Is a confession made to a police officer admissible in court?",
        "expected_domain": "statutes",
        "expected_citations": ["23"],
    },
    {
        "id": "E02", "category": "evidence",
        "description": "Burden of proof (BSA — Section 104, 105, or 108)",
        "query": "Who bears the burden of proof in a criminal trial under BSA?",
        "expected_domain": "statutes",
        "expected_citations": [],
        "expected_citations_any": [["104"], ["105"], ["108"]],
    },
    {
        "id": "E03", "category": "evidence",
        "description": "Electronic records as evidence (BSA Section 63)",
        "query": "Are electronic records admissible as evidence under BSA?",
        "expected_domain": "statutes",
        "expected_citations": ["63"],
    },
    {
        "id": "E04", "category": "evidence",
        "description": "Dying declaration (BSA Section 26)",
        "query": "What is a dying declaration under BSA and is it admissible?",
        "expected_domain": "statutes",
        "expected_citations": ["26"],
    },
    # ── constitution ──────────────────────────────────────────────────
    {
        "id": "C01", "category": "constitution",
        "description": "Right to life (Article 21)",
        "query": "What does Article 21 of the Constitution guarantee?",
        "expected_domain": "constitution",
        "expected_citations": ["21"],
    },
    {
        "id": "C02", "category": "constitution",
        "description": "Right to equality (Article 14)",
        "query": "What is Article 14 of the Indian Constitution?",
        "expected_domain": "constitution",
        "expected_citations": ["14"],
    },
    {
        "id": "C03", "category": "constitution",
        "description": "Freedom of speech (Article 19)",
        "query": "What are the reasonable restrictions on freedom of speech under Article 19?",
        "expected_domain": "constitution",
        "expected_citations": ["19"],
    },
    {
        "id": "C04", "category": "constitution",
        "description": "Writs (Article 32)",
        "query": "What does Article 32 of the Constitution say about writs?",
        "expected_domain": "constitution",
        "expected_citations": ["32"],
    },
    {
        "id": "C05", "category": "constitution",
        "description": "Emergency provisions (Article 352)",
        "query": "Under what circumstances can a national emergency be declared under Article 352?",
        "expected_domain": "constitution",
        "expected_citations": ["352"],
    },
    {
        "id": "C06", "category": "constitution",
        "description": "Amendment procedure (Article 368)",
        "query": "What is the procedure under Article 368 for amending the Constitution?",
        "expected_domain": "constitution",
        "expected_citations": ["368"],
    },
    # ── judgements ─────────────────────────────────────────────────────
    {
        "id": "J01", "category": "judgement",
        "description": "Basic structure doctrine — Kesavananda Bharati",
        "query": "What did Kesavananda Bharati hold on the basic structure doctrine?",
        "expected_domain": "judgements",
        "expected_citations": [],
        "expected_citations_any": [
            ["Kesavananda Bharati"],
            ["AIR 1973 SC 1461"],
        ],
    },
    {
        "id": "J02", "category": "judgement",
        "description": "Maneka Gandhi — golden triangle Articles 14 19 21",
        "query": "What did the Supreme Court hold in Maneka Gandhi v Union of India?",
        "expected_domain": "judgements",
        "expected_citations": [],
        "expected_citations_any": [
            ["Maneka Gandhi"],
            ["1978) 1 SCC 248"],
        ],
    },
    {
        "id": "J03", "category": "judgement",
        "description": "Bachan Singh — rarest of rare doctrine",
        "query": "What is the rarest of rare doctrine from Bachan Singh?",
        "expected_domain": "judgements",
        "expected_citations": [],
        "expected_citations_any": [
            ["Bachan Singh"],
            ["AIR 1980 SC 898"],
        ],
    },
    {
        "id": "J04", "category": "judgement",
        "description": "Right to privacy — Puttaswamy",
        "query": "What did the Supreme Court hold on right to privacy in Puttaswamy?",
        "expected_domain": "judgements",
        "expected_citations": [],
        "expected_citations_any": [
            ["Puttaswamy"],
            ["2017) 10 SCC 1"],
        ],
    },
    {
        "id": "J05", "category": "judgement",
        "description": "Section 377 decriminalisation — Navtej Johar",
        "query": "What did the Supreme Court hold on Section 377 and homosexuality?",
        "expected_domain": "judgements",
        "expected_citations": [],
        "expected_citations_any": [
            ["Navtej"],
            ["Johar"],
        ],
    },
    {
        "id": "J06", "category": "judgement",
        "description": "President's Rule — SR Bommai",
        "query": "What did SR Bommai case hold on President's Rule under Article 356?",
        "expected_domain": "judgements",
        "expected_citations": [],
        "expected_citations_any": [
            ["Bommai"],
            ["1994) 3 SCC 1"],
        ],
    },
    {
        "id": "J07", "category": "judgement",
        "description": "OBC reservations 50 percent cap — Indra Sawhney",
        "query": "What did Indra Sawhney hold on the 50 percent ceiling on reservations?",
        "expected_domain": "judgements",
        "expected_citations": [],
        "expected_citations_any": [
            ["Indra Sawhney"],
            ["AIR 1993 SC 477"],
        ],
    },
    {
        "id": "J08", "category": "judgement",
        "description": "Right to livelihood — Olga Tellis",
        "query": "What did Olga Tellis hold on right to livelihood under Article 21?",
        "expected_domain": "judgements",
        "expected_citations": [],
        "expected_citations_any": [
            ["Olga Tellis"],
            ["AIR 1986 SC 180"],
        ],
    },
    {
        "id": "J09", "category": "judgement",
        "description": "Sexual harassment at workplace — Vishaka guidelines",
        "query": "What are the Vishaka guidelines on sexual harassment at workplace?",
        "expected_domain": "judgements",
        "expected_citations": [],
        "expected_citations_any": [
            ["Vishaka"],
            ["1997) 6 SCC 241"],
        ],
    },
    {
        "id": "J10", "category": "judgement",
        "description": "Overruled case surfaced with warning — ADM Jabalpur",
        "query": "What did ADM Jabalpur hold on habeas corpus and Article 21 during Emergency?",
        "expected_domain": "judgements",
        "expected_citations": [],
        "expected_citations_any": [
            ["ADM Jabalpur"],
            ["AIR 1976 SC 1207"],
        ],
        # The LLM must flag that this is overruled — verified in answer mode
        "answer_must_contain": ["overruled"],
    },
    # ── Cross-domain: judgement + constitution ─────────────────────────
    {
        "id": "J11", "category": "judgement",
        "description": "Death penalty constitutionality — cross domain",
        "query": "Is the death penalty constitutional according to the Supreme Court?",
        "expected_domain": "all",
        "expected_citations": [],
        "expected_citations_any": [
            ["Bachan Singh"],
            ["103"],      # BNS Section 103 also relevant
        ],
    },
    {
        "id": "J12", "category": "judgement",
        "description": "Speedy trial right — Hussainara Khatoon",
        "query": "What did Hussainara Khatoon hold on right to speedy trial?",
        "expected_domain": "judgements",
        "expected_citations": [],
        "expected_citations_any": [
            ["Hussainara"],
            ["AIR 1979 SC 1360"],
        ],
    },
    # ── cross-index ───────────────────────────────────────────────────
    {
        "id": "X01", "category": "cross",
        "description": "Murder punishment under BNS (Section 103)",
        "query": "What is the punishment for murder under BNS 2023?",
        "expected_domain": "statutes",
        "expected_citations": ["103"],
    },
    {
        "id": "X02", "category": "cross",
        "description": "Right to silence — BSA confession rules",
        "query": "Does an accused have the right to silence and can they be compelled to confess?",
        "expected_domain": "statutes",
        "expected_citations": ["23"],
    },
    {
        "id": "X03", "category": "cross",
        "description": "Speedy trial — BNSS provisions",
        "query": "What BNSS provisions protect an accused from unreasonable delay in trial?",
        "expected_domain": "statutes",
        "expected_citations": ["346"],
    },
    {
        "id": "X04", "category": "cross",
        "description": "Double jeopardy — BNSS Section 337",
        "query": "Can a person be tried twice for the same offence in India?",
        "expected_domain": "statutes",
        "expected_citations": ["337"],
    },
    {
        "id": "X05", "category": "cross",
        "description": "Acts endangering sovereignty — BNS Section 152",
        "query": "What does BNS 2023 say about acts endangering sovereignty and integrity of India?",
        "expected_domain": "statutes",
        "expected_citations": ["152"],
    },
    # ── complex ───────────────────────────────────────────────────────
    {
        "id": "Q01", "category": "complex",
        "description": "Private defence — extent and limits",
        "query": "Can a person cause death in private defence under BNS and what are the limits?",
        "expected_domain": "statutes",
        "expected_citations": ["38"],
    },
    {
        "id": "Q02", "category": "complex",
        "description": "Abetment of suicide",
        "query": "What constitutes abetment of suicide and what is the punishment under BNS?",
        "expected_domain": "statutes",
        "expected_citations": ["108"],
    },
    {
        "id": "Q03", "category": "complex",
        "description": "Attempt to murder",
        "query": "What is the punishment for attempt to murder under BNS 2023?",
        "expected_domain": "statutes",
        "expected_citations": ["109"],
    },
    {
        "id": "Q04", "category": "complex",
        "description": "Insanity defence under BNS",
        "query": "Can a person of unsound mind be held criminally liable under BNS 2023?",
        "expected_domain": "statutes",
        "expected_citations": [],
        "expected_citations_any": [["22"], ["369"]],
    },
    {
        "id": "Q05", "category": "complex",
        "description": "Consent in sexual offences",
        "query": "How does BNS 2023 define consent in the context of sexual offences?",
        "expected_domain": "statutes",
        "expected_citations": ["63"],
    },
    # ── mode ──────────────────────────────────────────────────────────
    {
        "id": "M01", "category": "mode",
        "description": "ADVOCATE mode — argue against death penalty",
        "query": "ADVOCATE: The death penalty for murder is constitutionally valid.",
        "expected_domain": "all",
        "expected_citations": [],
        "expected_citations_any": [["103"], ["21"]],
    },
    {
        "id": "M02", "category": "mode",
        "description": "SUMMARISE mode — explain bail for non-lawyers",
        "query": "SUMMARISE: When can a person get bail after being arrested?",
        "expected_domain": "statutes",
        "expected_citations": ["480"],
    },
    # ── grounding ─────────────────────────────────────────────────────
    {
        "id": "G01", "category": "grounding",
        "description": "Out-of-scope query — model must refuse",
        "query": "What is the Companies Act 2013 penalty for insider trading?",
        "expected_domain": "all",
        "expected_citations": [],
        "forbidden_in_answer": ["Section 195", "SEBI"],
        "answer_must_contain": ["does not contain", "context"],
    },
    {
        "id": "G02", "category": "grounding",
        "description": "Case law hallucination check",
        "query": "What did the Supreme Court hold in Bachan Singh v State of Punjab?",
        "expected_domain": "judgements",
        "expected_citations": [],
        "forbidden_in_answer": ["the court held", "the court observed"],
        "answer_must_contain": [],
    },
]


# ══════════════════════════════════════════════════════════════════════
# Helpers — identical logic to original, now operate on HTTP response dicts
# ══════════════════════════════════════════════════════════════════════

def citation_in_sources(expected: str, sources: List[Dict]) -> bool:
    """True if `expected` appears anywhere in any source field."""
    for src in sources:
        for field in ("citation", "hierarchy", "chunk_id", "section", "label"):
            if expected in (src.get(field) or ""):
                return True
    return False


def _check_citations(test: Dict, sources: List[Dict]) -> List[str]:
    """Return list of expected citations not found in sources. Empty = pass."""
    if not test.get("expected_citations") and not test.get("expected_citations_any"):
        return []
    if test.get("expected_citations_any"):
        for alt_set in test["expected_citations_any"]:
            if all(citation_in_sources(c, sources) for c in alt_set):
                return []
        return test["expected_citations_any"][0]
    return [c for c in test["expected_citations"]
            if not citation_in_sources(c, sources)]


def _get_score(chunk: Dict) -> float:
    return chunk.get("rrf_score") or chunk.get("score") or 0.0


def _safe_citation_line(src: Dict) -> str:
    cit = (src.get("citation") or src.get("hierarchy") or
           src.get("chunk_id") or "?")
    lines = cit[:70].splitlines()
    return lines[0] if lines else "?"


def _domain_ok(test: Dict, detected: str) -> bool:
    expected = test["expected_domain"]
    return (
        expected == "all" or
        detected == expected or
        (expected == "statutes" and detected in ("statutes", "all"))
    )


# ══════════════════════════════════════════════════════════════════════
# Server readiness
# ══════════════════════════════════════════════════════════════════════

async def wait_for_ready(base_url: str, timeout: float = STARTUP_TIMEOUT_S) -> None:
    """
    Poll GET /ready until the server responds 200.
    Raises RuntimeError if the server isn't ready within `timeout` seconds.
    """
    deadline = time.time() + timeout
    async with httpx.AsyncClient(base_url=base_url, timeout=5.0) as client:
        while time.time() < deadline:
            try:
                resp = await client.get("/ready")
                if resp.status_code == 200:
                    print(f"  Server ready at {base_url}")
                    return
                print(f"  /ready → {resp.status_code}, waiting...")
            except httpx.ConnectError:
                print(f"  Server not reachable yet, waiting...")
            await asyncio.sleep(3)
    raise RuntimeError(
        f"Server at {base_url} did not become ready within {timeout}s.\n"
        f"Make sure `uvicorn app.main:app` is running."
    )


# ══════════════════════════════════════════════════════════════════════
# HTTP test runners
# ══════════════════════════════════════════════════════════════════════

async def run_retrieval_test(client: httpx.AsyncClient, test: Dict) -> Dict:
    """POST /api/v1/search and validate citations + domain."""
    t0 = time.time()
    try:
        resp = await client.post(
            "/api/v1/search",
            json={"query": test["query"], "k": 15},
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        return _error_result(test["id"], time.time() - t0,
                             f"HTTP {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        return _error_result(test["id"], time.time() - t0, str(e))

    elapsed = time.time() - t0
    data    = resp.json()
    sources = data.get("results", [])[:5]
    detected = data.get("domain", "?")

    domain_ok = _domain_ok(test, detected)
    missing   = _check_citations(test, sources)
    passed    = domain_ok and not missing

    return {
        "id":               test["id"],
        "passed":           passed,
        "elapsed":          elapsed,
        "domain":           detected,
        "domain_ok":        domain_ok,
        "missing_citations": missing,
        "top5": [(_safe_citation_line(s), round(_get_score(s), 3)) for s in sources],
    }


async def run_answer_test(client: httpx.AsyncClient, test: Dict) -> Dict:
    """POST /api/v1/answer and validate citations + hallucination + required strings."""
    t0 = time.time()
    try:
        resp = await client.post(
            "/api/v1/answer",
            json={"query": test["query"]},
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        return _error_result(test["id"], time.time() - t0,
                             f"HTTP {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        return _error_result(test["id"], time.time() - t0, str(e))

    elapsed = time.time() - t0
    data    = resp.json()
    sources = data.get("sources", [])[:5]
    answer  = data.get("answer", "").lower()

    missing_cit      = _check_citations(test, sources)
    hallucinations   = [s for s in test.get("forbidden_in_answer", [])
                        if s.lower() in answer]
    missing_required = [s for s in test.get("answer_must_contain", [])
                        if s.lower() not in answer]

    return {
        "id":               test["id"],
        "passed":           not missing_cit and not hallucinations and not missing_required,
        "elapsed":          elapsed,
        "missing_citations": missing_cit,
        "hallucinations":   hallucinations,
        "missing_required": missing_required,
        "answer_preview":   data.get("answer", "")[:300],
    }


def _error_result(test_id: str, elapsed: float, error: str) -> Dict:
    return {
        "id": test_id, "passed": False, "elapsed": elapsed,
        "top5": [], "domain_ok": True, "domain": "?",
        "missing_citations": [], "hallucinations": [],
        "missing_required": [], "error": error,
    }


# ══════════════════════════════════════════════════════════════════════
# Output
# ══════════════════════════════════════════════════════════════════════

def print_result(test: Dict, result: Dict, mode: str) -> None:
    status = "✅ PASS" if result["passed"] else "❌ FAIL"
    print(f"\n{status}  [{result['id']}] {test['description']}  ({result['elapsed']:.1f}s)")

    if result.get("error"):
        print(f"       Error            : {result['error']}")
    if not result["passed"]:
        if result.get("missing_citations"):
            print(f"       Missing in top-5 : {result['missing_citations']}")
        if result.get("hallucinations"):
            print(f"       Hallucinations   : {result['hallucinations']}")
        if result.get("missing_required"):
            print(f"       Required absent  : {result['missing_required']}")
        if not result.get("domain_ok", True):
            print(f"       Domain mismatch  : got '{result.get('domain')}', "
                  f"expected '{test.get('expected_domain')}'")
    if mode == "retrieval" and result.get("top5"):
        for rank, (cit, score) in enumerate(result["top5"], 1):
            print(f"         [{rank}] {score:>7.3f}  {cit}")


# ══════════════════════════════════════════════════════════════════════
# Router tests — pure Python, no HTTP
# ══════════════════════════════════════════════════════════════════════

def run_router_tests() -> bool:
    print(f"\n{'='*60}")
    print(f"QueryRouter Diagnostic — {len(ROUTER_TESTS)} queries")
    print(f"{'='*60}")
    passed = 0
    for query, expected in ROUTER_TESTS:
        got = router.route(query)
        ok  = (got == expected)
        passed += ok
        print(f"  {'✅' if ok else '❌'} [{expected:14s}→{got:14s}]  {query[:60]}")
        if not ok:
            print(f"       triggers: {router.explain(query)['triggers'][:3]}")
    print(f"\n  {passed}/{len(ROUTER_TESTS)} routing tests passed")
    return passed == len(ROUTER_TESTS)


# ══════════════════════════════════════════════════════════════════════
# Pytest interface
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("query,expected", ROUTER_TESTS)
def test_router(query: str, expected: str) -> None:
    """Unit test: QueryRouter logic — no server required."""
    assert router.route(query) == expected, (
        f"Router returned '{router.route(query)}' for: {query!r}\n"
        f"Triggers: {router.explain(query)['triggers']}"
    )


@pytest.fixture(scope="session")
def base_url() -> str:
    return DEFAULT_BASE_URL


@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


@pytest.mark.asyncio
@pytest.mark.slow
@pytest.mark.parametrize("test", [t for t in TESTS if t["category"] != "grounding"],
                         ids=[t["id"] for t in TESTS if t["category"] != "grounding"])
async def test_retrieval(test: Dict, base_url: str) -> None:
    """Integration test: POST /api/v1/search for each test case."""
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=REQUEST_TIMEOUT_S,
    ) as client:
        result = await run_retrieval_test(client, test)

    assert result["passed"], (
        f"[{test['id']}] {test['description']}\n"
        f"  Domain  : {result.get('domain')} "
        f"(ok={result.get('domain_ok')})\n"
        f"  Missing : {result.get('missing_citations')}\n"
        f"  Top-5   : {result.get('top5')}"
    )


@pytest.mark.asyncio
@pytest.mark.slow
@pytest.mark.parametrize("test", TESTS,
                         ids=[t["id"] for t in TESTS])
async def test_answer(test: Dict, base_url: str) -> None:
    """Integration test: POST /api/v1/answer for each test case."""
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=REQUEST_TIMEOUT_S,
    ) as client:
        result = await run_answer_test(client, test)

    assert result["passed"], (
        f"[{test['id']}] {test['description']}\n"
        f"  Missing citations : {result.get('missing_citations')}\n"
        f"  Hallucinations    : {result.get('hallucinations')}\n"
        f"  Required absent   : {result.get('missing_required')}\n"
        f"  Answer preview    : {result.get('answer_preview', '')[:200]}"
    )


# ══════════════════════════════════════════════════════════════════════
# CLI interface — mirrors original testSuite.py flags exactly
# ══════════════════════════════════════════════════════════════════════

async def _run_cli(args: argparse.Namespace) -> int:
    base_url = args.base_url

    if args.router:
        return 0 if run_router_tests() else 1

    # Wait for server to be ready
    print(f"\nWaiting for server at {base_url}...")
    await wait_for_ready(base_url)

    # Filter tests
    tests = TESTS
    if args.category:
        tests = [t for t in tests if t["category"] == args.category]
    if args.id:
        tests = [t for t in tests if t["id"] == args.id]
    if not tests:
        print("No tests matched.")
        return 0

    if args.retrieval_only:
        runnable = [t for t in tests if t["category"] != "grounding"]
        skipped  = [t for t in tests if t["category"] == "grounding"]
        if skipped:
            print(f"  (Skipping {len(skipped)} grounding tests — require LLM)")
        tests = runnable

    mode = "retrieval" if args.retrieval_only else "answer"
    print(f"\n{'='*60}")
    print(f"ParAILegal Test Suite  —  {len(tests)} tests  —  mode={mode}")
    print(f"Base URL: {base_url}")
    print(f"{'='*60}")

    results = []
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=REQUEST_TIMEOUT_S,
    ) as client:
        for test in tests:
            print(f"\nRunning [{test['id']}] {test['description']}...")
            try:
                if args.retrieval_only:
                    result = await run_retrieval_test(client, test)
                else:
                    result = await run_answer_test(client, test)
                print_result(test, result, mode)
                results.append((test, result))
            except Exception as e:
                traceback.print_exc()
                print(f"  💥 ERROR: {e}")
                results.append((test, _error_result(test["id"], 0, str(e))))

    # Summary
    passed     = sum(1 for _, r in results if r.get("passed"))
    total      = len(results)
    total_time = sum(r.get("elapsed", 0) for _, r in results)

    print(f"\n{'='*60}")
    print(f"Results: {passed}/{total} passed  |  {total_time:.1f}s total")
    print(f"{'='*60}")

    for cat in sorted(set(t["category"] for t, _ in results)):
        cat_res    = [(t, r) for t, r in results if t["category"] == cat]
        cat_passed = sum(1 for _, r in cat_res if r.get("passed"))
        bar        = "█" * cat_passed + "░" * (len(cat_res) - cat_passed)
        print(f"  {cat:15s} {cat_passed}/{len(cat_res)}  {bar}")

    failed = [(t, r) for t, r in results if not r.get("passed")]
    if failed:
        print(f"\nFailed:")
        for test, result in failed:
            print(f"  ❌ [{result['id']}] {test['description']}")
        return 1

    print("\nAll tests passed ✅")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ParAILegal test suite — runs against the live FastAPI server"
    )
    parser.add_argument("--retrieval-only", action="store_true",
                        help="Only test /search, skip /answer (no LLM calls)")
    parser.add_argument("--router", action="store_true",
                        help="Run QueryRouter unit tests only — no server required")
    parser.add_argument("--category", type=str, default=None,
                        help="Filter to one category: statutes, procedure, evidence, "
                             "constitution, cross, complex, mode, grounding")
    parser.add_argument("--id", type=str, default=None,
                        help="Run a single test by ID, e.g. S01")
    parser.add_argument("--base-url", type=str, default=DEFAULT_BASE_URL,
                        help=f"API base URL (default: {DEFAULT_BASE_URL})")
    args = parser.parse_args()

    exit_code = asyncio.run(_run_cli(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()