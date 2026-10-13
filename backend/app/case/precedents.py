"""Case Builder: Supreme Court precedents for the facts (docs/OFFLINE-RAG.md section 8).

    find      no model: judgments on the same provisions (old codes included: a BNS 85 case
              finds the IPC 498A judgments) and the same kind of facts, each with what it held,
              the passages that match, its standing in later judgments (citator), and the law it
              applied as it stood then (app/judgments/law_at_time.py)
    compare   the model reads the facts and one precedent and lists what is alike and what
              differs, each point quoting both sides; a quote that is in neither text is dropped

A precedent whose facts differ is still worth citing for the principle it lays down; the
comparison says how far it carries.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from rapidfuzz import fuzz

from app.judgments.law_at_time import History, law_at_time
from app.judgments.store import JudgmentStore, catchline, citation, holdings, passages
from app.llm.client import complete_json
from app.search.engine import ref_of

MAX_HOLDINGS = 3


def older_refs(refs: list[str], engine: Any) -> list[str]:
    """The refs and the provisions they replaced (BNS 103 -> IPC 302): judgments before July
    2024 cite the old codes. Built once from the corpus's correspondence tables."""
    replaced = getattr(engine, "_replaced_by_new", None)
    if replaced is None:
        replaced = defaultdict(list)
        for c in engine.chunks:
            for new in c.get("corresponds_to") or []:
                replaced[new].append(ref_of(c))
        engine._replaced_by_new = replaced
    out = list(refs)
    for r in refs:
        out += [o for o in replaced.get(r, []) if o not in out]
    return out


def standing(status: dict, titles: dict[str, str] | None = None) -> dict:
    """The citator's record for a judgment, as counts and the overruling notes (with the sentence
    that says so: "overruled, as noted in", never a bare "overruled", since a later judgment may
    only be reporting it)."""
    return {
        "cited_by": len(status.get("cited_by", [])),
        "followed_by": len(status.get("followed_by", [])),
        "distinguished_by": len(status.get("distinguished_by", [])),
        "doubted_by": len(status.get("doubted_by", [])),
        "overruled_noted_in": status.get("overruled_noted_in", []),
        "per_incuriam_noted_in": status.get("per_incuriam_noted_in", []),
    }


def find(facts: str, refs: list[str], acts: list[str], store: JudgmentStore, engine: Any,
         history: History, k: int = 5) -> list[dict]:
    query = " ".join(acts) if acts else facts
    cited = older_refs(refs, engine)
    out = []
    for hit in store.search(query, k=k, refs=cited):
        record = store.record(hit.id) or {}
        held = holdings(record)
        # the holdings that share most words with the facts first, in the headnote's order otherwise
        words = set(re.findall(r"[a-z]{4,}", query.lower()))
        held = sorted(held, key=lambda h: -len(words & set(re.findall(r"[a-z]{4,}", h["text"].lower()))))
        own = [p for p in record.get("provisions") or [] if p["ref"] in hit.matched_refs] or \
              (record.get("provisions") or [])[:3]
        then = law_at_time({**record, "provisions": own}, engine, history, limit=3)
        out.append({
            **hit.to_dict(),
            "bench": record.get("bench", []),
            "status": standing(hit.status),
            "holdings": held[:MAX_HOLDINGS],
            "passages": passages(record, query),
            "law_at_time": [{"ref": p.ref, "note": p.note, "amended_since": p.amended_since,
                             "now": p.now} for p in then],
        })
    return out


# ── Comparison ───────────────────────────────────────────────────────────────────────────────

_POINT = {"type": "object", "properties": {
    "point": {"type": "string", "maxLength": 160},
    "our_fact": {"type": "string", "maxLength": 240},
    "their_fact": {"type": "string", "maxLength": 240}},
    "required": ["point", "our_fact", "their_fact"]}
# bounded lengths: the grammar stops a long-winded reply before it runs out of tokens mid-string
COMPARE_SCHEMA = {
    "type": "object",
    "properties": {
        "similar": {"type": "array", "items": _POINT, "maxItems": 3},
        "different": {"type": "array", "items": _POINT, "maxItems": 3},
        "bearing": {"type": "string", "maxLength": 600},
    },
    "required": ["similar", "different", "bearing"],
}

COMPARE_PROMPT = """Compare the facts of our case with a Supreme Court judgment.

Our case:
{facts}

The judgment: {title}, decided {decided}.
What it was about:
{catch}
What it held:
{held}

List up to three ways the facts are alike ("similar") and up to three ways they differ ("different"). Compare
what happened (who did what, to whom, how, with what result), not the charges, sections or the court's
procedure: our case has not been charged or tried. A point must be true of both texts as written: do not call
something alike unless both texts say it, and do not add anything our case does not say. For each, give a
short point (under 20 words), then copy the exact words from our case ("our_fact") and from the judgment ("their_fact")
that show it; use "" for a side that says nothing about it. Then in one or two sentences ("bearing") say how
the judgment's principle could still apply to our case, or why it may be distinguished. Use only the texts above."""


