"""Fetch archived India Code copies of the Acts whose amendment history lacks earlier wordings.

    python -m scripts.fetch_archived_acts <out dir>      (e.g. ../harvester-data/indiacode-archive)

A footnote "Subs. by Act 4 of 2005, s. 2, for section 4" says what replaced the section, not what it
said before; an older consolidated text of the Act still has the earlier words. The Internet Archive
holds two generations of India Code:

  the old site (to 2017)  fullact1.asp?tfnm=<year><act number>, plain text with footnotes ("1*[...]")
  the new site (2018 on)  /bitstream/.../A<year>-<number>.pdf

For each Act with a gap this saves up to three distinct copies of each kind (oldest first: the older a
copy, the more amendments came after it), as <out>/<act code>/<timestamp>.txt or .pdf, and writes
manifest.json. Resumable: a copy already saved is not fetched again. App/history/fill.py reads them.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
from collections import defaultdict
from pathlib import Path

from scripts.fetch_replaced_statutes import CDX, captures, get

ROOT = Path(__file__).resolve().parents[1]
PER_KIND = 3
PAUSE = 1.5
RECHECK = "--recheck" in sys.argv  # look again at Acts already done (to replace incomplete copies)  # seconds between requests: the Internet Archive asks to be treated gently


def acts_with_gaps() -> dict[str, tuple[int, int]]:
    """Act code -> (substitutions and omissions without their earlier wording, the latest one's year)."""
    gaps: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for f in (ROOT / "data" / "amendments").glob("*.jsonl"):
        if f.name.startswith("_"):
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            a = json.loads(line)
            if a["action"] in ("substituted", "omitted") and not a.get("old_text"):
                year = int((a.get("effective") or "")[:4] or a.get("year") or 0)
                gaps[a["act"]][0] += 1
                gaps[a["act"]][1] = max(gaps[a["act"]][1], year)
    return {k: (n, y) for k, (n, y) in gaps.items()}


def act_numbers() -> dict[str, tuple[str, str]]:
    out = {}
    for f in (ROOT / "data" / "statutes").glob("*.jsonl"):
        first = f.read_text(encoding="utf-8").split("\n", 1)[0]
        if first.strip():
            r = json.loads(first)
            if r.get("year") and r.get("act_number"):
                out[r["act_code"]] = (str(r["year"]), str(r["act_number"]))
    return out


def distinct(caps: list[tuple[str, str, int]], limit: int) -> list[tuple[str, str, int]]:
    """Oldest first, one per version (a copy within 1% of the size of one already kept is taken to be
    the same text, captured again)."""
    kept: list[tuple[str, str, int]] = []
    for c in sorted(caps):
        if all(abs(c[2] - k[2]) > 0.01 * k[2] for k in kept):
            kept.append(c)
        if len(kept) == limit:
            break
    return kept


def complete(path: Path) -> bool:
    """A saved copy is whole (a PDF cut off mid-download has no end marker)."""
    if path.suffix != ".pdf":
        return True
    with path.open("rb") as f:
        f.seek(max(0, path.stat().st_size - 2048))
        return b"%%EOF" in f.read()


def old_site(year: str, number: str) -> list[tuple[str, str, int]]:
    tfnm = f"{year}{int(number):02d}" if number.isdigit() else f"{year}{number}"
    q = urllib.parse.urlencode({"url": f"indiacode.nic.in/fullact1.asp?tfnm={tfnm}",
                                "fl": "timestamp,original,length", "filter": "statuscode:200"})
    out = []
    for line in get(f"{CDX}?{q}").decode().splitlines():
        ts, url, length = line.split(" ")
        # from 2018 the address answers with a short redirect page, not the Act
        if length.isdigit() and int(length) > 3000 and ts < "2018":
            out.append((ts, url, int(length)))
    return out


NEW_SITE_FROM = 2018  # the new site's copies are from 2018 on: they help only with changes after that
NEW_PER_ACT = 2


def fetch_act(code: str, year: str, number: str, gaps: tuple[int, int], out: Path) -> dict:
    entry = {"year": year, "number": number, "gaps": gaps[0], "copies": []}
    try:
        plan = [("old", c) for c in distinct(old_site(year, number), PER_KIND)]
        time.sleep(PAUSE)
        if number.isdigit() and gaps[1] >= NEW_SITE_FROM:
            plan += [("new", c) for c in distinct([c for c in captures(int(year), int(number))
                                                   if "/bitstream/" in c[1]], NEW_PER_ACT)]
        for kind, (ts, url, _) in plan:
            folder = out / code
            folder.mkdir(exist_ok=True)
            existing = [p for p in folder.glob(f"{ts}.*") if complete(p)]
            if existing:
                path = existing[0]
            else:
                data = get(f"https://web.archive.org/web/{ts}id_/{url}")
                time.sleep(PAUSE)
                if data[:4] == b"%PDF" and b"%%EOF" not in data[-2048:]:
                    print(f"  {code} {ts}: incomplete PDF ({len(data)} bytes), skipped", flush=True)
                    continue
                path = folder / f"{ts}.{'pdf' if data[:4] == b'%PDF' else 'txt'}"
                path.write_bytes(data)
            entry["copies"].append({"kind": kind, "timestamp": ts, "url": url, "file": path.name})
        entry["done"] = True
    except Exception as e:  # one Act's failure must not stop the rest; it is retried on the next run
        entry["error"] = f"{type(e).__name__}: {e}"
    return entry


def main() -> int:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    out = Path(next(a for a in sys.argv[1:] if not a.startswith("--")))
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    numbers = act_numbers()
    gaps = acts_with_gaps()
    todo = [c for c in sorted(gaps, key=lambda c: -gaps[c][0])
            if c in numbers and (RECHECK or not manifest.get(c, {}).get("done"))]
    print(f"{len(todo)} Acts to fetch ({len(gaps) - len(todo)} done or without an act number)", flush=True)
    # two Acts at a time: a search on the archive's index takes up to a minute, mostly waiting
    with ThreadPoolExecutor(2) as pool:
        jobs = {pool.submit(fetch_act, c, *numbers[c], gaps[c], out): c for c in todo}
        for i, job in enumerate(as_completed(jobs), 1):
            code, entry = jobs[job], job.result()
            manifest[code] = entry
            manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
            print(f"[{i}/{len(todo)}] {code}: {len(entry['copies'])} copies"
                  + (f" ({entry['error']})" if "error" in entry else ""), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
