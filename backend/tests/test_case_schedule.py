import json
from pathlib import Path

import pytest

from app.case.schedule import Schedule, parse

BNSS = Path(__file__).parents[1] / "data" / "statutes" / "bnss.jsonl"

SAMPLE = (
    "THE FIRST SCHEDULE CLASSIFICATION OF OFFENCES EXPLANATORY NOTES: (1) ... Section Offence Punishment "
    "Cognizable or Non-cognizable Bailable or Non-bailable By what Court triable 1 2 3 4 5 6 "
    "49 Abetment of any offence. Same as for offence abetted. According as offence abetted is cognizable or "
    "non-cognizable. According as offence abetted is bailable or non-bailable. Court by which offence abetted is triable. "
    "85 Punishment for subjecting a married woman to cruelty. Imprisonment for 3 years and fine. Cognizable if "
    "information relating to the commission of the offence is given to an officer in charge of a police station. "
    "Non-bailable. Magistrate of the first class. "
    "103(1) Murder. Death or imprisonment for life and fine. Cognizable. Non-bailable. Court of Session. 1 2 3 4 5 6 "
    "318(2) Cheating. Imprisonment for 3 years, or fine, or both. Non-cognizable. Bailable. Any Magistrate. "
    "If committed by a public servant. Imprisonment for 5 years. Cognizable. Bailable Any Magistrate. "
    "II.--CLASSIFICATION OF OFFENCES AGAINST OTHER LAWS If punishable with death. Cognizable. Non-bailable. Court of Session."
)


def test_rows_columns_and_sections():
    s = Schedule(parse(SAMPLE))
    murder = s.classify("103(1)")[0]
    assert (murder.offence, murder.punishment, murder.cognizable, murder.bailable, murder.court) == (
        "Murder.", "Death or imprisonment for life and fine.", "Cognizable", "Non-bailable", "Court of Session")
    cruelty = s.classify("85")[0]
    assert cruelty.cognizable.startswith("Cognizable if information") and cruelty.court == "Magistrate of the first class"
    abetment = s.classify("49")[0]
    assert abetment.bailable.startswith("According as") and abetment.court.startswith("Court by which")
    cheating = s.classify("318")
    assert len(cheating) == 2 and cheating[1].offence == "If committed by a public servant."
    assert cheating[1].bailable == "Bailable" and cheating[1].court == "Any Magistrate"  # no full stop in the source
    assert set(s.by_section) == {"49", "85", "103", "318"}  # Part II (other laws) left out


@pytest.mark.skipif(not BNSS.exists(), reason="needs the corpus")
def test_the_real_schedule():
    rows = [json.loads(line) for line in BNSS.read_text(encoding="utf-8").splitlines()]
    s = Schedule(parse(" ".join(r["text"] for r in rows if r["section_number"] == "SCHEDULE")))
    assert len(s.by_section) >= 280
    known = {"103": ("Cognizable", "Non-bailable", "Court of Session"),
             "318": ("Non-cognizable", "Bailable", "Any Magistrate"),
             "115": ("Non-cognizable", "Bailable", "Any Magistrate"),
             "80": ("Cognizable", "Non-bailable", "Court of Session")}
    for sec, expected in known.items():
        r = s.classify(sec)[0]
        assert (r.cognizable, r.bailable, r.court) == expected, sec
    assert not any("OTHER LAWS" in r.offence for r in s.rows)