def _norm(text: str) -> str:
    """Letters and digits only: a quote still matches when the scan split a word ("in- laws") or
    the model changed a dash or a quote mark."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


QUOTE_MATCH = 92        # rapidfuzz score: a quote the model copied from a scan, correcting its noise
FUZZY_MIN_CHARS = 25    # ("intimidatig", "f11rher", a margin letter: "the c parties"); short quotes must be exact


def quoted(quote: str, text: str) -> bool:
    """The quote is in the text (both normalised by _norm); one the model shortened with "..." when
    its pieces are there in order. A long piece may differ from the text by a scan's stray letters."""
    pos = 0
    for piece in re.split(r"\.{3}|…", quote):
        piece = _norm(piece)
        if not piece:
            continue
        at = text.find(piece, pos)
        if at >= 0:
            pos = at + len(piece)
            continue
        if len(piece) < FUZZY_MIN_CHARS:
            return False
        found = fuzz.partial_ratio_alignment(piece, text[pos:], score_cutoff=QUOTE_MATCH)
        if found is None:
            return False
        pos += found.dest_end
    return pos > 0


# a point about the proceedings, not what happened: our case has none to compare ("our case has not
# been charged", "the judgment cites IPC sections", "witnesses testified")
_PROCEDURE = re.compile(r"\b(charge[sd]?|charge.?sheet|tried|trial|convict\w*|acquit\w*|appeal\w*|high court|"
                        r"sections?|ipc|penal code|witness\w*|testif\w*|deposed?|evidence|sentenc\w*|fir|"
                        r"record|cites?|holdings?|prejudice\w*)\b", re.IGNORECASE)


def check_points(points: list[dict], ours: str, theirs: str, both: bool = False) -> tuple[list[dict], int]:
    """Points whose quotes are in the texts they claim to come from and that compare what
    happened, not the proceedings; the count of points dropped.
    both: each point must quote both texts, as a likeness must ("both were charged under s.306"
    with nothing from our facts is a likeness the facts do not show)."""
    a, b = _norm(ours), _norm(theirs)
    kept = []
    for p in points:
        our, their = (p.get("our_fact") or "").strip(), (p.get("their_fact") or "").strip()
        point = (p.get("point") or "").strip()
        if both and not (our and their):
            continue
        if not ((our or their) and (not our or quoted(our, a)) and (not their or quoted(their, b))):
            continue
        if _PROCEDURE.search(point):
            continue
        if both and _borrowed_role(point, ours, theirs):
            continue
        kept.append({"point": point, "our_fact": our, "their_fact": their})
    return kept, len(points) - len(kept)


# who someone is: a likeness that names a role must find it in both texts. The model's commonest slip
# lends one case the other's people ("the husband and father-in-law demanded money" when our facts
# name the husband and his mother; "both between neighbours" when only ours were)
_ROLES = re.compile(r"\b(husband|wife|father|mother|son|daughter|brother|sister|(?:father|mother|brother|sister)-in-law|"
                    r"in-laws|uncle|aunt|neighbou?r|landlord|tenant|employer|employee|manager|teacher|student|doctor|"
                    r"patient|police\w*|constable|officer|clerk|driver|shopkeeper|stranger|friend|cashier)s?\b", re.IGNORECASE)


def _borrowed_role(point: str, ours: str, theirs: str) -> list[str]:
    """The roles a likeness names that one of the texts does not."""
    def has(role: str, text: str) -> bool:
        stem = role.lower().rstrip("s")[:6]
        return re.search(rf"\b{re.escape(stem)}", text.lower()) is not None
    return [m.group(0) for m in _ROLES.finditer(point) if not (has(m.group(0), ours) and has(m.group(0), theirs))]


# what the model reads, in characters: the prompt, these and the reply must fit the model's context
# (8,192 tokens); one headnote's holdings alone ran to 12,000 tokens
HOLDING_CHARS, HOLDINGS_CHARS, FACTS_CHARS = 1500, 5000, 4000


def _clip(text: str, limit: int) -> str:
    """At most limit characters, ending at a sentence when one ends near the limit."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = cut.rfind(". ")
    return cut[:end + 1] if end > limit // 2 else cut.rsplit(" ", 1)[0] + " ..."


def _their_text(record: dict) -> tuple[str, str]:
    """What the model reads of the judgment: its catchline and holdings (both, and the holdings as listed)."""
    held, total = [], 0
    for h in holdings(record)[:4]:
        text = _clip(h["text"], HOLDING_CHARS)
        if held and total + len(text) > HOLDINGS_CHARS:
            break
        held.append(f"- {text}")
        total += len(text)
    held_text = "\n".join(held) or "(the headnote gives no holdings)"
    return f"{catchline(record)}\n{held_text}", held_text


async def compare_reply(facts: str, record: dict, base_url: str) -> dict:
    """The model's comparison, as it wrote it (review() checks it)."""
    _, held_text = _their_text(record)
    prompt = COMPARE_PROMPT.format(facts=_clip(facts, FACTS_CHARS), title=record.get("title", ""), decided=record.get("decided", ""),
                                   catch=catchline(record), held=held_text)
    return await complete_json(base_url, [{"role": "user", "content": prompt}], COMPARE_SCHEMA, max_tokens=900)


