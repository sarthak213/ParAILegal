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


def test_a_likeness_must_quote_both_texts():
    ours, theirs = "The in-laws taunted her daily. She hanged herself.", "Victim committed suicide by hanging herself."
    kept, dropped = check_points([
        {"point": "both died by hanging", "our_fact": "She hanged herself", "their_fact": "hanging herself"},
        {"point": "both charged under s.306", "our_fact": "", "their_fact": "Victim committed suicide"},
    ], ours, theirs, both=True)
    assert [p["point"] for p in kept] == ["both died by hanging"] and dropped == 1


def test_shortened_quotes_count_and_points_about_the_proceedings_do_not():
    from app.case.precedents import quoted, _norm

    theirs = "P.W.1 deposed about the demand. The respondent asked him to pay at least Rs. 10,000 in the hotel."
    assert quoted("asked him to pay at least Rs. 10,000... in the hotel", _norm(theirs))
    assert not quoted("in the hotel... asked him to pay", _norm(theirs))  # pieces out of order
    points = [
        {"point": "A public servant demanded money to approve an official act.",
         "our_fact": "asked the complainant for Rs. 10,000", "their_fact": "asked him to pay at least Rs. 10,000... in the hotel"},
        {"point": "The judgment discusses an acquittal by the High Court.", "our_fact": "", "their_fact": "P.W.1 deposed about the demand"},
    ]
    kept, dropped = check_points(points, "The clerk asked the complainant for Rs. 10,000 to approve his plan.", theirs)
    assert [p["point"] for p in kept] == [points[0]["point"]] and dropped == 1


def test_the_check_drops_likenesses_untrue_of_either_text():
    from app.case.precedents import apply_checks

    result = {"similar": [{"point": "a"}, {"point": "b"}, {"point": "c"}], "different": [{"point": "d"}],
              "bearing": "", "dropped": 2}
    out = apply_checks(result, {"checks": [{"n": 1, "ours": "yes", "theirs": "yes"}, {"n": 2, "ours": "no", "theirs": "yes"},
                                           {"n": 3, "ours": "yes", "theirs": "no"}]})
    assert [p["point"] for p in out["similar"]] == ["a"] and out["dropped"] == 4
    assert out["different"] == result["different"]  # differences are not checked
    assert apply_checks(result, {})["similar"] == result["similar"]  # no check reply: nothing dropped


def test_a_long_headnote_is_cut_to_fit_the_model():
    from app.case.precedents import HOLDINGS_CHARS, _clip

    text = "The Court held this. " * 200
    assert len(_clip(text, 1500)) <= 1500 and _clip(text, 1500).endswith(".")
    assert HOLDINGS_CHARS < 8192 * 3  # well inside the model's context, with the facts and the reply


def test_the_brief_reads_each_point_with_its_quotes():
    comparison = {"similar": [{"point": "both died by hanging", "our_fact": "She hanged herself",
                               "their_fact": "hanging herself"}], "different": [], "bearing": ""}
    text = evidence_text(MODERN, comparison, [], {})
    assert 'Facts similar: both died by hanging (our facts: "She hanged herself"; the judgment: "hanging herself")' in text


def test_a_quote_may_correct_a_scan_but_not_change_the_words():
    from app.case.precedents import _norm, quoted

    scan = _norm("which had the effect of creating an intimidatig or hostile working environment f11r her. "
                 "Altercation between the c parties resulted in fatal blow to victim")
    assert quoted("had the effect of creating an intimidating or hostile working environment for her", scan)
    assert quoted("Altercation between the parties resulted in fatal blow to victim", scan)
    assert not quoted("had the effect of creating a friendly and supportive working environment for her", scan)
    assert not quoted("the c partys", scan)  # short: exact only


def test_a_likeness_cannot_lend_one_case_the_others_people():
    ours = "Rakesh and his mother demanded a car from the wife's parents and beat her."
    theirs = "The husband and father-in-law demanded money from her and tormented her."
    kept, dropped = check_points([
        {"point": "In both, the husband and father-in-law demanded money.", "our_fact": "Rakesh and his mother demanded a car",
         "their_fact": "The husband and father-in-law demanded money"},
        {"point": "In both, money was demanded from the woman.", "our_fact": "demanded a car",
         "their_fact": "demanded money from her"},
    ], ours, theirs, both=True)
    assert [p["point"] for p in kept] == ["In both, money was demanded from the woman."] and dropped == 1
