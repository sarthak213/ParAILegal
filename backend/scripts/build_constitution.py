"""Build the Constitution corpus from the Legislative Department's official English PDF.

    python scripts/build_constitution.py <constitution.pdf> [--out data/constitution.jsonl]

The PDF is "The Constitution of India [As on <date>]" (Ministry of Law and Justice). It becomes
one record per article (long articles split into parts), the Preamble, the twelve Schedules and
the Appendices, in the schema ConstitutionLoader reads. The cleaning is the statute builder's
(scripts/build_corpus.py): footnotes and page furniture removed, articles cut at the next
article's start, split words rejoined.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from build_corpus import (
    clean_body,
    cut_sections,
    lines_of,
    pdf_to_text,
    section_text,
    split_parts,
    toc_entries,
    word_rejoiner,
)

ORDINALS = ["FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH", "SIXTH", "SEVENTH", "EIGHTH", "NINTH",
            "TENTH", "ELEVENTH", "TWELFTH"]
SCHEDULE_TITLES = {
    1: "The States and the Union territories",
    2: "Provisions as to the President, the Governors, the Speakers and Chairmen, the Judges and the "
       "Comptroller and Auditor-General",
    3: "Forms of Oaths or Affirmations",
    4: "Allocation of seats in the Council of States",
    5: "Provisions as to the Administration and Control of Scheduled Areas and Scheduled Tribes",
    6: "Provisions as to the Administration of Tribal Areas in the States of Assam, Meghalaya, "
       "Tripura and Mizoram",
    7: "Union List, State List and Concurrent List",
    8: "Languages",
    9: "Validation of certain Acts and Regulations",
    10: "Provisions as to disqualification on ground of defection",
    11: "Powers, authority and responsibilities of Panchayats",
    12: "Powers, authority and responsibilities of Municipalities, etc.",
}
_PREFIX = r"(?:\d{1,3}\s?\[)?"


def schedule_starts(text: str) -> dict[int, int]:
    """Offset of each Schedule's start in the text after the last article. A schedule opens
    with its heading ("1[FIRST SCHEDULE") and then its article reference ("[Articles 1 and 4]");
    when the heading is missing from the PDF's text layer, the reference line marks the start."""
    # the appendices (amending Acts, orders) have schedules of their own: stop before them
    appendix = re.search(r"(?m)^APPENDIX\s+I\b", text)
    text = text[:appendix.start()] if appendix else text
    found: dict[int, int] = {}
    for n, word in enumerate(ORDINALS, 1):
        m = re.search(rf"(?m)^{_PREFIX}(?:THE\s+)?{word}\s+SCHEDULE\b", text)
        if m:
            found[n] = m.start()
    refs = [m.start() for m in re.finditer(r"(?m)^[\[(]\s*Articles?\s+\d", text)]
    for n in range(1, 13):
        if n in found:
            continue
        lo = max((v for k, v in found.items() if k < n), default=0)
        hi = min((v for k, v in found.items() if k > n), default=len(text))
        # the reference line of schedule n-1 comes first; schedule n's is the next one
        in_gap = [r for r in refs if lo < r < hi]
        if len(in_gap) >= 2:
            found[n] = in_gap[1]
        elif in_gap and n - 1 not in found:
            found[n] = in_gap[0]
    return dict(sorted(found.items(), key=lambda kv: kv[1]))


def record(number: str, title: str, text: str, kind: str, part: str = "", part_name: str = "",
           label: str = "", n: int = 1, status: str = "active") -> dict:
    return {
        "chunk_id": f"constitution::{number}::{n}",
        "article_number": number,
        "title": title,
        "text": text,
        "full_text": text,
        "label": label,
        "chunk_type": kind,
        "status": status,
        "part_number": part.replace("Part ", "") or None,
        "part_name": part_name or None,
        "cross_references": [],
        "token_count": len(text.split()),
        "needs_split": False,
        "source": "legislative_department_coi_2026",
    }


