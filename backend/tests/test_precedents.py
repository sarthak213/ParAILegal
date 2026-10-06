from app.case.precedents import check_points, evidence_text, find, older_refs
from app.judgments.law_at_time import History
from app.judgments.store import JudgmentStore
from tests.test_judgment_store import MODERN


class Engine:
    chunks = [{"_domain": "statutes", "_act": "IPC", "section": "302", "corresponds_to": ["BNS 103"]},
              {"_domain": "statutes", "_act": "IPC", "section": "498A", "corresponds_to": ["BNS 85", "BNS 86"]}]

    def provision(self, ref):
        return [{"section_title": "Punishment for murder.", "status": "repealed", "corresponds_to": ["BNS 103"],
                 "replaced_by": "The Bharatiya Nyaya Sanhita, 2023", "text": "Whoever commits murder ..."}] \
            if ref == "IPC 302" else []


def test_new_code_refs_bring_the_old_ones():
    assert older_refs(["BNS 103", "BNS 85"], Engine()) == ["BNS 103", "BNS 85", "IPC 302", "IPC 498A"]


def test_a_point_needs_quotes_that_are_really_there():
    ours = "The accused stabbed the deceased in the market. His son, aged eight, saw it."
    theirs = "Testimony of child witness – sole basis of conviction"
    kept, dropped = check_points([
        {"point": "a child is the only eyewitness", "our_fact": "His son, aged eight, saw it",
         "their_fact": "Testimony of child witness"},
        {"point": "invented", "our_fact": "the accused confessed", "their_fact": ""},
        {"point": "nothing quoted", "our_fact": "", "their_fact": ""},
    ], ours, theirs)
    assert [p["point"] for p in kept] == ["a child is the only eyewitness"] and dropped == 2


def test_find_a_precedent_through_the_new_code(tmp_path):
    JudgmentStore.build([MODERN], tmp_path / "judgments.sqlite")
    store = JudgmentStore.open(tmp_path, dense_key=None, citator_path=tmp_path / "none.json")
    [p] = find("The accused stabbed the deceased; a child saw it.", ["BNS 103"], ["the accused stabbed the deceased"],
               store, Engine(), History(directory=tmp_path), k=3)
    assert p["id"] == MODERN["id"] and p["matched_refs"] == ["IPC 302"]
    assert p["holdings"][0]["paras"] == "Paras 8 and 9"
    assert p["law_at_time"][0]["now"] == ["BNS 103"] and "IPC s.302" in p["law_at_time"][0]["note"]


def test_evidence_text_for_the_brief():
    comparison = {"similar": [{"point": "a child is the only eyewitness"}],
                  "different": [{"point": "here the child was not examined in court"}], "bearing": "Caution applies."}
    status = {"overruled_noted_in": [{"title": "X v. Y", "decided": "2024-01-01"}]}
    text = evidence_text(MODERN, comparison, ["Decided on 2023-07-05 under IPC s.302. ..."], status)
    assert "Facts similar: a child is the only eyewitness" in text and "Facts different: here" in text
    assert "Law at the time: Decided on 2023-07-05" in text and "Caution: noted as overruled in X v. Y" in text
