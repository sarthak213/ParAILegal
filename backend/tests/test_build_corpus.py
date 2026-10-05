"""The corpus builder's cleaning rules, on small hand-made pages (no data files needed)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_corpus as b  # noqa: E402
from pdf_text import repair_splits  # noqa: E402


def test_footnotes_at_the_foot_of_a_page_are_dropped():
    page = ("2. Definitions.—In this Act, “court” means a civil court.\n \n"
            "1. Subs. by Act 7 of 2017, s. 3, for “tribunal” (w.e.f. 1-4-2017).\n"
            "2. Ins. by s. 4, ibid.\n\f\n"
            "3. Powers of court.—The court may")
    lines = b.lines_of(page)
    assert "1. Subs. by Act 7 of 2017, s. 3, for “tribunal” (w.e.f. 1-4-2017)." not in lines
    assert lines[-1] == "3. Powers of court.—The court may"


def test_a_blank_line_mid_page_does_not_hide_the_text_after_it():
    """Epidemic Diseases Act s. 2A sat after a '* * *' line followed by a long blank line."""
    page = ("(a) the inspection of persons;\n"
            "6*              *             *             *\n"
            "                                        \n"
            "7[2A. Powers of Central Government.—When the Central Government is satisfied\n")
    assert any("2A. Powers of Central Government" in line for line in b.lines_of(page))


def test_a_numbered_list_in_the_body_is_not_a_footnote_run():
    """A schedule line '1. The Indian Partnership Act, 1932.' once swallowed ss. 4-9."""
    page = ("SCHEDULE\n1. The Indian Partnership Act, 1932.\n"
            "By order and in the name of the Lieutenant Governor.\nCHAPTER II\n"
            "4. Definition of “partnership”.—“Partnership” is the relation between persons\n"
            "5. Partnership not created by status.—The relation arises from contract\n\f\n")
    lines = b.lines_of(page)
    assert any(line.startswith("4. Definition") for line in lines)
    assert any(line.startswith("5. Partnership") for line in lines)


def test_the_watermark_ends_a_page_and_its_footnotes():
    page = ("1. Short title.—This Act may be called the Test Act.\n"
            "                                        \n1. Subs. by the A. O. 1950.\nIndiaCode\n"
            "2. Acts repealed.—(1) The Old Act is repealed.")
    assert b.lines_of(page)[-1] == "2. Acts repealed.—(1) The Old Act is repealed."


def _cut(text: str, entries: list[tuple[str, str]]) -> dict[str, str]:
    lines = text.split("\n")
    toc = [{"number": n, "title": t, "chapter": "", "chapter_title": ""} for n, t in entries]
    sections, _ = b.cut_sections(lines, toc, 0)
    return {s["number"]: b.section_text(s["raw"], s["number"], s["title"]) for s in sections}


def test_sections_are_cut_at_the_next_section_whatever_its_amendment_marks():
    text = ("29. Time limit.—The award shall be made within a year.\n"
            "2 [29A. Fast track.—Parties may agree to a fast track.]\n"
            "1145. Cross-examination.—A witness may be cross-examined.\n"
            "*30D. Protection.—No suit shall lie.\n"
            "38-I. Acquisition by a zoo.—No zoo shall acquire an animal.")
    got = _cut(text, [("29", "Time limit."), ("29A", "Fast track."), ("145", "Cross-examination."),
                      ("30D", "Protection."), ("38I", "Acquisition by a zoo.")])
    assert got["29"] == "The award shall be made within a year."
    assert got["29A"].startswith("Parties may agree")
    assert got["145"] == "A witness may be cross-examined."
    assert got["30D"] == "No suit shall lie."
    assert got["38I"] == "No zoo shall acquire an animal."


def test_a_heading_without_its_dash_is_still_removed():
    assert b.section_text("101B. Advisory Committee. (1) The Authority shall", "101B",
                          "Advisory Committee.") == "(1) The Authority shall"


def test_footnote_digits_are_dropped_but_section_numbers_kept():
    assert b.clean_body("on such date1 as the Government may appoint") == \
        "on such date as the Government may appoint"
    assert b.clean_body("mentioned in section159, although") == "mentioned in section 159, although"
    assert b.clean_body("shall 3[extend to the whole of India]") == "shall [extend to the whole of India]"


def test_words_split_by_the_pdf_are_rejoined():
    lined = "the provi sions of this Act shall be commit ted to the court"
    reference = "the provisions of this Act shall be committed to the court provisions committed"
    fixed, n = repair_splits(lined, reference)
    assert fixed == "the provisions of this Act shall be committed to the court" and n == 2


def _act(title: str, *sections: tuple[str, str]) -> tuple[None, list[dict]]:
    sections = sections or (("Short title.", "This Act may be called …"),)
    return None, [{"act_title": title, "section_title": t, "full_text": text, "status": "in force"}
                  for t, text in sections]


def test_whole_act_repeals_are_read_but_partial_ones_ignored():
    built = [
        _act("The Indian Telegraph Act, 1885"), _act("The Indian Wireless Telegraphy Act, 1933"),
        _act("The Cardamom Act, 1965"), _act("The Code of Civil Procedure, 1908"),
        _act("The Mahatma Gandhi National Rural Employment Guarantee Act, 2005"),
        _act("The Telecommunications Act, 2023", ("Repeal of certain Acts and savings.",
             "(1) Subject to the other provisions of this section, the enactments namely, the Indian "
             "Telegraph Act, 1885 (13 of 1885), and the Indian Wireless Telegraphy Act, 1933 (17 of 1933), "
             "are hereby repealed.")),
        _act("The Spices Board Act, 1986", ("Repeal and savings.",
             "(1) Sections 3 to 33 of the Cardamom Act, 1965 (42 of 1965) are hereby repealed.")),
        _act("The Goa Extension Act, 1965", ("Repeal and saving.",
             "(1) So much of any law in force in Goa as corresponds to the Code of Civil Procedure, "
             "1908 (5 of 1908) shall stand repealed.")),
        _act("The VB-G RAM G Act, 2025", ("Repeal, savings and transitional provisions.",
             "(1) on and from the appointed date, the Mahatma Gandhi National Rural Employment Guarantee "
             "Act, 2005 (42 of 2005), together with all rules made thereunder, shall stand repealed.")),
    ]
    b.detect_repeals(built)
    acts = {records[0]["act_title"]: records[0] for _, records in built}
    assert acts["The Indian Telegraph Act, 1885"]["status"] == "repealed"
    assert acts["The Indian Wireless Telegraphy Act, 1933"]["replaced_by"] == "The Telecommunications Act, 2023"
    assert "replaced_by" not in acts["The Cardamom Act, 1965"]            # only ss. 3-33 repealed
    assert "replaced_by" not in acts["The Code of Civil Procedure, 1908"]  # only Goa's local laws
    mgnrega = acts["The Mahatma Gandhi National Rural Employment Guarantee Act, 2005"]
    assert mgnrega["status"] == "in force" and mgnrega["repeal_pending"]  # from a date to be notified


def test_derived_links_match_like_sections_and_skip_unclear_ones():
    from derive_section_links import derive

    old = {"4": {"title": "Payment of gratuity.", "text": "Gratuity shall be payable to an employee on the "
                                                           "termination of his employment after five years"},
           "6": {"title": "Nomination.", "text": "Each employee shall make a nomination for the gratuity"},
           "9": {"title": "Penalties.", "text": "Whoever makes any false statement shall be punishable"}}
    new = {"53": {"title": "Payment of gratuity.", "text": "Gratuity shall be payable to an employee on the "
                                                            "termination of his employment after five years"},
           "55": {"title": "Nomination.", "text": "Each employee shall make nomination for gratuity"},
           "60": {"title": "Wages.", "text": "Wages shall be paid in current coin"}}
    got = derive(old, new, title_weight=0.3, threshold=0.45, margin=0.1)
    assert got["4"][0] == "53" and got["6"][0] == "55"
    assert "9" not in got  # nothing in the new Act resembles it


def test_misspelt_amendment_acts_are_skipped_but_real_acts_kept():
    assert b.SKIP_TITLE.search("The Merchant Shipping (Amnendnent) Act, 1984")
    assert b.SKIP_TITLE.search("The Finance Act, 2021")
    assert not b.SKIP_TITLE.search("The Payment of Gratuity Act, 1972")
    assert not b.SKIP_TITLE.search("The Indian Penal Code")
