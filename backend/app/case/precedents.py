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


def check_points(points: list[dict], ours: str, theirs: str, both: bool = False) -> tuple[list[dict], int]:
    """Points whose quotes are in the texts they claim to come from; the count of points dropped.
    both: each point must quote both texts, as a likeness must ("both were charged under s.306"
    with nothing from our facts is a likeness the facts do not show)."""
    a, b = _norm(ours), _norm(theirs)
    kept = []
    for p in points:
        our, their = (p.get("our_fact") or "").strip(), (p.get("their_fact") or "").strip()
        if both and not (our and their):
            continue
        if (our or their) and (not our or _norm(our) in a) and (not their or _norm(their) in b):
            kept.append({"point": p.get("point", "").strip(), "our_fact": our, "their_fact": their})
    return kept, len(points) - len(kept)


async def compare(facts: str, record: dict, base_url: str) -> dict:
    catch = catchline(record)
    held = holdings(record)
    held_text = "\n".join(f"- {h['text']}" for h in held[:4]) or "(the headnote gives no holdings)"
    prompt = COMPARE_PROMPT.format(facts=facts, title=record.get("title", ""), decided=record.get("decided", ""),
                                   catch=catch, held=held_text)
    reply = await complete_json(base_url, [{"role": "user", "content": prompt}], COMPARE_SCHEMA, max_tokens=900)
    theirs = f"{catch}\n{held_text}"
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
                lines.append(f"Facts {kind}: {p['point']}")
        if comparison.get("bearing"):
            lines.append(f"Bearing: {comparison['bearing']}")
    lines += [f"Law at the time: {n}" for n in notes]
    if status.get("overruled_noted_in"):
        o = status["overruled_noted_in"][0]
        lines.append(f"Caution: noted as overruled in {o.get('title', '')} ({o.get('decided', '')}).")
    return "\n".join(lines)
