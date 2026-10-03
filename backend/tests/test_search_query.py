"""Query understanding, the old→new code tables, and the hybrid engine (offline, no network)."""

import json
import re
from pathlib import Path

import pytest

from app.search.legal_data import CRPC_TO_BNSS, IEA_TO_BSA, IPC_TO_BNS, SPLIT_TARGETS
from app.search.query import QueryParser, find_citations

DATA = Path(__file__).resolve().parents[1] / "data"


def corpus_sections(name: str) -> set[str]:
    with (DATA / name).open(encoding="utf-8") as fh:
        return {str(json.loads(line)["section_number"]) for line in fh}


@pytest.mark.parametrize(("table", "corpus"), [
    (IPC_TO_BNS, "bns_clean.jsonl"),
    (CRPC_TO_BNSS, "bnss_clean.jsonl"),
    (IEA_TO_BSA, "bsa_clean.jsonl"),
])
def test_every_mapped_section_exists_in_the_corpus(table, corpus):
    sections = corpus_sections(corpus)
    missing = sorted({new for new in table.values() if new not in sections})
    assert not missing, f"targets not in {corpus}: {missing}"


def test_ipc_table_agrees_with_the_corpus_equivalents():
    """The BNS data says which IPC section each BNS section replaced; the table must agree."""
    disagreements = []
    with (DATA / "bns_clean.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            for old in re.findall(r"\d+[A-Z]*", row.get("ipc_equivalent") or ""):
                mapped = IPC_TO_BNS.get(old)
                targets = {mapped} | {t.split()[1] for t in SPLIT_TARGETS.get(f"IPC {old}", [])}
                if mapped and str(row["section_number"]) not in targets:
                    disagreements.append((old, mapped, row["section_number"]))
    assert not set(disagreements), sorted(set(disagreements))


@pytest.mark.parametrize(("query", "refs"), [
    ("What does Section 103 of the BNS say?", ["BNS 103"]),
    ("Section 103 BNSS", ["BNSS 103"]),
    ("s. 35 BNSS", ["BNSS 35"]),
    ("u/s 438 CrPC", ["BNSS 482"]),
    ("Section 302 IPC", ["BNS 103"]),
    ("IPC 498A", ["BNS 85", "BNS 86"]),
    ("Section 65B Evidence Act", ["BSA 63"]),
    ("Section 41A of the Code of Criminal Procedure", ["BNSS 35"]),
    ("Art. 32", ["ART 32"]),
    ("Article 19(1)(a)", ["ART 19"]),
    ("Article 21A", ["ART 21A"]),
    ("artcle 356", ["ART 356"]),
    ("Seventh Schedule", ["ART SCHEDULE_7"]),
    ("the Preamble", ["ART PREAMBLE"]),
])
def test_citations(query, refs):
    assert [c.ref for c in find_citations(query)] == refs


def test_bare_section_is_read_as_ipc_but_uncertain():
    (c,) = find_citations("dhara 302 kya hai")
    assert c.ref == "BNS 103" and not c.certain and c.via == "IPC 302"


def test_mode_prefix_never_reaches_retrieval():
    p = QueryParser().parse("ADVOCATE: punishment for murder")
    assert p.mode == "ADVOCATE" and "advocate" not in p.text.lower()
    assert "advocate" not in p.keywords


def test_typos_are_corrected_against_the_vocabulary_but_legal_words_are_left_alone():
    p = QueryParser({"punishment", "murder", "section"}).parse("punishmnet for murdr under section 5")
    assert p.corrections == {"punishmnet": "punishment", "murdr": "murder"}


def test_hindi_and_hinglish_expand_to_statutory_terms():
    assert {"murder", "punishment"} <= set(QueryParser().parse("हत्या की सजा क्या है").keywords)
    assert {"anticipatory", "bail"} <= set(QueryParser().parse("agrim zamanat kaise milegi").keywords)


@pytest.fixture(scope="module")
def engine():
    from app.search.engine import SearchEngine

    return SearchEngine.from_corpus()


@pytest.mark.parametrize(("query", "top"), [
    ("Section 103 of the BNS", "BNS 103"),
    ("Section 103 BNSS", "BNSS 103"),
    ("u/s 438 CrPC", "BNSS 482"),
    ("What did the court hold in kesavanand bharti?", "CASE kesavananda_bharati"),
])
def test_engine_puts_the_named_provision_first(engine, query, top):
    assert engine.search(query, k=3)[0]["_ref"] == top


def test_anti_defection_finds_the_tenth_schedule(engine):
    assert "ART SCHEDULE_10" in [h["_ref"] for h in engine.search("anti-defection law", k=3)]
