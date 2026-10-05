from app.answer.evidence import Evidence
from app.answer.prompts import CAVEAT_LEAD, LEGAL_AID
from app.answer.sources_only import DISCLAIMER
from app.answer.verify import citations, mentions, remove_invalid_ids, verify

NI_138 = Evidence(1, "negotiable_instruments_act_1881 138",
                  "Section 138 — Dishonour of cheque, The Negotiable Instruments Act, 1881", "In force",
                  "Where any cheque ... shall be punished with imprisonment for a term which may extend to "
                  "two years, or with fine which may extend to twice the amount of the cheque. The notice "
                  "shall be given within thirty days, as provided in section 142.", {})
BNS_103 = Evidence(2, "BNS 103", "Section 103 — Punishment for murder, The Bharatiya Nyaya Sanhita, 2023",
                   "In force", "(1) Whoever commits murder shall be punished with death or imprisonment for life.", {},
                   relation="Corresponds to Section 302 — Punishment for murder, The Indian Penal Code [3]")
EV = [NI_138, BNS_103]


def test_mentions():
    assert mentions("Sections 41 and 41A of the CrPC, s. 35(1) and Articles 14, 19 and 21") == {
        ("section", "41"), ("section", "41A"), ("section", "35"),
        ("article", "14"), ("article", "19"), ("article", "21")}
    assert mentions("Schedule VII") == {("schedule", "VII")}


def test_invalid_source_numbers_are_removed():
    text, bad = remove_invalid_ids("A [1]. B [7]. C [2, 9].", 2)
    assert text == "A [1]. B . C [2]." and bad == [7, 9]


def test_a_grounded_answer_passes():
    answer = ("Dishonour of a cheque is punishable with imprisonment up to two years [1]. "
              "Notice must be given as section 142 requires [1]. "
              "Section 103 of the Bharatiya Nyaya Sanhita, 2023 replaced Section 302 [2].")
    fixed, v = verify(answer, EV)
    assert fixed == answer
    assert not v.unsupported_provisions and not v.unsupported_figures and not v.uncited
    assert v.cited_sources == [1, 2] and v.warning == ""


def test_invented_provisions_and_figures_are_flagged():
    answer = ("Under Section 139 the court presumes a debt [1]. "
              "The fine can reach 10,000 rupees under Article 22 [2].")
    _, v = verify(answer, EV)
    assert v.unsupported_provisions == ["Article 22", "Section 139"]
    assert v.unsupported_figures == ["10,000"]
    assert "Section 139" in v.warning


def test_provision_pinned_on_the_wrong_source():
    _, v = verify("Section 142 requires notice [2]. Section 103 punishes murder [2].", EV)
    assert v.misattributed == ["Section 142 [2]"]  # section 142 is mentioned in source 1, not 2
    assert not v.unsupported_provisions


def test_uncited_legal_sentences_are_counted():
    _, v = verify("A person who commits murder shall be punished with death. Murder is serious [2].", EV)
    assert v.claims == 1 and v.uncited == ["A person who commits murder shall be punished with death."]
    assert v.warning  # the only legal claim has no source


def test_code_written_text_is_not_checked():
    answer = CAVEAT_LEAD + "Imprisonment may extend to two years [1]." + LEGAL_AID + "\n\n" + DISCLAIMER
    _, v = verify(answer, EV)
    assert not v.unsupported_figures  # the helpline number 15100 is ours, not the model's
    assert v.claims == 1 and not v.uncited


def test_citations_map_numbers_to_sources():
    assert citations(EV)[1] == {"n": 2, "ref": "BNS 103",
                                "label": "Section 103 — Punishment for murder, The Bharatiya Nyaya Sanhita, 2023"}
