from scripts.link_new_codes import link

THEFT = ("Whoever, intending to take dishonestly any movable property out of the possession of any person without "
         "that person's consent, moves that property in order to such taking, is said to commit theft.")
OLD = {
    "378": {"title": "Theft.", "text": THEFT},
    "379": {"title": "Punishment for theft.", "text": "Whoever commits theft shall be punished with imprisonment "
                                                      "of either description for a term which may extend to three years."},
    "21": {"title": "Public servant.", "text": "The words public servant denote a person falling under any of the "
                                               "descriptions hereinafter following, namely every commissioned officer"},
    "22": {"title": "Movable property.", "text": "The words movable property are intended to include corporeal "
                                                 "property of every description, except land and things attached"},
    "500": {"title": "Punishment for defamation.", "text": "Whoever defames another shall be punished with simple "
                                                           "imprisonment for a term which may extend to two years."},
}
NEW = {
    "303": {"title": "Theft.", "text": THEFT + " (2) Whoever commits theft shall be punished with imprisonment of "
                                               "either description for a term which may extend to three years."},
    "2": {"title": "Definitions.", "text": "In this Sanhita, (28) public servant denote a person falling under any of "
                                           "the descriptions hereinafter following, namely every commissioned officer; "
                                           "(21) movable property are intended to include corporeal property of every "
                                           "description, except land and things attached"},
    "356": {"title": "Defamation.", "text": "Whoever, by words either spoken or intended to be read, makes any "
                                            "imputation concerning any person intending to harm the reputation."},
}


def test_wording_links_old_sections_to_new_merged_ones_too():
    got = link(OLD, NEW)
    assert got["378"][0][0] == "303"
    assert got["21"][0][0] == "2" and got["22"][0][0] == "2"  # definitions merged into one section
    assert got["379"][0][0] == "303"                           # punishment follows its definition


def test_a_shared_punishment_formula_alone_makes_no_link():
    assert "500" not in link(OLD, NEW)  # "shall be punished ... a term which may extend to" is not wording
