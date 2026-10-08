from app.history.amendments import INSERTED, SUBSTITUTED, Amendment
from app.judgments.law_at_time import History, law_at_time


def amendment(act, section, action, by, effective, new_text, old_text=None):
    return Amendment(act, section, 1, 1, action, by, effective, new_text, old_text, "")


class Engine:
    provisions = {
        "IPC 302": [{"section_title": "Punishment for murder.", "status": "repealed",
                     "replaced_by": "The Bharatiya Nyaya Sanhita, 2023", "corresponds_to": ["BNS 103"],
                     "text": "Whoever commits murder shall be punished with death, or [imprisonment for life], and fine."}],
        "IPC 498A": [{"section_title": "Husband or relative of husband of a woman subjecting her to cruelty.",
                      "status": "repealed", "replaced_by": "The Bharatiya Nyaya Sanhita, 2023",
                      "corresponds_to": ["BNS 85", "BNS 86"], "text": "Whoever, being the husband ..."}],
    }

    def provision(self, ref):
        return self.provisions.get(ref, [])


def history():
    h = History(directory=__import__("pathlib").Path("does-not-exist"))
    h.by_section[("IPC", "302")].append(amendment("IPC", "302", SUBSTITUTED, "Act 26 of 1955", "1956-01-01",
                                                  "imprisonment for life", "transportation for life"))
    h.by_section[("IPC", "498A")].append(amendment("IPC", "498A", INSERTED, "Act 46 of 1983", "1983-12-25",
                                                   "498A. Husband or relative of husband ..."))
    return h


def judgment(decided, *refs):
    return {"decided": decided, "provisions": [{"ref": r, "count": 3} for r in refs]}


def test_a_ruling_before_an_amendment_sees_the_old_words():
    [p] = law_at_time(judgment("1950-06-01", "IPC 302"), Engine(), history())
    assert "transportation for life" in p.text_then and p.complete
    assert p.amended_since == [{"action": SUBSTITUTED, "by": "Act 26 of 1955", "effective": "1956-01-01"}]
    assert p.now == ["BNS 103"]
    assert p.note == ("Decided on 1950-06-01 under IPC s.302. Amended since the decision (substituted by Act 26 of 1955 "
                      "from 1956-01-01). Now repealed; replaced by The Bharatiya Nyaya Sanhita, 2023 (BNS s.103).")


def test_a_section_not_yet_enacted_and_one_unchanged_since():
    early, later = (law_at_time(judgment(d, "IPC 498A"), Engine(), history())[0] for d in ("1980-01-01", "2005-01-01"))
    assert early.text_then is None and "did not exist yet on 1980-01-01" in early.note
    assert later.text_then == "Whoever, being the husband ..." and not later.amended_since
    assert "Not amended between the decision and today's text (or its repeal)." in later.note
    assert "(BNS s.85, BNS s.86)" in later.note


def test_a_provision_outside_the_corpus():
    [p] = law_at_time(judgment("2023-07-05", "IPC 999"), Engine(), history())
    assert p.note == "IPC s.999 is not in ParAILegal's corpus."


def test_an_earlier_wording_from_an_archived_copy(tmp_path):
    import json
    a = amendment("IPC", "302", SUBSTITUTED, "Act 26 of 1955", "1956-01-01", "imprisonment for life")
    (tmp_path / "ipc.jsonl").write_text(json.dumps(a.as_dict()) + "\n", encoding="utf-8")
    (tmp_path / "_filled.jsonl").write_text(json.dumps({
        "act": "IPC", "section": "302", "by": "Act 26 of 1955", "note": "", "old_text": "transportation for life",
        "source": "archived India Code copy saved 2007-08-21"}) + "\n", encoding="utf-8")
    [p] = law_at_time(judgment("1950-06-01", "IPC 302"), Engine(), History(directory=tmp_path))
    assert "transportation for life" in p.text_then and p.complete
    assert "Earlier wording taken from an archived India Code copy saved 2007-08-21, not the Gazette." in p.note


def test_an_omitted_article():
    class Constitution:
        def provision(self, ref):
            return [{"section_title": "Compulsory acquisition of property", "status": "in force",
                     "text": "Compulsory acquisition of property..—Omitted by the Constitution (Forty-fourth Amendment) "
                             "Act, 1978, s. 6 (w.e.f. 20-6-1979)."}]
    h = History(directory=__import__("pathlib").Path("does-not-exist"))
    h.by_section[("ART", "31")].append(Amendment("ART", "31", 1, 1, "omitted", "Constitution (Forty-fourth Amendment) "
                                                 "Act, 1978", "1979-06-20", "", None, "Omitted by ..."))
    [p] = law_at_time(judgment("1975-11-07", "ART 31"), Constitution(), h)
    assert p.status_today == "omitted" and p.note.endswith("Now omitted.")
    assert "omitted by Constitution (Forty-fourth Amendment) Act, 1978 from 1979-06-20" in p.note
