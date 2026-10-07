from types import SimpleNamespace

from app.case.offence_index import OffenceIndex, lay_terms
from app.case.schedule import Classification, Schedule


def chunk(act, section, title, text, status="in force", document_title=""):
    return {"_domain": "statutes", "_act": act, "section": section, "section_title": title, "text": text,
            "status": status, "document_title": document_title, "citation": f"Section {section} — {title}"}


def row(section, offence):
    return Classification(section, "", offence, "Imprisonment", "Cognizable", "Non-bailable", "Court of Session")


CHUNKS = [
    chunk("BNS", "63", "Rape", "A man is said to commit rape if he has sexual intercourse with a woman against her will."),
    chunk("BNS", "64", "Punishment for rape", "Whoever commits rape shall be punished with rigorous imprisonment."),
    chunk("BNS", "115", "Voluntarily causing hurt", "Whoever voluntarily causes hurt shall be punished."),
    chunk("BNS", "2", "Definitions", "In this Sanhita words have these meanings."),
    chunk("BNS", "61", "Criminal conspiracy", "Whoever is a party to a criminal conspiracy shall be punished."),
    chunk("dowry_prohibition_act_1961", "4", "Penalty for demanding dowry",
          "If any person demands any dowry he shall be punishable with imprisonment.",
          document_title="The Dowry Prohibition Act, 1961"),
    chunk("companies_act_2013", "447", "Punishment for fraud", "Any person guilty of fraud shall be punishable with imprisonment.",
          document_title="The Companies Act, 2013"),
    chunk("companies_act_2013", "SCHEDULE", "Schedule", "Table of fees punishable with fine.",
          document_title="The Companies Act, 2013"),
    chunk("ipc", "302", "Punishment for murder", "Whoever commits murder shall be punished.", status="repealed"),
]
SCHEDULE = Schedule([row("64", "Rape"), row("115", "Voluntarily causing hurt"), row("61", "Criminal conspiracy")])


def index():
    return OffenceIndex(SimpleNamespace(chunks=CHUNKS, dense=None), SCHEDULE)


def test_only_offences_are_indexed_and_punishment_carries_its_definition():
    idx = index()
    assert set(idx.refs) == {"BNS 64", "BNS 115", "BNS 61", "dowry_prohibition_act_1961 4", "companies_act_2013 447"}
    rape = idx.refs.index("BNS 64")
    assert [CHUNKS[i]["section"] for i in idx.chunks_of[rape]] == ["64", "63"]  # section 63, "Rape", comes with it


def test_everyday_words_reach_the_law():
    assert "hurt" in lay_terms("the accused slapped and punched him")
    assert "dowry" in lay_terms("the groom's father demanded a motorcycle as a condition of the marriage")
    assert "cannabis" in lay_terms("he carried 25 kg of ganja")
    assert lay_terms("the weather was fine") == ""
    found = [r for r, _ in index().search("he slapped and punched the complainant")]
    assert found[0] == "BNS 115"


def test_general_sections_and_off_topic_special_acts_give_way():
    idx = index()
    # conspiracy (BNS 61) only when the facts speak of it
    assert "BNS 61" not in [r for r, _ in idx.search("whoever is a party to a plan")]
    assert "BNS 61" in [r for r, _ in idx.search("they conspired together as a party to a criminal conspiracy")]
    # a special Act counts fully only when the facts touch its subject
    on = dict(idx.search("the husband demanded dowry"))
    off = dict(idx.search("the cashier was guilty of fraud with the money"))
    assert on["dowry_prohibition_act_1961 4"] > 0 and "companies_act_2013 447" in off
    assert off["companies_act_2013 447"] < dict(idx.search("the company director was guilty of fraud"))["companies_act_2013 447"]
