"""Fill earlier wordings the amendment footnotes lack, from archived copies of each Act.

    python -m scripts.fill_old_wordings <archive dir>     (scripts/fetch_archived_acts.py's output)

Reads data/amendments/<code>.jsonl and data/statutes/<code>.jsonl, writes data/amendments/_filled.jsonl:
one line per substitution whose earlier wording was found, {act, section, by, note, old_text, source}.
app/judgments/law_at_time.History applies them. Run it again after each scripts/build_corpus.py run.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from app.history.amendments import SUBSTITUTED, Amendment
from app.history.fill import fill, read_copy
from scripts.fetch_archived_acts import complete
from scripts.pdf_text import fast_text

DATA = Path(__file__).resolve().parents[1] / "data"


def copies_of(folder: Path, entry: dict) -> list:
    out = []
    for c in entry.get("copies", []):
        path = folder / c["file"]
        if not path.exists() or not complete(path):
            continue
        ts = c["timestamp"]
        try:
            raw = fast_text(path) if path.suffix == ".pdf" else path.read_bytes().decode("latin-1")
        except Exception as e:  # a damaged copy is skipped, not fatal
            print(f"  {folder.name}/{path.name}: {type(e).__name__}")
            continue
        out.append(read_copy(raw, f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"))
    return out


def main() -> int:
    archive = Path(sys.argv[1])
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    stats: Counter = Counter()
    lines = []
    for code, entry in sorted(manifest.items()):
        amend, statute = DATA / "amendments" / f"{code}.jsonl", DATA / "statutes" / f"{code}.jsonl"
        if not amend.exists() or not statute.exists():
            continue
        amendments = [Amendment(**json.loads(x)) for x in amend.read_text(encoding="utf-8").splitlines()]
        gaps = sum(a.action == SUBSTITUTED and a.old_text is None for a in amendments)
        sections: dict[str, str] = {}
        for x in statute.read_text(encoding="utf-8").splitlines():
            r = json.loads(x)
            sections[r["section_number"]] = sections.get(r["section_number"], "") + " " + (r.get("text") or "")
        found = fill(amendments, sections, copies_of(archive / code, entry))
        stats["gaps"] += gaps
        stats["filled"] += len(found)
        lines += [json.dumps(f.__dict__, ensure_ascii=False) for f in found]
        if found:
            print(f"{code}: {len(found)}/{gaps}", flush=True)
    (DATA / "amendments" / "_filled.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    print(f"filled {stats['filled']} of {stats['gaps']} substitutions without their earlier wording "
          f"(in the {len(manifest)} Acts fetched)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
