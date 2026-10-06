from scripts.build_judgments import ActNames, by_page, clean, paragraphs, provisions, split_parts

ACTS = ActNames(["ipc", "crpc", "iea", "indian_forest_act_1927", "central_sales_tax_act_1956",
                 "code_of_civil_procedure_1908"])


def test_any_act_in_the_headnote_index():
    index = ("Forest Act, 1927: s. 4 – Declaration of reserved forest – Penal Code, 1860 – ss. 34, 302 – "
             "Central Sales Tax Act 1956 S2(h)-'Sale Price' – Bombay Rent Act, 1947 – s. 13")
    refs = {p["ref"] for p in provisions("", "2023-07-05", index, ACTS)}
    assert refs == {"indian_forest_act_1927 4", "IPC 34", "IPC 302", "central_sales_tax_act_1956 2"}


def test_codes_named_in_the_text():
    refs = [p["ref"] for p in provisions("convicted u/s. 302 r/w s.34, IPC; Order IX, s. 9 C.P.C.", "2001-01-01")]
    assert refs[:2] == ["IPC 302", "IPC 34"] and "code_of_civil_procedure_1908 9" in refs
    # before 1 April 1974 "Cr.P.C." is the 1898 Code, not the corpus's 1973 Code
    assert provisions("bail under s. 497 Cr.P.C.", "1965-01-01")[0]["ref"] == "CRPC_1898 497"
    assert provisions("bail under s. 437 Cr.P.C.", "1990-01-01")[0]["ref"] == "CRPC 437"


def test_numbered_paragraphs_survive_a_lost_space_and_a_lost_number():
    body = ("The Judgment of the Court was delivered by X, J.\n1. The appeal.\n2. The facts.\n"
            "3. A quote:\n1. Rule one.\n2. Rule two.\n4. Next.\n5.While dealing\n7. Skipped six.\n8. End.")
    assert [p["n"] for p in paragraphs(body)] == [0, 1, 2, 3, 4, 5, 7, 8]


def test_an_older_report_is_split_by_page_without_margin_lines():
    raw = ("16 SUPREME COURT REPORTS [1950]\nRam Krisltna\n1950\nKANIA C. J.-This is an appeal from the\n"
           "judgment of the High Court at Nagpur.\n\f\nS.C.R. SUPREME COURT REPORTS 17\nMarch 14\n"
           "The appeal is dismissed.\n19. Paragraph 298\n")
    text = clean(raw, "RAM KRISHNA RAMNATH v. SECRETARY, MUNICIPAL COMMITTEE")
    front, body = split_parts(text)
    assert "Ram Krisltna" not in text and "March 14" not in text and "19. Paragraph 298" in text
    assert paragraphs(body) == []  # unnumbered: by page instead
    pages = by_page(body)
    assert [p["n"] for p in pages] == [16, 17] and pages[0]["text"].startswith("KANIA C. J.-This")


def test_line_end_hyphens_and_opinions_that_open_with_a_comma():
    assert clean("the basic struc\ufffe\nture of the Con\u00adstitution") == "the basic structure of the Constitution"
    body = "Counsel for the respondent.\nSIKRI, C.J. I have had the advantage of reading the judgment.\n"
    front, rest = split_parts(clean(body))
    assert rest.startswith("SIKRI, C.J. I have had")
