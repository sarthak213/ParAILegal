from app.answer.sources_only import DISCLAIMER, heading, sources_only_answer, status_note


BNS_103 = {
    "citation": "Section 103 — Punishment for murder.\nChapter VI, The Bharatiya Nyaya Sanhita, 2023",
    "act_title": "The Bharatiya Nyaya Sanhita, 2023",
    "status": "in force",
    "text": "(1) Whoever commits murder shall be punished with death or imprisonment for life.",
}
IPC_302 = {
    "citation": "Section 302 — Punishment for murder.",
    "act_title": "The Indian Penal Code",
    "status": "repealed",
    "replaced_by": "The Bharatiya Nyaya Sanhita, 2023",
    "text": "Whoever commits murder shall be punished with death.",
}


def test_heading_joins_provision_and_act():
    assert heading(BNS_103) == "Section 103 — Punishment for murder, The Bharatiya Nyaya Sanhita, 2023"


def test_status_notes():
    assert status_note(BNS_103) == ""
    assert status_note(IPC_302) == "Repealed; replaced by The Bharatiya Nyaya Sanhita, 2023"
    assert status_note({"status": "omitted"}) == "Omitted from the Constitution"
    assert status_note({"status": "in force", "scope": "regional", "places": ["Goa"]}) == "Applies only in Goa"


def test_answer_lists_sources_in_order_with_disclaimer():
    answer = sources_only_answer([BNS_103, IPC_302])
    assert answer.index("1. **Section 103") < answer.index("2. **Section 302")
    assert "*Repealed; replaced by" in answer
    assert answer.endswith(DISCLAIMER)


def test_no_sources():
    answer = sources_only_answer([])
    assert answer.startswith("No relevant provisions were found")
    assert answer.endswith(DISCLAIMER)
