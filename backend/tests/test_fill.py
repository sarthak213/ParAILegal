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


def test_an_omitted_clause_is_read_from_an_older_copy_and_put_back():
    from app.history.amendments import OMITTED, text_as_on
    from app.history.fill import omitted_units, unquoted_words

    raw = ("\n      2. Definitions.- In this Act, unless the context otherwise requires,-\n"
           " (a) \"board\" means the Board constituted under section 3;\n"
           " (b) \"cess\" means the cess levied under section 9;\n"
           " (c) \"zoo\" means an establishment where animals are kept.\n"
           " ----------------------------------------------------------------------\n"
           " 1.   Ins. by Act 12 of 1990, s. 2 (w.e.f. 1-1-1991).\n")  # the copy is as amended to 1990
    copy = read_copy(raw, "2007-08-21")
    today = ("In this Act, unless the context otherwise requires,— (a) “board” means the Board constituted under "
             "section 3; (c) “zoo” means an establishment where animals are kept.")
    note = "Clause (b) omitted by Act 16 of 2003, s. 3 (w.e.f. 1-4-2003)."
    a = Amendment("x_act_1980", "2", 1, 3, OMITTED, "Act 16 of 2003", "2003-04-01", "", None, note, "w.e.f.", 2003)
    assert omitted_units(note) == ["(b)"] and unquoted_words(note) is None
    [f] = fill([a], {"2": today, "3": "Authority for licences."}, [copy])
    assert f.unit == "(b)" and f.old_text == '(b) "cess" means the cess levied under section 9'
    a.old_text, a.unit = f.old_text, f.unit
    then = text_as_on(today, [a], "2000-01-01")
    assert then.complete and '(b) "cess" means the cess levied under section 9 (c)' in then.text


def test_words_an_omission_names_without_quotation_marks():
    from app.history.amendments import OMITTED

    a = Amendment("x", "5", 1, 1, OMITTED, "Act 62 of 1951", "1951-11-03", "", None,
                  "The words the University omitted by Act 62 of 1951, s. 3 (w.e.f. 3-11-1951)")
    [f] = fill([a], {"5": "anything"}, [])
    assert (f.old_text, f.source) == ("the University", "the footnote")


def test_an_omitted_unit_keeps_its_cited_numbers_and_ends_at_the_next_unit():
    from app.history.fill import _tidy, earlier_unit

    raw = ("\n      114. Power to make rules.- (1) The Central Government may make rules.\n"
           " (2) In particular, such rules may provide for- (k) the fees payable and the manner of collection; **\n"
           " (l) the conditions which may be prescribed under sub-sections (5), (6) , (10) and (12) of section 92;\n"
           " (m) the term of office of members of the Committee.\n"
           "      115. Repeal.- The Act is repealed.\n")
    copy = read_copy(raw, "2006-03-05")
    today = "(1) The Central Government may make rules. (2) In particular, such rules may provide for— (m) the term."
    old = earlier_unit(today, "(l)", copy, "The Act is repealed.", "114")
    assert old == "(l) the conditions which may be prescribed under sub-sections (5), (6), (10) and (12) of section 92"
    assert earlier_unit(today, "(k)", copy, "The Act is repealed.", "114") == "(k) the fees payable and the manner of collection"
    assert _tidy("the provisions of sections 39 and 40 shall apply") == "the provisions of sections 39 and 40 shall apply"
    assert _tidy("as specified 1 therein") == "as specified therein"
    assert _tidy("under sub-sections (5), 2 (6), (10) and (12)") == "under sub-sections (5), (6), (10) and (12)"


def test_a_fill_stops_at_the_next_section_and_sub_clauses_stay_in_their_clause():
    from app.history.fill import _tidy, earlier_unit, omitted_units

    assert _tidy("shall be punishable with fine, or with both. 43. Penalty for contravention of section 24") == (
        "shall be punishable with fine, or with both.")
    assert _tidy("may extend to five lakh rupees, or with both. 54") == "may extend to five lakh rupees, or with both."
    assert _tidy("the offence under section 43. Penalty is fine") == "the offence under section 43. Penalty is fine"
    assert omitted_units("Clause (c) of sub-section (1) of section 132, omitted by Act 32 of 2019") == []
    assert omitted_units("Cls. (zl) omitted by Act 6 of 2026, s. 70") == ["(zl)"]

    raw = ("\n      2. Definitions.- In this Act,- (m) \"wages\" includes any privilege;\n"
           " (n) \"workman\" means any person who is- (i) a railway servant; or (ii) employed on monthly wages;\n"
           " (o) \"zone\" means an area.\n      3. Liability.- The employer shall pay.\n")
    copy = read_copy(raw, "2006-03-05")
    today = 'In this Act,— (m) "wages" includes any privilege; (o) "zone" means an area.'
    assert earlier_unit(today, "(n)", copy, "The employer shall pay.", "2") == (
        '(n) "workman" means any person who is- (i) a railway servant; or (ii) employed on monthly wages')
