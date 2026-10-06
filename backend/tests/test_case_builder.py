import asyncio

from app.case import builder
from app.case.elements import OFFENCES
from app.case.schedule import Schedule, parse

FACTS = ("Rakesh beat Priya with a belt on 12 June 2023. He sent WhatsApp messages threatening to finish her "
         "if the money was not paid.")


def run(coro):
    return asyncio.run(coro)


def test_a_quote_not_in_the_facts_is_not_accepted(monkeypatch):
    async def fake(base_url, messages, schema, max_tokens, temperature=0.1):
        return {"elements": [
            {"n": 1, "status": "shown", "fact": "threatening to finish her"},          # in the facts
            {"n": 2, "status": "shown", "fact": "he said he would kill her family"},  # invented
        ]}
    monkeypatch.setattr(builder, "complete_json", fake)
    checks = run(builder.check_elements(FACTS, OFFENCES["BNS 351"], "http://x"))
    assert checks[0]["status"] == "shown" and checks[0]["quote_checked"]
    assert checks[1]["status"] == "unclear" and checks[1]["fact"] == "" and not checks[1]["quote_checked"]
    assert checks[2]["status"] == "unclear"  # the model said nothing about element 3


def test_a_tick_needs_a_fact(monkeypatch):
    async def fake(*a, **k):
        return {"elements": [{"n": 1, "status": "shown", "fact": ""}]}
    monkeypatch.setattr(builder, "complete_json", fake)
    assert run(builder.check_elements(FACTS, OFFENCES["BNS 351"], "http://x"))[0]["status"] == "unclear"


def check(kind, status, group=""):
    return {"kind": kind, "status": status, "group": group}


def test_element_summary():
    S, N, U = builder.SHOWN, builder.NOT_SHOWN, builder.UNCLEAR
    assert builder.element_summary([check("element", S), check("any_of", N, "g"), check("any_of", S, "g")]) == "met"
    assert builder.element_summary([check("element", S), check("any_of", U, "g"), check("any_of", N, "g")]) == "open"
    assert builder.element_summary([check("element", N), check("any_of", S, "g")]) == "not met"
    assert builder.element_summary([check("element", S), check("aggravation", N)]) == "met"  # heavier form only


def test_definitions_point_to_the_offence():
    assert builder.DEFINED_IN["BNS 101"] == "BNS 103" and builder.DEFINED_IN["BNS 86"] == "BNS 85"


SCHEDULE = Schedule(parse("By what Court triable 351(2) Criminal intimidation. Imprisonment for 2 years, or fine, "
                          "or both. Non-cognizable. Bailable. Any Magistrate. 127(2) Wrongful confinement. "
                          "Imprisonment for 1 year. Cognizable. Bailable. Any Magistrate."))


class Engine:
    results = {"threat": ["BNS 351", "BNSS 173"], "locked": ["BNS 127", "BNS 351"]}

    def search(self, query, k):
        refs = next((v for key, v in self.results.items() if key in query), [])
        return [{"_ref": r, "_rank": i + 1, "citation": f"Section {r.split()[1]} — x"} for i, r in enumerate(refs)]

    def provision(self, ref):
        return [{"citation": f"Section {ref.split()[1]} — Title", "document_title": "The Bharatiya Nyaya Sanhita, 2023",
                 "text": "shall be punished"}]


def test_offences_from_acts():
    found = builder.offences("facts", Engine(), SCHEDULE, acts=["the husband sent a threat", "she was locked in"])
    refs = [o["ref"] for o in found]
    assert refs[:2] == ["BNS 351", "BNS 127"] and "BNSS 173" not in refs  # procedure is not an offence
    assert found[0]["classification"][0]["court"] == "Any Magistrate" and found[0]["has_checklist"]


def test_procedure_table_and_checklist_text():
    table = builder.procedure_table(["BNS 351"], SCHEDULE)
    assert "| BNS 351(2) | Criminal intimidation | Imprisonment for 2 years, or fine, or both | Non-cognizable | Bailable | Any Magistrate |" in table
    text = builder.checklist_text({"BNS 351": [dict(check("element", builder.SHOWN), label="A threat", fact="finish her")]})
    assert text.startswith("Criminal intimidation (BNS 351): met") and '"finish her"' in text


def test_acts_the_facts_do_not_bear_out_are_dropped(monkeypatch):
    facts = ("Meena married Rakesh in March 2021. From the start Rakesh and his mother demanded a car and Rs. 5 lakh "
             "from Meena's parents, and beat her when the money was not paid. On 14 February 2023 she died of burns.")
    acts = ["Rakesh demanded a car and Rs. 5 lakh from Meena's parents", "Rakesh beat Meena when the money was not paid",
            "the husband and his mother demanded money as dowry",
            "Rakesh threatened by message to kill his wife unless money was paid"]  # copied from a prompt example

    async def fake(*_a, **_k):
        return {"parties": [], "events": [], "harm": [], "property": [], "documents": [], "acts": acts}
    monkeypatch.setattr(builder, "complete_json", fake)
    out = run(builder.structure_facts(facts, "http://model"))
    assert out["acts"] == acts[:3] and out["acts_dropped"] == acts[3:]


def test_an_offence_added_by_number_comes_first():
    found = builder.offences("facts", Engine(), SCHEDULE, acts=["the husband sent a threat"], include=["BNS 85"])
    assert found[0]["ref"] == "BNS 85" and found[0]["added"] and found[0]["has_checklist"]
    assert [o["ref"] for o in found].count("BNS 85") == 1 and not found[1]["added"]
