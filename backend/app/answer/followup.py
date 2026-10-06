"""Follow-up questions: "what is the punishment for it?" after "what is cheating?".

Deterministic, no model: a question is a follow-up of the one before it when it names no
provision of its own and either
  - starts as a continuation ("and if", "what about", "but"), or
  - is short and points back ("is it bailable?", "what is the punishment for that offence?"), or
  - asks only about an aspect of the earlier provision ("punishment?", "is it bailable?").
A self-contained question ("cheque bounce", "Article 21") starts fresh, even right after another.

For a follow-up, search reads both questions together and the earlier answer's provisions are
added as candidates, so the follow-up stays on the same law unless the new words lead elsewhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_POINTS_BACK = re.compile(
    r"\b(?:it|its|this|that|these|those|they|them|such|the same|above|said|the (?:section|offence|"
    r"provision|article|act|law|case))\b", re.IGNORECASE)
_CONTINUES = re.compile(r"^\s*(?:and|but|also|so|then|what about|how about|what if|in that case)\b",
                        re.IGNORECASE)
SHORT = 10  # words: a longer question that says "it" usually explains itself
# words that ask about an aspect of a provision already under discussion
_ASPECTS = {
    "punishment", "punishable", "penalty", "fine", "imprisonment", "sentence", "jail", "bail",
    "bailable", "non-bailable", "cognizable", "non-cognizable", "compoundable", "triable", "court",
    "exception", "exceptions", "proviso", "provisos", "defence", "defences", "procedure", "appeal",
    "limitation", "evidence", "proof", "burden", "old", "new", "ipc", "crpc", "equivalent",
    "corresponding", "repealed", "example", "examples", "illustration", "illustrations", "meaning",
    "definition", "explain", "summary", "elements", "ingredients", "maximum", "minimum",
}
_FILLER = {"what", "is", "the", "a", "an", "of", "for", "in", "on", "under", "to", "and", "or", "are",
           "there", "any", "how", "much", "long", "which", "can", "be", "does", "do", "law", "section", "?"}


@dataclass
class Previous:
    question: str
    chunk_ids: list[str] = field(default_factory=list)  # the sources shown with its answer


def is_follow_up(question: str, has_citation: bool) -> bool:
    if has_citation:
        return False
    if _CONTINUES.search(question):
        return True
    if _POINTS_BACK.search(question) and len(question.split()) <= SHORT:
        return True
    words = [w for w in re.findall(r"[a-z-]+", question.lower()) if w not in _FILLER]
    return bool(words) and all(w in _ASPECTS for w in words)