async def compare(facts: str, record: dict, base_url: str) -> dict:
    result = review(await compare_reply(facts, record, base_url), facts, record)
    return apply_checks(result, await check_reply(result, facts, record, base_url))


# ── Checking each likeness ───────────────────────────────────────────────────────────────────
# The model's quotes are checked above; its one-line point is not, and a likeness is where it slips:
# it lends one case a detail of the other ("both incidents occurred between neighbours" when only
# ours says so). A second, narrower question to the same model catches part of it: is this true of
# our case, and of the judgment? Differences are not checked: on "our case does not mention X" the
# model answered "no" though it was true, and dropped five good points for one slip (eval/case_compare.py).

CHECK_SCHEMA = {
    "type": "object",
    "properties": {"checks": {"type": "array", "items": {"type": "object", "properties": {
        "n": {"type": "integer"},
        "ours": {"type": "string", "enum": ["yes", "no"]},
        "theirs": {"type": "string", "enum": ["yes", "no"]}},
        "required": ["n", "ours", "theirs"]}}},
    "required": ["checks"],
}

CHECK_PROMPT = """Below are the facts of our case, a Supreme Court judgment, and numbered statements that say something is true of both.
For each statement answer two questions, strictly from the texts:
- "ours": is the statement true of our case, as our case's text tells it? "no" if our text does not say it.
- "theirs": is the statement true of the judgment's case, as the judgment's text tells it? "no" if the judgment's text does not say it.

Our case:
{facts}

The judgment:
{theirs}

Statements:
{points}"""


async def check_reply(result: dict, facts: str, record: dict, base_url: str) -> dict:
    """The model's yes/no on each kept likeness ({} when there are none, or the check fails)."""
    points = [p["point"] for p in result.get("similar", [])]
    if not points:
        return {}
    theirs, _ = _their_text(record)
    listed = "\n".join(f"{i}. {p}" for i, p in enumerate(points, 1))
    prompt = CHECK_PROMPT.format(facts=_clip(facts, FACTS_CHARS), theirs=theirs, points=listed)
    try:
        return await complete_json(base_url, [{"role": "user", "content": prompt}], CHECK_SCHEMA,
                                   max_tokens=40 + 25 * len(points))
    except (RuntimeError, ValueError) as e:
        print(f"Comparison check: {type(e).__name__}: {e}")
        return {}


def apply_checks(result: dict, reply: dict) -> dict:
    """Without the likenesses the check found untrue of either text. Unchecked ones (no reply) are
    kept, as before."""
    verdict = {c.get("n"): c for c in reply.get("checks", [])}
    kept = [p for n, p in enumerate(result.get("similar", []), 1)
            if not ((c := verdict.get(n)) and (c.get("ours") == "no" or c.get("theirs") == "no"))]
    dropped = len(result.get("similar", [])) - len(kept)
    return {**result, "similar": kept, "dropped": result.get("dropped", 0) + dropped, "checked": bool(verdict)}


def review(reply: dict, facts: str, record: dict) -> dict:
    """The comparison with only the points its quotes bear out."""
    theirs, _ = _their_text(record)
    similar, dropped_a = check_points(reply.get("similar", [])[:3], facts, theirs, both=True)
    different, dropped_b = check_points(reply.get("different", [])[:3], facts, theirs)
    bearing = (reply.get("bearing") or "").strip()
    if bearing and not bearing.endswith((".", "?", "!")) and "." in bearing:
        bearing = bearing[:bearing.rfind(".") + 1]  # cut at the length limit: keep whole sentences
    return {"id": record.get("id"), "similar": similar, "different": different, "bearing": bearing,
            "dropped": dropped_a + dropped_b}


# ── For the brief ────────────────────────────────────────────────────────────────────────────

def evidence_text(record: dict, comparison: dict | None, notes: list[str], status: dict) -> str:
    """A precedent as the brief model reads it: holdings, the comparison the lawyer kept, the law then."""
    lines = ["Held: " + " ".join(h["text"] for h in holdings(record)[:MAX_HOLDINGS])]
    if comparison:
        for kind in ("similar", "different"):
            for p in comparison.get(kind, []):
                # the quotes with the point: the brief rests on the words of both texts, not the summary
                said = "; ".join(f'{side}: "{p[key]}"' for side, key in (("our facts", "our_fact"),
                                                                         ("the judgment", "their_fact")) if p.get(key))
                lines.append(f"Facts {kind}: {p['point']}" + (f" ({said})" if said else ""))
        if comparison.get("bearing"):
            lines.append(f"Bearing: {comparison['bearing']}")
    lines += [f"Law at the time: {n}" for n in notes]
    if status.get("overruled_noted_in"):
        o = status["overruled_noted_in"][0]
        lines.append(f"Caution: noted as overruled in {o.get('title', '')} ({o.get('decided', '')}).")
    return "\n".join(lines)
