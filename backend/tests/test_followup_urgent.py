import json
import sqlite3
from pathlib import Path

import pytest

from app.answer import urgent
from app.answer.evidence import Evidence
from app.answer.followup import Previous, is_follow_up
from app.answer.pipeline import prepare
from app.answer.verify import verify
from app.search.query import QueryParser

PACK = Path(__file__).parents[1] / "data" / "pack" / "corpus.sqlite"


# ── Follow-ups ────────────────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    "what is the punishment for it?", "is it bailable?", "and if the cheque was post-dated?",
    "what about the old IPC section?", "punishment?", "exceptions", "Is that offence compoundable?",
])
def test_follow_ups(q):
    assert is_follow_up(q, has_citation=False)


@pytest.mark.parametrize("q", [
    "cheque bounce", "Can police arrest without a warrant?", "what is anticipatory bail",
    "My landlord is not returning my deposit and says it will be adjusted against repairs he did",
])
def test_fresh_questions(q):
    assert not is_follow_up(q, has_citation=False)


def test_a_named_provision_starts_fresh():
    assert not is_follow_up("and what does Section 318 say?", has_citation=True)


class Engine:
    def __init__(self):
        self.searched = None

    def parse(self, q):
        return QueryParser({}).parse(q)

    def search(self, query, k, context_refs=None):
        self.searched = (query, context_refs)
        return [{"_ref": "BNS 318", "_exact": False, "_ce": 8.0, "_cos": 0.9, "chunk_id": "bns::318::1",
                 "citation": "Section 318 — Cheating", "text": "Whoever cheats shall be punished ..."}]

    def unknown_citations(self, q):
        return []

    def provision(self, ref):
        return []

    def refs_of_chunks(self, ids):
        return ["BNS 318"] if "bns::318::1" in ids else []


def test_follow_up_searches_with_the_earlier_question_and_its_provisions():
    e = Engine()
    p = prepare("what is the punishment for it?", e, 15, previous=Previous("SUMMARISE: what is cheating?", ["bns::318::1"]))
    assert p.earlier == "what is cheating?"
    assert e.searched == ("what is cheating? what is the punishment for it?", ["BNS 318"])


def test_fresh_question_ignores_the_previous_one():
    e = Engine()
    p = prepare("cheque bounce", e, 15, previous=Previous("what is cheating?", ["bns::318::1"]))
    assert p.earlier == "" and e.searched == ("cheque bounce", None)


# ── Urgent-help notices ───────────────────────────────────────────────

@pytest.mark.parametrize("q, keys", [
    ("Police arrested my brother last night, what can we do?", ["arrest"]),
    ("my husband beats me and demands dowry", ["violence"]),
    ("I want to end my life", ["self_harm"]),
    ("Someone scammed me, money got debited from my account", ["cyber_fraud"]),
    ("tomorrow is the last date to file my appeal", ["deadline"]),
    ("What is the punishment for domestic violence?", []),        # a student's question
    ("Can police arrest without a warrant?", []),
    ("What is the punishment for abetment of suicide?", []),
])
def test_notices(q, keys):
    assert [n.key for n in urgent.notices(q)] == keys


def test_urgent_question_is_answered_with_a_caveat_not_declined():
    class Weak(Engine):
        def search(self, query, k, context_refs=None):
            return [dict(h, _ce=-9.0, _cos=0.5) for h in super().search(query, k, context_refs)]

    p = prepare("Police arrested my brother last night, what can we do?", Weak(), 15)
    assert p.outcome == "caveat" and p.notice.startswith("> **If you or someone close to you has been arrested")
    assert prepare("What is the boiling point of water at sea level?", Weak(), 15).outcome == "sources_only"


def test_notice_text_is_not_checked_by_the_verifier():
    ev = [Evidence(1, "BNS 85", "Section 85 — Cruelty", "In force", "shall be punished with imprisonment ...", {})]
    answer = urgent.block("my husband beats me") + "Cruelty is punishable with imprisonment [1]."
    _, v = verify(answer, ev, "my husband beats me")
    assert not v.unsupported_figures and not v.unsupported_provisions  # 112, 181, 15100, s.12 are ours


@pytest.mark.skipif(not PACK.exists(), reason="needs the data pack (built on first start)")
def test_every_provision_a_notice_cites_is_in_the_corpus():
    from app.search.engine import ref_of

    chunks = json.loads(sqlite3.connect(PACK).execute("SELECT data FROM chunk_data").fetchone()[0])
    titles = {}
    for c in chunks:
        titles.setdefault(ref_of(c), (c.get("citation") or "").split("\n")[0].lower())
    for n in urgent.NOTICES:
        for ref, word in n.cites:
            assert ref in titles, ref
            assert word in titles[ref], (ref, titles[ref])
