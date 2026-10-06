from app.history.amendments import INSERTED, SUBSTITUTED, Amendment, reattribute
from app.history.fill import earlier_words, fill, predates, read_copy

OLD_SITE = """
      4. Punishment.- Whoever commits the offence shall be punished
 with imprisonment for a term which may extend to two years, or with
 fine which may extend to one thousand rupees, or with both 1*[and
 shall also be liable to forfeiture].

      5. Abetment.- Whoever abets the offence shall be punished as if
 he had committed it.
 ----------------------------------------------------------------------
 1.   Ins. by Act 12 of 1990, s. 2 (w.e.f. 1-1-1991).
"""
TODAY_4 = ("Whoever commits the offence shall be punished with imprisonment for a term which may extend to "
           "[seven years, and with fine which may extend to one lakh rupees] and shall also be liable to forfeiture.")


def amendment(section, action, by, effective, new_text, year=None):
    return Amendment("x_act_1980", section, 1, 3, action, by, effective, new_text, None,
                     f"Subs. by {by}, s. 3, for certain words", "w.e.f.", year)


def test_a_copy_and_what_it_predates():
    copy = read_copy(OLD_SITE, "2007-08-21")
    assert copy.cites == {"Act 12 of 1990"} and copy.newest == 1990
    assert "1*" not in copy.text and "Ins. by" not in copy.text
    later = amendment("4", SUBSTITUTED, "Act 7 of 2012", "2012-06-01", "seven years, and with fine", 2012)
    assert predates(copy, later)
    assert not predates(copy, amendment("4", SUBSTITUTED, "Act 12 of 1990", "1991-01-01", "x", 1990))
    assert not predates(copy, amendment("4", SUBSTITUTED, "Act 3 of 1985", "1985-01-01", "x", 1985))


def test_the_earlier_words_are_read_between_the_same_neighbours():
    copy = read_copy(OLD_SITE, "2007-08-21")
    a = amendment("4", SUBSTITUTED, "Act 7 of 2012", "2012-06-01", "seven years, and with fine which may extend to "
                                                                     "one lakh rupees", 2012)
    assert earlier_words(TODAY_4, a, copy) == ("two years, or with fine which may extend to one thousand rupees, "
                                               "or with both")
    [f] = fill([a], {"4": TODAY_4}, [copy])
    assert f.source == "archived India Code copy saved 2007-08-21" and f.old_text.startswith("two years")


def test_no_answer_rather_than_a_guess():
    copy = read_copy(OLD_SITE, "2007-08-21")
    # the copy already reads like today (it is not older than the change)
    same = amendment("5", SUBSTITUTED, "Act 7 of 2012", "2012-06-01", "the offence shall be punished", 2012)
    assert earlier_words("Whoever abets the offence shall be punished as if he had committed it.", same, copy) is None
    # the span is nowhere in today's section
    lost = amendment("4", SUBSTITUTED, "Act 7 of 2012", "2012-06-01", "words that are not there at all", 2012)
    assert earlier_words(TODAY_4, lost, copy) is None


def test_amendments_are_refiled_under_the_section_that_has_their_words():
    a = Amendment("x", "24", 1, 1, INSERTED, "Act 1 of 1990", None, "a roll of advocates shall be kept by the Bar "
                  "Council in the prescribed form", None, "Ins. by Act 1 of 1990")
    sections = {"24": "Persons who may be admitted as advocates.", "24A": "[A roll of advocates shall be kept by the "
                "Bar Council in the prescribed form.]", "25": "Authority to whom applications are made."}
    assert reattribute([a], sections) == 1 and a.section == "24A"


def test_a_whole_clause_is_read_between_its_label_and_the_next():
    raw = ("\n      2. Definitions.- In this Act, unless the context otherwise requires,-\n"
           " (a) \"board\" means the Board constituted under section 3;\n"
           " (b) \"dealer\" means any person who buys and sells captive animals;\n"
           " (c) \"zoo\" means an establishment where animals are kept.\n")
    copy = read_copy(raw, "2007-08-21")
    today = ("In this Act, unless the context otherwise requires,— (a) “board” means the Board constituted under "
             "section 3; [(b) “dealer” in relation to any captive animal or specified plant, means a person who "
             "carries on the business of trading;] (c) “zoo” means an establishment where animals are kept.")
    a = amendment("2", SUBSTITUTED, "Act 16 of 2003", "2003-04-01", "(b) “dealer” in relation to any captive animal "
                  "or specified plant, means a person who carries on the business of trading;", 2003)
    old = earlier_words(today, a, copy, next_section="Authority for licences.")
    assert old == '(b) "dealer" means any person who buys and sells captive animals'