def build(text: str) -> tuple[list[dict], dict]:
    # a few pages carry their running header as text: "3 THE CONSTITUTION OF INDIA" + "(Part I.—…)"
    text = re.sub(r"(?m)^[ \t]*\d{1,4}[ \t]+THE CONSTITUTION OF INDIA[ \t]*\n(?:[ \t]*\([^)\n]*\)[ \t]*\n)?", "", text)
    lines = lines_of(text)
    toc_start = next(i for i, line in enumerate(lines) if line.strip() == "PREAMBLE")
    body = next(i for i in range(toc_start + 1, len(lines)) if lines[i].strip() == "PREAMBLE")
    entries = toc_entries(lines, toc_start, body, group="PART", bracketed=True)
    sections, tail = cut_sections(lines, entries, body + 1)
    records: list[dict] = []

    # Preamble: from the body start to the first article
    body_text = "\n".join(lines[body + 1:])
    first = re.search(r"(?m)^(?:\d{1,3}\s?\[)?1\.\s", body_text)
    preamble = clean_body(" ".join(body_text[:first.start() if first else 2000].split("\n")))
    preamble = re.sub(r"\s*PART\s+I\b.*$", "", preamble)
    records.append(record("PREAMBLE", "Preamble", preamble, "preamble"))

    for s in sections:
        body_of = section_text(s["raw"], s["number"], s["title"])
        omitted = bool(re.search(r"omitted", s["title"], re.IGNORECASE)) and len(body_of.split()) < 40
        title = re.sub(r"\s*[—–⎯-]+\s*Omitted\.?$", "", s["title"].strip("[] ."), flags=re.IGNORECASE)
        parts = split_parts(body_of)
        for n, part in enumerate(parts, 1):
            records.append(record(s["number"], title, part, "article", s["chapter"], s["chapter_title"],
                                  label=f"part {n}/{len(parts)}" if len(parts) > 1 else "", n=n,
                                  status="omitted" if omitted else "active"))

    # an article the arrangement lists as omitted may have no text at all ("[238. Omitted.]")
    got = {s["number"] for s in sections}
    for e in entries:
        if e["number"] not in got and re.search(r"omitted", e["title"], re.IGNORECASE):
            title = re.sub(r"\s*[—–⎯-]+\s*Omitted\.?$", "", e["title"].strip("[] ."), flags=re.IGNORECASE)
            records.append(record(e["number"], title, "Omitted.", "article", e["chapter"], e["chapter_title"],
                                  status="omitted"))

    # Schedules and appendices follow the last article (the statute builder's own schedule
    # split misses "1[FIRST SCHEDULE", so work on everything after the last article's start)
    last = sections[-1]
    after = last["raw"] + ("\n" + tail if tail else "")
    starts = schedule_starts(after)
    if starts:
        for r in [r for r in records if r["article_number"] == last["number"]]:
            records.remove(r)
        trimmed = section_text(after[:min(starts.values())], last["number"], last["title"])
        records.append(record(last["number"], last["title"].strip("[] ."), trimmed, "article",
                              last["chapter"], last["chapter_title"]))
    appendix = re.search(r"(?m)^APPENDIX\s+I\b", after)
    end_of_schedules = appendix.start() if appendix else len(after)
    order = list(starts.items())
    for (n, s), nxt in zip(order, [*order[1:], (None, end_of_schedules)], strict=True):
        chunk = clean_body(" ".join(after[s:nxt[1]].split("\n")))
        for k, part in enumerate(split_parts(chunk), 1):
            records.append(record(f"SCHEDULE_{n}", SCHEDULE_TITLES[n], part, "schedule",
                                  label=f"part {k}", n=k))
    if appendix:
        app_text = after[appendix.start():]
        heads = list(re.finditer(r"(?m)^APPENDIX\s+(I{1,3})\b", app_text))
        for h, nxt in zip(heads, [*heads[1:], None], strict=True):
            num = len(h.group(1))
            chunk = clean_body(" ".join(app_text[h.start():nxt.start() if nxt else len(app_text)].split("\n")))
            for k, part in enumerate(split_parts(chunk), 1):
                records.append(record(f"APPENDIX_{num}", f"Appendix {h.group(1)}", part, "appendix",
                                      label=f"part {k}", n=k))

    rejoin = word_rejoiner(r["text"] for r in records)
    for r in records:
        r["text"] = r["full_text"] = rejoin(r["text"])
    got = {s["number"] for s in sections}
    report = {"articles_listed": len(entries), "articles_found": len(sections),
              "missing": [e["number"] for e in entries if e["number"] not in got],
              "schedules": sorted(starts), "records": len(records),
              "kept": round(sum(len(r["text"].split()) for r in records) / max(1, len(text.split())), 3)}
    return records, report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "constitution.jsonl")
    ap.add_argument("--cache", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "pdf_text")
    args = ap.parse_args()
    raw = pdf_to_text(args.pdf, args.cache, args.pdf.stem)
    records, report = build(raw)
    with args.out.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    # the amendment history beside the Acts' (data/amendments/<code>.jsonl; act code "ART")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.history.amendments import extract_constitution

    texts: dict[str, str] = {}
    for r in records:
        texts[r["article_number"]] = texts.get(r["article_number"], "") + " " + (r.get("text") or "")
    amendments = extract_constitution(raw, texts)
    amend = args.out.parent / "amendments" / "constitution.jsonl"
    amend.parent.mkdir(parents=True, exist_ok=True)
    with amend.open("w", encoding="utf-8") as f:
        for a in amendments:
            f.write(json.dumps(a.as_dict(), ensure_ascii=False) + "\n")
    report["amendments"] = len(amendments)
    report["amendments_dated"] = sum(bool(a.effective) for a in amendments)
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
