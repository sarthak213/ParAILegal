"""Prompts for the local answer model: short and concrete, because a 4B model follows a short
prompt better than v1's 1,500-word one (written for a 105B model).

The parts a model could get wrong and that never change are written by code, not generated:
the caveat line, the legal aid line in Summarise mode, and the disclaimer.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.answer.gate import CAVEAT

RESEARCH, SUMMARISE, ADVOCATE = "research", "summarise", "advocate"

SYSTEM = """You are ParAILegal, a research assistant for Indian law. You answer using only the numbered sources given with the question.

Rules:
1. After each sentence or bullet point that states the law, write the number of the source it comes from in square brackets, like [2], even when there is only one source. Use only the numbers of the sources given.
2. Use only the sources. Never mention a section, article, Act, case, penalty or date that is not in them, even if you know it.
3. Copy numbers, ages, time limits, amounts and penalties exactly as the source states them, including words like "not less than", "above" and "within".
4. If the sources do not answer the question, say so in one sentence, then say briefly what they do cover.
5. If a source is marked repealed, say that it is repealed and name the provision that replaced it.
6. Explain what the law says. Do not predict how a court will decide a particular case."""


@dataclass(frozen=True)
class ModeSpec:
    instruction: str
    max_tokens: int


MODES = {
    RESEARCH: ModeSpec(
        "Answer for a law student or advocate, in the statute's own terms. Start with a direct "
        "answer in one or two sentences. Then set out the relevant provisions under '###' "
        "headings, with their conditions, exceptions and provisos as bullet points. Where the "
        "sources give an old and a new provision, give both. About 150 to 300 words.",
        700),
    SUMMARISE: ModeSpec(
        "Answer for someone with no legal training. Use plain, everyday words and short "
        "sentences, and explain any legal term you have to use. Say what the law says, then what "
        "it means in practice. About 120 to 200 words. Still cite sources with [n].",
        450),
    ADVOCATE: ModeSpec(
        "Answer for an advocate preparing arguments. Using only the sources, write three '###' "
        "sections: 'For', the provisions and conditions supporting the proposition; 'Against', "
        "the exceptions, provisos and limits that cut against it; 'Which way the text leans'. "
        "Make clear these are arguments, not settled law. About 200 to 300 words.",
        700),
}

CAVEAT_INSTRUCTION = ("The sources may not cover this exact question. If they do not, say so "
                      "first, then explain only what they do cover.")

CAVEAT_LEAD = "*The closest provisions found may not cover your exact situation.*\n\n"

LEGAL_AID = ("\n\n**Need a lawyer?** If you cannot afford one, you may be entitled to free legal "
             "aid: contact your District Legal Services Authority, or call the NALSA helpline 15100.")


def mode_of(name: str | None) -> str:
    """'ADVOCATE' / 'summarise' / 'SUMMARIZE' / None -> a MODES key."""
    n = (name or "").strip().lower().replace("summarize", "summarise")
    return n if n in MODES else RESEARCH


def messages(query: str, context: str, mode: str, outcome: str, read_as: str = "",
             earlier: str = "") -> list[dict]:
    """read_as: the question with typos fixed and lay or Hindi words given their legal terms
    ("anticipatry bail" -> "anticipatory bail", "zamanat" -> "bail"), when that differs.
    earlier: the question this one follows up ("what is cheating?" before "and the punishment?")."""
    spec = MODES[mode]
    task = spec.instruction + (f"\n\n{CAVEAT_INSTRUCTION}" if outcome == CAVEAT else "")
    if earlier:
        question = f"Earlier question: {earlier}\nFollow-up question (answer this one): {query}"
    else:
        question = f"Question: {query}"
    question += f"\nIn legal terms: {read_as}" if read_as else ""
    user = f"Sources:\n\n{context}\n\n{question}\n\n{task}"
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


BRIEF_INSTRUCTION = (
    "Write a case brief for an advocate, using only the facts and the numbered sources. Use these "
    "'###' sections: 'Facts in brief' (three to five sentences); 'Offences' (for each offence, its "
    "provision with [n], which elements the facts show and which are still open, following the "
    "checklist); 'Points for the prosecution'; 'Points for the defence' (exceptions, provisos and "
    "missing elements from the sources); 'Gaps to investigate' (facts that still need evidence). "
    "Make clear these are arguments, not settled conclusions. About 300 to 450 words.")
BRIEF_PRECEDENTS = (
    "Some sources are Supreme Court judgments. Add a '### Precedents' section after 'Offences': for each "
    "judgment, what it held [n], which facts are alike and which differ (from its 'Facts similar' and 'Facts "
    "different' lines), how far its principle still applies, and what its 'Law at the time' line says about "
    "the law it applied. A judgment whose facts differ can still be cited for its principle; say so. If a "
    "judgment carries a 'Caution' line, repeat it.")
BRIEF_MAX_TOKENS = 1300  # the precedents section adds about 300 words


def brief_messages(facts: str, context: str, checklist: str, precedents: bool = False) -> list[dict]:
    """The Case Builder's brief (app/case/builder.py): the facts take the question's place, and
    the elements checklist the lawyer confirmed goes with the sources; Supreme Court judgments
    among the sources get a section of their own."""
    task = BRIEF_INSTRUCTION + (f" {BRIEF_PRECEDENTS}" if precedents else "")
    user = (f"Sources:\n\n{context}\n\nElements checklist (from the facts):\n{checklist}\n\n"
            f"Facts of the case:\n{facts}\n\n{task}")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def lead(outcome: str) -> str:
    return CAVEAT_LEAD if outcome == CAVEAT else ""


def tail(mode: str) -> str:
    return LEGAL_AID if mode == SUMMARISE else ""
