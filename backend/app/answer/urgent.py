"""Urgent-help notices: fixed rights and helplines shown above the answer when a question says
something urgent is happening to the person asking (or someone close to them).

Written and reviewed by hand, never generated: in an emergency the numbers must be right.
Each notice is shown only when the question both names the situation and speaks in the first
person ("my husband beats me", "police arrested my brother"), so a student asking "what is the
punishment for domestic violence?" gets a plain answer. Self-harm wording is first person by
its nature and always shows its notice.

Every provision a notice cites is checked against the corpus by tests/test_urgent.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_FIRST_PERSON = re.compile(r"\b(?:i|i'm|im|me|my|mine|myself|we|us|our|mujhe|mera|meri|mere|hum|hamara|hamari)\b",
                           re.IGNORECASE)


@dataclass(frozen=True)
class Notice:
    key: str
    pattern: re.Pattern
    text: str
    needs_first_person: bool = True
    cites: tuple[tuple[str, str], ...] = ()  # (corpus ref, a word its section title contains)


NOTICES = (
    Notice(
        "self_harm",
        re.compile(r"\b(?:kill(?:ing)? myself|end(?:ing)? my life|want to die|suicidal|commit suicide|"
                   r"take my (?:own )?life|self[- ]harm|hurt myself)\b", re.IGNORECASE),
        "> **You are not alone.** If you are thinking about ending your life, please call "
        "**Tele-MANAS on 14416**: free, 24 hours a day, in many Indian languages. "
        "In an emergency, call **112**.",
        needs_first_person=False),
    Notice(
        "arrest",
        re.compile(r"\b(?:arrest(?:ed|ing)?|detain(?:ed)?|in (?:police )?custody|police (?:station|lock-?up)|"
                   r"giraftar|hirasat|picked up by (?:the )?police)\b", re.IGNORECASE),
        "> **If you or someone close to you has been arrested:**\n"
        "> - They must be told the grounds of arrest and whether they can get bail (BNSS s.47; Article 22(1)).\n"
        "> - Police must inform a relative or friend about the arrest (BNSS s.48).\n"
        "> - They may meet an advocate of their choice during interrogation (BNSS s.38).\n"
        "> - They must be produced before a Magistrate within 24 hours (BNSS s.58; Article 22(2)).\n"
        "> - Free legal aid: call the **NALSA helpline on 15100**.",
        cites=(("BNSS 47", "grounds"), ("BNSS 48", "relative"), ("BNSS 38", "advocate"),
               ("BNSS 58", "twenty-four"), ("ART 22", "arrest"))),
    Notice(
        "violence",
        re.compile(r"\b(?:domestic violence|beat(?:s|ing)? (?:me|us|her)|hits? (?:me|us)|hitting me|"
                   r"abus(?:es|ing) me|threat(?:en(?:s|ed|ing)?)? to kill|kill me|harass(?:es|ing|ment)|"
                   r"dowry|maar(?:ta|te|ti)|marpit)\b", re.IGNORECASE),
        "> **If you are in danger now, call 112** (police and emergency). Women's helpline: **181**.\n"
        "> - A woman facing domestic violence can ask a Magistrate for protection, residence and "
        "maintenance orders, directly or through a Protection Officer (Protection of Women from "
        "Domestic Violence Act, 2005, s.12).\n"
        "> - Free legal aid: call the **NALSA helpline on 15100**.",
        cites=(("protection_of_women_from_domestic_violence_act_2005 12", "magistrate"),)),
    Notice(
        "child",
        re.compile(r"\b(?:child (?:abuse|marriage|labour|labor)|abus(?:e|ed|ing) (?:my|a|our) (?:child|son|daughter)|"
                   r"(?:my|our) (?:child|son|daughter) (?:was|is being) (?:abused|touched|harassed))\b", re.IGNORECASE),
        "> **To report harm to a child, call Childline on 1098** or **112**. Sexual abuse of a child "
        "can be reported to the police or the Special Juvenile Police Unit (POCSO Act, 2012, s.19).",
        cites=(("protection_of_children_from_sexual_offences_act_2012 19", "report"),)),
    Notice(
        "cyber_fraud",
        re.compile(r"\b(?:online fraud|cyber ?(?:fraud|crime)|upi fraud|otp fraud|scam(?:med)?|"
                   r"money (?:was |got )?(?:debited|deducted|stolen)|hacked)\b", re.IGNORECASE),
        "> **For online financial fraud, call 1930 at once** or report it at cybercrime.gov.in: "
        "reporting quickly can help stop the money being moved."),
    Notice(
        "deadline",
        re.compile(r"\b(?:last date|deadline|limitation (?:period )?(?:expires|ends|is ending|is over)|"
                   r"time[- ]barred|(?:days|hours) left to (?:file|appeal|reply))\b", re.IGNORECASE),
        "> **Legal time limits are strict.** If a deadline is close, speak to a lawyer now. "
        "Free legal aid: call the **NALSA helpline on 15100**."),
)


def notices(question: str) -> list[Notice]:
    first_person = bool(_FIRST_PERSON.search(question))
    return [n for n in NOTICES if n.pattern.search(question) and (first_person or not n.needs_first_person)]


def block(question: str) -> str:
    """The notices for a question as markdown to put above the answer, or ""."""
    found = notices(question)
    return "".join(n.text + "\n\n" for n in found)
