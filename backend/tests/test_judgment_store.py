from app.judgments.store import JudgmentStore, catchline, citation, holdings, passages

MODERN = {
    "id": "2023_16_1022_1036", "title": "PRADEEP v. THE STATE OF HARYANA", "decided": "2023-07-05",
    "scr": "[2023] 16 S.C.R. 1022", "insc": "2023 INSC 599", "bench_size": "2 Judges",
    "summary": "Evidence – Testimony of child witness – Sole basis of conviction – When not proper",
    "headnote": ("PRADEEP v. THE STATE OF HARYANA (Criminal Appeal No. 553 of 2012) JULY 05, 2023 Evidence – "
                 "Testimony of child witness – Held: In view of s.118, Evidence Act ... Allowing the appeal, the Court "
                 "HELD: 1.1 Corroboration of the testimony of a child witness is not a rule but a measure of caution "
                 "and prudence. [Paras 8 and 9][1028-H; 1029-A-D] 1.2 In the facts of the case, the preliminary "
                 "examination of the minor was very sketchy. [Paras 14-16][1030-H] CRIMINAL APPELLATE "
                 "JURISDICTION: Criminal Appeal No. 553 of 2012."),
    "paragraphs": [{"n": 0, "text": "The Judgment of the Court was delivered by ABHAY S. OKA, J."},
                   {"n": 8, "text": "A child witness of tender age is easily susceptible to tutoring."},
                   {"n": 9, "text": "The appellant was convicted of murder under section 302."}],
    "provisions": [{"ref": "IPC 302", "count": 2}, {"ref": "IEA 118", "count": 2}],
}
OLD = {
    "id": "1950_1_15_25", "title": "RAM KRISHNA v. SECRETARY, MUNICIPAL COMMITTEE", "decided": "1950-03-14",
    "scr": "[1950] 1 S.C.R. 15",
    "headnote": ("[SHRI HARILAL KANIA C.J., SAIYID FAZL ALI and S.R. DAS JJ.] Central Excises and Salt Act (I of "
                 "1944), ss. 2, 3-Import of tobacco within municipality-Levy of octroi duty-Legality. Section 66 "
                 "empowered municipalities to levy octroi. Held, that excise duty and octroi were taxes essentially "
                 "different in their nature. Held further, that nothing in the Act was contrary to the levy. "
                 "APPEAL from the High Court of Judicature at Nagpur."),
    "paragraphs": [{"n": 16, "page": True, "text": "KANIA C. J.-This is an appeal about octroi on tobacco."}],
    "provisions": [{"ref": "ART 14", "count": 1}],
}


def test_numbered_holdings_with_the_paragraphs_they_rest_on():
    held = holdings(MODERN)
    assert [h["paras"] for h in held] == ["Paras 8 and 9", "Paras 14-16"]
    assert held[0]["text"].startswith("Corroboration of the testimony") and "1028-H" not in held[0]["text"]
    assert "JURISDICTION" not in held[1]["text"]
    assert citation(MODERN) == "[2023] 16 SCR 1022 : 2023 INSC 599"


def test_older_reports_held_that():
    held = holdings(OLD)
    assert [h["text"][:30] for h in held] == ["excise duty and octroi were ta", "nothing in the Act was contrar"]
    assert catchline(OLD).startswith("Central Excises and Salt Act")  # the bench is not part of it


def test_passages_find_the_matching_paragraph():
    [p] = passages(MODERN, "child witness tutoring", n=1)
    assert p["n"] == 8 and not p["page"]


def test_search_by_words_and_by_provision(tmp_path):
    path = tmp_path / "judgments.sqlite"
    JudgmentStore.build([MODERN, OLD], path)
    store = JudgmentStore.open(tmp_path, dense_key=None, citator_path=tmp_path / "none.json")
    assert [h.id for h in store.search("octroi on tobacco")] == ["1950_1_15_25"]
    [hit] = store.search("", refs=["IPC 302"])
    assert hit.id == "2023_16_1022_1036" and hit.matched_refs == ["IPC 302"]
    assert [h.id for h in store.search("child witness octroi", before="2000-01-01")] == ["1950_1_15_25"]
    assert store.record("1950_1_15_25")["scr"] == "[1950] 1 S.C.R. 15"
