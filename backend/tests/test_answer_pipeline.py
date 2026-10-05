from app.answer.evidence import PROVISION_TOKENS, assemble, provision_text, render, tokens
from app.answer.gate import ANSWER, CAVEAT, SOURCES_ONLY, Thresholds, decide, foreign
from app.answer.pipeline import prepare, readable_ref

T = Thresholds(low=-4.0, cos_low=0.66, high=6.0)


def hit(ref, ce=None, exact=False, cos=0.8, **kw):
    return {"_ref": ref, "_ce": ce, "_exact": exact, "_cos": cos, "chunk_id": f"{ref}::1", **kw}


# ── Gate ──────────────────────────────────────────────────────────────

def test_named_provision_is_always_answered():
    d = decide("Section 103 BNS", [hit("BNS 103", exact=True), hit("BNS 101", ce=-9)], t=T)
    assert d.outcome == ANSWER and d.evidence[0]["_ref"] == "BNS 103"


def test_unknown_citation_declines_without_sources():
    d = decide("Section 999 BNS", [hit("BNS 99", ce=1)], ["BNS 999"], t=T)
    assert d.outcome == SOURCES_ONLY and d.unknown == ["BNS 999"] and not d.evidence


def test_foreign_law_declines():
    assert decide("murder under UK law", [hit("BNS 103", ce=8)], t=T).outcome == SOURCES_ONLY
    assert foreign("divorce in the U.S.") and foreign("What is GDPR")
    assert not foreign("Can police arrest us without a warrant")       # "us" the pronoun
    assert not foreign("Compare murder law in the UK and India")        # the Indian half answers


def test_thresholds():
    assert decide("q", [hit("A 1", ce=7)], t=T).outcome == ANSWER
    assert decide("q", [hit("A 1", ce=0)], t=T).outcome == CAVEAT
    assert decide("q", [hit("A 1", ce=-6, cos=0.5)], t=T).outcome == SOURCES_ONLY
    # a low logit alone does not decline when the question is close to the corpus in meaning
    assert decide("q", [hit("A 1", ce=-6, cos=0.7)], t=T).outcome == CAVEAT


# ── Evidence ──────────────────────────────────────────────────────────

class Corpus:
    def __init__(self, provisions):
        self.provisions = provisions

    def provision(self, ref):
        return self.provisions.get(ref, [])


IPC_302 = {"chunk_id": "ipc::302::1", "citation": "Section 302 — Punishment for murder.",
           "act_title": "The Indian Penal Code", "status": "repealed",
           "replaced_by": "The Bharatiya Nyaya Sanhita, 2023", "corresponds_to": ["BNS 103"],
           "text": "Whoever commits murder shall be punished with death."}
BNS_103 = {"chunk_id": "bns::103::1", "citation": "Section 103 — Punishment for murder.",
           "act_title": "The Bharatiya Nyaya Sanhita, 2023", "status": "in force",
           "text": "(1) Whoever commits murder shall be punished with death or imprisonment for life."}


def test_repealed_provision_brings_its_replacement():
    corpus = Corpus({"IPC 302": [IPC_302], "BNS 103": [BNS_103]})
    ev = assemble([{**IPC_302, "_ref": "IPC 302"}], corpus)
    assert [e.ref for e in ev] == ["IPC 302", "BNS 103"]
    assert ev[1].linked and ev[1].relation.startswith("Corresponds to Section 302")
    assert ev[0].relation.startswith("Now Section 103") and ev[0].relation.endswith("[2]")
    text = render(ev)
    assert text.startswith("[1] Section 302 — Punishment for murder, The Indian Penal Code")
    assert "Status: Repealed; replaced by The Bharatiya Nyaya Sanhita, 2023" in text


def test_parts_are_joined_and_long_provisions_trimmed():
    short = [{"chunk_id": "x::1", "text": "one two"}, {"chunk_id": "x::2", "text": "three"}]
    assert provision_text(short, "x::2") == ("one two three", False)
    long = [{"chunk_id": f"y::{i}", "text": f"p{i} " + "word " * 300} for i in range(1, 5)]
    text, trimmed = provision_text(long, "y::3")
    assert trimmed and text.startswith("p1 ") and " […] p3 " in text
    assert tokens(text) <= PROVISION_TOKENS + 5


def test_each_provision_once():
    corpus = Corpus({"BNS 103": [BNS_103]})
    ev = assemble([{**BNS_103, "_ref": "BNS 103"}, {**BNS_103, "_ref": "BNS 103"}], corpus)
    assert len(ev) == 1 and ev[0].status == "In force"


# ── Pipeline ──────────────────────────────────────────────────────────

class Engine(Corpus):
    def __init__(self, hits, unknown=()):
        super().__init__({"IPC 302": [IPC_302], "BNS 103": [BNS_103]})
        self.hits, self.unknown = hits, list(unknown)

    def search(self, query, k):
        return self.hits[:k]

    def unknown_citations(self, query):
        return self.unknown


def test_prepare_answers_with_numbered_evidence():
    p = prepare("302 IPC", Engine([{**IPC_302, **hit("IPC 302", exact=True)}]), 15)
    assert p.outcome == ANSWER
    assert [s["_ref"] for s in p.sources] == ["IPC 302", "BNS 103"]
    assert "[2] Section 103" in p.context


def test_prepare_unknown_citation():
    p = prepare("Section 999 BNS", Engine([hit("BNS 99", ce=1)], ["BNS 999"]), 15)
    assert p.outcome == SOURCES_ONLY and p.sources == [] and p.context == ""
    assert p.fallback.startswith("**Section 999 of the BNS is not in ParAILegal's corpus.**")


def test_readable_ref():
    assert readable_ref("ART 512") == "Article 512"
    assert readable_ref("code_on_wages_2019 3") == "Section 3 of the Code On Wages 2019"
