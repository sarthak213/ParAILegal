import sqlite3
from types import SimpleNamespace

from app.answer.context import Context, cases, history, relation
from app.answer.evidence import assemble, render
from app.history.amendments import INSERTED, SUBSTITUTED, Amendment

IPC_499 = {"status": "repealed", "citation": "Section 499 — Defamation", "act_title": "The Indian Penal Code",
           "text": "Whoever by words makes any imputation...", "corresponds_to": ["BNS 356"]}
IPC_500 = {**IPC_499, "citation": "Section 500 — Punishment for defamation", "corresponds_to": ["BNS 356"]}
BNS_356 = {"status": "in force", "citation": "Section 356 — Defamation", "act_title": "The Bharatiya Nyaya Sanhita, 2023",
           "text": "(1) Whoever, by words either spoken or intended to be read...", "corresponds_to": ["IPC 499", "IPC 500"]}
CRPC_154 = {"status": "repealed", "citation": "Section 154 — Information in cognizable cases",
            "act_title": "The Code of Criminal Procedure, 1973", "text": "Every information...", "corresponds_to": ["BNSS 173"]}
BNSS_173 = {"status": "in force", "citation": "Section 173 — Information in cognizable cases",
            "act_title": "The Bharatiya Nagarik Suraksha Sanhita, 2023", "text": "(1) Every information...",
            "corresponds_to": ["CRPC 154"]}


class Corpus:
    rows = {"IPC 499": [IPC_499], "IPC 500": [IPC_500], "BNS 356": [BNS_356], "CRPC 154": [CRPC_154], "BNSS 173": [BNSS_173]}

    def provision(self, ref):
        return self.rows.get(ref, [])


def test_a_new_section_says_what_it_replaced_and_an_old_one_what_replaced_it():
    assert relation(BNSS_173, Corpus(), {}) == (
        "Formerly Section 154 — Information in cognizable cases, The Code of Criminal Procedure, 1973")
    assert relation(CRPC_154, Corpus(), {"BNSS 173": 2}).endswith("Bharatiya Nagarik Suraksha Sanhita, 2023 [2]")
    # merged: the old numbers only
    assert relation(BNS_356, Corpus(), {}) == "Formerly Sections 499 and 500 of The Indian Penal Code"


def test_history_gives_the_latest_changes_first():
    h = SimpleNamespace(of=lambda ref: [
        Amendment("IPC", "304B", 1, 1, INSERTED, "Act 43 of 1986", "1986-11-19", "", None, ""),
        Amendment("IPC", "304B", 2, 1, SUBSTITUTED, "Act 2 of 2000", "2000-03-01", "", None, ""),
        Amendment("IPC", "304B", 3, 1, "commenced", "", "1987-01-01", "", None, ""),  # not a change
    ])
    assert history("IPC 304B", Context(history=h)) == (
        "History: substituted by Act 2 of 2000, in force from 2000-03-01; "
        "inserted by Act 43 of 1986, in force from 1986-11-19.")
    assert history("IPC 304B", Context()) == ""


def store_with(rows, status):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE judgments (id TEXT, title TEXT, decided TEXT)")
    db.execute("CREATE TABLE cites (ref TEXT, rowid INTEGER, count INTEGER)")
    for i, (jid, title, decided, ref, count) in enumerate(rows, 1):
        db.execute("INSERT INTO judgments (rowid, id, title, decided) VALUES (?, ?, ?, ?)", (i, jid, title, decided))
        db.execute("INSERT INTO cites VALUES (?, ?, ?)", (ref, i, count))
    return SimpleNamespace(db=db, status=status)


def test_leading_judgments_deal_with_the_section_and_are_not_overruled():
    store = store_with([
        ("a", "FAMOUS v. STATE", "2017-01-01", "CRPC 154", 1),       # names it once in passing
        ("b", "T.T. ANTONY v. STATE", "2001-07-06", "CRPC 154", 7),
        ("c", "LALITA KUMARI v. STATE", "2013-11-12", "CRPC 154", 27),
        ("d", "OVERRULED v. STATE", "1990-01-01", "CRPC 154", 30),
    ], {"a": {"cited_by": list(range(90))}, "b": {"cited_by": list(range(28))}, "c": {"cited_by": [1]},
        "d": {"cited_by": list(range(50)), "overruled_noted_in": [{"title": "X"}]}})
    line = cases("BNSS 173", BNSS_173, Context(judgments=store))  # found through CrPC 154, the section it replaced
    assert line.startswith("Leading Supreme Court judgments applying it (names only")
    assert "LALITA KUMARI v. STATE (2013)" in line and "T.T. ANTONY v. STATE (2001), cited in 28" in line
    assert "FAMOUS" not in line and "OVERRULED" not in line


def test_the_model_reads_the_context_lines():
    ev = assemble([{**BNSS_173, "_ref": "BNSS 173"}], Corpus())
    assert "Formerly Section 154" in render(ev)
