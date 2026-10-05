"""Query understanding, the old→new code tables, and the hybrid engine (offline, no network)."""

import json
from pathlib import Path

import pytest

from app.search.legal_data import CRPC_TO_BNSS, IEA_TO_BSA, IPC_TO_BNS, SPLIT_TARGETS
from app.search.query import QueryParser, find_citations

STATUTES = Path(__file__).resolve().parents[1] / "data" / "statutes"


def corpus_sections(code: str) -> set[str]:
    path = STATUTES / f"{code}.jsonl"
    if not path.exists():
        pytest.skip(f"corpus not built: {path} (scripts/build_corpus.py)")
    with path.open(encoding="utf-8") as fh:
        return {str(json.loads(line)["section_number"]) for line in fh}


@pytest.mark.parametrize(("table", "old", "new"), [
    (IPC_TO_BNS, "ipc", "bns"),
    (CRPC_TO_BNSS, "crpc", "bnss"),
    (IEA_TO_BSA, "iea", "bsa"),
])
def test_every_mapped_section_exists_on_both_sides(table, old, new):
    old_sections, new_sections = corpus_sections(old), corpus_sections(new)
    split = {t.split()[1] for k, v in SPLIT_TARGETS.items() if k.split()[0] == old.upper() for t in v}
    missing_new = sorted(({n for n in table.values()} | split) - new_sections)
    missing_old = sorted(set(table) - old_sections)
    assert not missing_new, f"targets not in {new}: {missing_new}"
    assert not missing_old, f"sources not in {old}: {missing_old}"


def test_new_and_old_sections_link_to_each_other():
    rows = {}
    for code in ("ipc", "bns"):
        corpus_sections(code)
        with (STATUTES / f"{code}.jsonl").open(encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                rows[f"{r['act_code']} {r['section_number']}"] = r
    assert "IPC 302" in rows["BNS 103"]["corresponds_to"]
    assert "BNS 103" in rows["IPC 302"]["corresponds_to"]
    assert {"BNS 85", "BNS 86"} <= set(rows["IPC 498A"]["corresponds_to"])
    assert rows["IPC 302"]["status"] == "repealed" and rows["BNS 103"]["status"] == "in force"


@pytest.mark.parametrize(("query", "refs"), [
    ("What does Section 103 of the BNS say?", ["BNS 103"]),
    ("Section 103 BNSS", ["BNSS 103"]),
    ("s. 35 BNSS", ["BNSS 35"]),
    ("u/s 438 CrPC", ["BNSS 482", "CRPC 438"]),
    ("Section 302 IPC", ["BNS 103", "IPC 302"]),
    ("IPC 498A", ["BNS 85", "BNS 86", "IPC 498A"]),
    ("Section 65B Evidence Act", ["BSA 63", "IEA 65B"]),
    ("Section 41A of the Code of Criminal Procedure", ["BNSS 35", "CRPC 41A"]),
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
    c, old = find_citations("dhara 302 kya hai")
    assert c.ref == "BNS 103" and not c.certain and c.via == "IPC 302"
    assert old.ref == "IPC 302" and not old.certain


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
