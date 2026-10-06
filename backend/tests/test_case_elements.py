"""Every element's quote must be in the statute text it cites, word for word."""

import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

from app.case.elements import OFFENCES

STATUTES = Path(__file__).parents[1] / "data" / "statutes"
FILES = {"BNS": "bns.jsonl", "negotiable_instruments_act_1881": "negotiable_instruments_act_1881.jsonl"}


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip().lower()


@pytest.fixture(scope="module")
def sections():
    if not STATUTES.exists():
        pytest.skip("needs the corpus")
    out = defaultdict(str)
    for act, name in FILES.items():
        for line in (STATUTES / name).read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            out[f"{act} {r['section_number']}"] += " " + r["text"]
    return {ref: norm(t) for ref, t in out.items()}


@pytest.mark.parametrize("ref", sorted(OFFENCES))
def test_quotes_are_in_the_statute(ref, sections):
    for e in OFFENCES[ref].elements:
        source = e.source or ref
        assert source in sections, f"{source} not in the corpus"
        assert norm(e.quote) in sections[source], f"{ref}: {e.label!r}: quote not found in {source}"


def test_alternatives_come_in_groups():
    for o in OFFENCES.values():
        groups = defaultdict(int)
        for e in o.elements:
            assert e.kind in ("element", "any_of", "aggravation")
            if e.kind == "any_of":
                assert e.group, (o.ref, e.label)
                groups[e.group] += 1
        assert all(n >= 2 for n in groups.values()), (o.ref, dict(groups))
