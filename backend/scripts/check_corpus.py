"""Quality report for the built corpus (data/statutes + data/constitution.jsonl).

    python scripts/check_corpus.py [--statutes data/statutes] [--constitution data/constitution.jsonl]

Checks what a reader would notice: sections listed in an Act's arrangement but missing from
the corpus, Acts whose text was mostly lost, footnotes or page headers left in the text,
words split in two, one section's text running into the next, and the old <-> new links.
Exit status 1 when a hard check fails.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

FOOTNOTE = re.compile(r"(?:^|\s)\d{1,2}\s?\.\s*(?:Subs|Ins|Rep)\s?\.\s*by\b")
HEADER = re.compile(r"\bIndiaCode\b|THE CONSTITUTION OF INDIA\s*\(Part")


def load(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def main() -> int:
    root = Path(__file__).resolve().parents[1] / "data"
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--statutes", type=Path, default=root / "statutes")
    ap.add_argument("--constitution", type=Path, default=root / "constitution.jsonl")
    args = ap.parse_args()

    report = json.loads((args.statutes / "_build_report.json").read_text(encoding="utf-8"))
    acts = {p.stem: load(p) for p in sorted(args.statutes.glob("*.jsonl"))}
    rows = [r for records in acts.values() for r in records]
    constitution = load(args.constitution) if args.constitution.exists() else []
    texts = [r["text"] for r in rows] + [r["text"] for r in constitution]
    failures: list[str] = []

    print(f"Statutes: {len(acts)} Acts, {len({(r['act_code'], r['section_number']) for r in rows})} sections, "
          f"{len(rows)} chunks, {sum(len(t.split()) for t in texts):,} words in all")
    print(f"  in force {sum(1 for a in acts.values() if a[0]['status'] == 'in force')}, "
          f"repealed/replaced {sum(1 for a in acts.values() if a[0]['status'] == 'repealed')}, "
          f"from PDF {sum(r['from_pdf'] for r in report)}/{len(report)}")

    # coverage against each Act's own arrangement of sections
    incomplete = [r for r in report if r["toc"] and r["missing"]]
    listed = sum(r["toc"] for r in report)
    found = sum(r["found"] for r in report)
    print(f"Coverage: {found}/{listed} listed sections found ({100 * found / max(1, listed):.2f}%)")
    for r in incomplete:
        print(f"  {r['title']}: missing {', '.join(r['missing'][:12])}")
    no_toc = [r["title"] for r in report if not r["toc"]]
    if no_toc:
        print(f"  no arrangement of sections ({len(no_toc)}): {'; '.join(no_toc[:10])}")
    truncated = [r["title"] for r in report if r["truncated"]]
    if truncated:
        failures.append(f"{len(truncated)} Acts built from a truncated text extraction")

    # text kept: a low share means text was dropped (footnotes and the arrangement are ~10-25%)
    low = sorted((r for r in report if r.get("kept") is not None and r["kept"] < 0.6 and r["chunks"]),
                 key=lambda r: r["kept"])
    print(f"Text kept: median {sorted(r.get('kept', 1) for r in report)[len(report) // 2]:.2f}; "
          f"{len(low)} Acts under 60%")
    for r in low[:10]:
        print(f"  {r['title']}: {r['kept']:.0%}")

    # residue of page furniture
    leaks = sum(len(FOOTNOTE.findall(t)) for t in texts)
    headers = sum(len(HEADER.findall(t)) for t in texts)
    words = re.findall(r"[a-z]+", " ".join(texts).lower())
    counts = Counter(words)
    splits = sum(1 for a, b in zip(words, words[1:])
                 if counts[a + b] >= 3 and min(counts[a], counts[b]) <= 2)
    print(f"Residue: {leaks} footnotes, {headers} page headers, {splits} split words "
          f"(per 10k words: {10_000 * (leaks + splits) / max(1, len(words)):.2f})")
    if headers:
        failures.append(f"{headers} page headers/watermarks in the text")

    # one section running into the next: the next section's number and heading inside it
    bleed = 0
    bled: list[str] = []
    for records in acts.values():
        sections = {}
        for r in records:
            sections.setdefault(r["section_number"], r)
        order = list(sections)
        for a, b in zip(order, order[1:]):
            head = re.sub(r"[^a-z]", "", sections[b]["section_title"].lower())[:24]
            body = " ".join(r["text"] for r in records if r["section_number"] == a)
            # the next section's number followed by its own heading (not "under section 5A.")
            title_words = re.escape(" ".join(sections[b]["section_title"].split()[:3]))
            if len(head) >= 12 and re.search(rf"(?:^|\s)\[?{re.escape(b)}\s?\.\s*\[?{title_words}", body):
                bleed += 1
                bled.append(f"{records[0]['act_title']} s. {a} -> {b}")
    print(f"Bleed: {bleed} sections contain the start of the next")
    for x in bled[:10]:
        print(f"  {x}")
    if bleed:
        failures.append(f"{bleed} sections bleed into the next")

    # old <-> new links
    linked = Counter(r["act_code"] for r in rows if r.get("corresponds_to"))
    derived = {r["act_code"] for r in rows if r.get("derived_links")}
    print(f"Derived section links (not official; scripts/derive_section_links.py): {len(derived)} Acts, "
          f"{sum(len(r.get('derived_links') or []) for r in rows)} links")
    print(f"Links: section correspondences {dict(linked)}; "
          f"Acts naming their replacement {sum(1 for a in acts.values() if a[0].get('replaced_by'))}; "
          f"Acts naming what they replaced {sum(1 for a in acts.values() if a[0].get('replaces'))}")
    for code in ("ipc", "crpc", "iea", "bns", "bnss", "bsa"):
        if code not in acts:
            failures.append(f"{code.upper()} missing from the corpus")

    # Constitution
    if constitution:
        arts = {r["article_number"] for r in constitution}
        numbered = [a for a in arts if a[0].isdigit()]
        schedules = sorted(int(a.split("_")[1]) for a in arts if a.startswith("SCHEDULE_"))
        print(f"Constitution: {len(numbered)} articles, Preamble {'PREAMBLE' in arts}, "
              f"schedules {schedules}, {len(constitution)} chunks")
        for must in ("14", "19", "21", "21A", "32", "44", "226", "356", "368", "370"):
            if must not in arts:
                failures.append(f"Article {must} missing")
        if schedules != list(range(1, 13)):
            failures.append(f"schedules {schedules}")
    else:
        failures.append("no Constitution corpus")

    print("\nFAIL: " + "; ".join(failures) if failures else "\nAll hard checks passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
