import pytest

from app.judgments.citator import Index, learn_parallels, norm_name, treatments_in


def kinds(text):
    return {c: k for c, _, _, k, _ in treatments_in(text)}


@pytest.mark.parametrize("text, expected", [
    # the case that overruled is not itself overruled
    ("In Indore Development Authority v. Manoharlal (2020) 8 SCC 129, the Constitution Bench overruled "
     "Pune Municipal Corporation v. Harakchand (2014) 3 SCC 183.",
     {"(2020) 8 SCC 129": None, "(2014) 3 SCC 183": "overruled"}),
    ("The decision in Pune Municipal Corporation (2014) 3 SCC 183 was overruled by Indore Development "
     "Authority (2020) 8 SCC 129.",
     {"(2014) 3 SCC 183": "overruled", "(2020) 8 SCC 129": None}),
    ("Major General Shri Kant Sharma (2015) 6 SCC 773 does not lay down the correct law and is hereby overruled.",
     {"(2015) 6 SCC 773": "overruled"}),
    ("We, therefore, hold that Sree Balaji Nagar (2015) 3 SCC 353 is per incuriam.",
     {"(2015) 3 SCC 353": "per incuriam"}),
    ("This Court in Maneka Gandhi v. Union of India (1978) 1 SCC 248 was followed in later cases.",
     {"(1978) 1 SCC 248": "followed"}),
    ("The facts in Rampal Singh (2012) 8 SCC 289 are distinguishable.",
     {"(2012) 8 SCC 289": "distinguished"}),
    ("Reference was made to Basdev v. State of Pepsu AIR 1956 SC 488.",
     {"AIR 1956 SC 488": None}),
    # a long case name between "overruled in" and the citation: Puttaswamy did the overruling
    ("ADM Jabalpur was overruled in K.S. Puttaswamy (Retd.) v. Union of India, (2017) 10 SCC 1.",
     {"(2017) 10 SCC 1": None}),
    ("M.P. Sharma (supra) came to be overruled in K.S. Puttaswamy v. Union of India , (2017) 10 SCC 1, to the "
     "extent that it had observed that privacy is not a right.",
     {"(2017) 10 SCC 1": None}),
    # the newer Reports' case-law table: the label follows its citation, then the paragraph
    ("(2021) 2 SCC 1 overruled Para 2 [2011] 9 SCR 382 overruled Para 5 [2019] 5 SCR 579 overruled Para 8 "
     "[1971] 3 SCR 590 relied on Para 17",
     {"(2021) 2 SCC 1": "overruled", "[2011] 9 SCR 382": "overruled", "[2019] 5 SCR 579": "overruled",
      "[1971] 3 SCR 590": "followed"}),
    # the Supreme Court Reports' case-law list: each group takes the label that closes it
    ("SMS Tea Estates (2011) 14 SCC 66 : [2011] 9 SCR 382 – overruled. Hariom Agrawal (2007) 8 SCC 514 : "
     "[2007] 10 SCR 772 and Jupudi Kesava Rao (1971) 1 SCC 545 : [1971] 3 SCR 590 – referred to.",
     {"(2011) 14 SCC 66": "overruled", "[2011] 9 SCR 382": "overruled", "(2007) 8 SCC 514": None,
      "[2007] 10 SCR 772": None, "(1971) 1 SCC 545": None, "[1971] 3 SCR 590": None}),
])
def test_treatment_direction(text, expected):
    assert kinds(text) == expected


def index():
    ix = Index()
    ix.add("1978_2_621_700", "MANEKA GANDHI versus UNION OF INDIA", "1978-01-25", "1978 INSC 16")
    ix.add("1973_4_1_200", "KESAVANANDA BHARATI SRIPADAGALVARU versus STATE OF KERALA & ANR.", "1973-04-24")
    ix.add("S_1959_1_28_38", "X versus Y", "1959-01-01")
    return ix


def test_resolve_by_scr_page_range_and_insc():
    ix = index()
    assert ix.resolve("[1978] 2 SCR 650", "scr") == ("1978_2_621_700", "scr")  # a pinpoint page inside it
    assert ix.resolve("[1959] Supp. 1 SCR 30", "scr") == ("S_1959_1_28_38", "scr")
    assert ix.resolve("[1978] 2 SCR 701", "scr") == (None, "scr")
    assert ix.resolve("1978 INSC 16", "insc") == ("1978_2_621_700", "insc")


def test_resolve_scc_by_parallel_citation_or_name():
    ix = index()
    assert learn_parallels(ix, ["see Maneka Gandhi (1978) 1 SCC 248 : [1978] 2 S.C.R. 621 and"]) == 1
    assert ix.resolve("(1978) 1 SCC 248", "scc") == ("1978_2_621_700", "parallel")
    assert ix.resolve("(1973) 4 SCC 225", "scc", "Kesavananda Bharati v. State of Kerala") == ("1973_4_1_200", "name")
    assert ix.resolve("(1973) 4 SCC 225", "scc", "Somebody Else v. State of Bihar")[0] is None


def test_norm_name():
    assert norm_name("M/s. Shah & Co. & Ors. vs. The State of U.P.") == "shah co v state of up"
