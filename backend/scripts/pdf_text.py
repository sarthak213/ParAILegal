"""PDF → text for building the corpus, with lines kept and words intact.

pypdf keeps each printed line (the section parser needs line starts) but sometimes splits a
word at a kerning gap ("provi sions"); pdfminer keeps words intact but runs a page together.
So pypdf supplies the lines and pdfminer is the reference for joins: "a b" becomes "ab" when
pdfminer's text has "ab" and never "a b".
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path

_WORD = re.compile(r"[A-Za-z]+")


def _pypdf_text(path: Path) -> str:
    from pypdf import PdfReader

    # a form feed between pages: the corpus builder needs page ends (footnotes end there)
    return "\n\f\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def _pdfminer_text(path: Path) -> str:
    from pdfminer.high_level import extract_text

    return extract_text(str(path))


_TOKEN = re.compile(r"^([^A-Za-z]*)([A-Za-z]+)([^A-Za-z]*)$")


def repair_splits(lined: str, reference: str) -> tuple[str, int]:
    words = Counter(w.lower() for w in _WORD.findall(reference))
    ref_tokens = [w.lower() for w in _WORD.findall(reference)]
    pairs = Counter(zip(ref_tokens, ref_tokens[1:]))
    fixed = 0

    def should_join(a: str, b: str) -> bool:
        whole, pair = words[a + b], pairs[(a, b)]
        # the reference has the whole word, and either never the pair, or a half that only
        # ever occurs inside this split ("commit ted": "ted" never stands alone)
        return whole > 0 and (pair == 0 or min(words[a], words[b]) <= pair)

    def fix_line(line: str) -> str:
        nonlocal fixed
        tokens = line.split(" ")
        out: list[str] = []
        for tok in tokens:
            if out:
                prev = _TOKEN.match(out[-1])
                cur = _TOKEN.match(tok)
                # join only letter-to-letter: no punctuation between the halves
                if prev and cur and not prev.group(3) and not cur.group(1) \
                        and should_join(prev.group(2).lower(), cur.group(2).lower()):
                    out[-1] = out[-1] + tok
                    fixed += 1
                    continue
            out.append(tok)
        return " ".join(out)

    # spaces within a line only, never across a line break
    return "\n".join(fix_line(line) for line in lined.split("\n")), fixed


def pdf_text(path: Path) -> str:
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    text, _ = repair_splits(_pypdf_text(path), _pdfminer_text(path))
    return text
