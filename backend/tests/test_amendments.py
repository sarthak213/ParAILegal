from pathlib import Path

import pytest

from app.history.amendments import INSERTED, OMITTED, SUBSTITUTED, extract, parse_note, text_as_on

SEP = " " * 40
PAGE_1 = f"""302. Punishment for murder.—Whoever commits murder shall be punished with death, or
1[imprisonment for life], and shall also be liable to fine.
303. Punishment for murder by life-convict.—Whoever, being under sentence of 1[imprisonment
for life], commits murder shall be punished with death.
2[CHAPTER XXA
OF CRUELTY BY HUSBAND
498A. Husband or relative of husband of a woman subjecting her to cruelty.—Whoever, being
the husband subjects such woman to cruelty shall be punished.]
{SEP}
1. Subs. by Act 26 of 1955, s. 117 and the Sch., for "transportation for life" (w.e.f. 1-1-1956).
2. Ins. by Act 46 of 1983, s. 2 (w.e.f. 25-12-1983).
"""
CONTENTS_PAGE = """32. Words referring to acts include illegal omissions.
33. "Act". "Omission".
"""


def test_footnotes_become_amendments_of_the_right_sections():
    am = extract("IPC", CONTENTS_PAGE + "\f" + PAGE_1)
    by_section = {(a.section, a.action) for a in am}
    assert ("302", SUBSTITUTED) in by_section and ("303", SUBSTITUTED) in by_section  # one footnote, two marks
    assert ("498A", INSERTED) in by_section                                             # inserted with its chapter
    assert all(a.section != "33" for a in am)                                          # contents lines are not footnotes
    sub = next(a for a in am if a.section == "302")
    assert (sub.by, sub.effective, sub.old_text, sub.new_text) == (
        "Act 26 of 1955", "1956-01-01", "transportation for life", "imprisonment for life")


def test_parse_note():
    assert parse_note("Ins. by Act 46 of 1983, s. 5 (w.e.f. 25-12-1983).") == (INSERTED, "Act 46 of 1983", "1983-12-25", None)
    assert parse_note('Subs. by the A.O. 1950, for "a British subject"') == (SUBSTITUTED, "A.O. 1950", "1950-01-26", "a British subject")
    assert parse_note("Subs. by s. 51, ibid., for the Explanation (w.e.f. 27-10-2009).", "Act 10 of 2009")[1] == "Act 10 of 2009"
    assert parse_note('The words "or transportation" omitted by Act 26 of 1955, s. 117.')[::3] == (OMITTED, "or transportation")


def test_text_on_a_past_date():
    am = extract("IPC", PAGE_1)
    sec = lambda s: [a for a in am if a.section == s]  # noqa: E731
    then = text_as_on("Whoever commits murder shall be punished with death, or [imprisonment for life], and shall also be "
                      "liable to fine.", sec("302"), "1950-01-01")
    assert then.complete and "death, or transportation for life, and" in then.text
    assert text_as_on("Whoever, being the husband ...", sec("498A"), "1980-01-01").text is None  # not yet enacted
    now = text_as_on("Whoever, being the husband ...", sec("498A"), "1990-01-01")
    assert now.text == "Whoever, being the husband ..." and now.complete


def test_unknown_earlier_wording_is_reported_not_guessed():
    page = f"""4. Extension of Code.—1[The provisions of this Code apply also to any offence.]
{SEP}
1. Subs. by Act 4 of 1898, s. 2, for section 4 (w.e.f. 18-2-1898).
"""
    then = text_as_on("The provisions of this Code apply also to any offence.", extract("IPC", page), "1890-01-01")
    assert not then.complete and then.unknown[0]["by"] == "Act 4 of 1898"


IPC = Path(__file__).parents[1] / "data" / "pdf_text" / "ipc_1860.txt"


@pytest.mark.skipif(not IPC.exists(), reason="needs the cached IPC text")
def test_the_real_ipc():
    am = extract("IPC", IPC.read_text(encoding="utf-8"))
    history = lambda s: {(a.action, a.by, a.effective) for a in am if a.section == s}  # noqa: E731
    assert ("inserted", "Act 46 of 1983", "1983-12-25") in history("498A")
    assert ("inserted", "Act 43 of 1986", "1986-11-19") in history("304B")
    assert ("substituted", "Act 26 of 1955", "1956-01-01") in history("302")
    assert ("substituted", "Act 13 of 2013", "2013-02-03") in history("375")
