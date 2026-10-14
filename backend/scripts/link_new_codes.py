"""Section links from the old criminal codes to the new ones, read from the two texts.

    python -m scripts.link_new_codes --check          # against the links already known (legal_data)
    python -m scripts.link_new_codes --out links.json # for build_corpus.py --section-links

The BNS, BNSS and BSA keep most of the IPC's, CrPC's and Evidence Act's wording, renumbered,
sometimes merged (the IPC's definitions, ss.8-52A, are all in BNS s.2) or split (IPC s.498A is
BNS ss.85 and 86). So a new section that carries much of an old one's wording, word for word, is
its successor; and an old section that carries much of a new one's (a split) is its source.

"Much of the wording": the share of a section's 6-word runs found in the other. A link needs a
share of SHARE either way and a run count of MIN_RUNS, so a shared formula ("shall be punished
with imprisonment of either description for a term which may extend to") cannot make one alone.

The links are read from the texts, not copied from any published table. Against the 535 links
app/search/legal_data.py lists, 527 first links agree; with a share of STRONG or more, 513 of 514.
Those go in as correspondences; weaker ones as derived links with their score, which answers call
"probably corresponds". A section legal_data lists keeps its link from there.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.search.legal_data import OLD_CODES, SPLIT_TARGETS  # noqa: E402

PAIRS = [("IPC", "BNS"), ("CRPC", "BNSS"), ("IEA", "BSA")]
RUN = 6          # words in a run
SHARE = 0.35     # share of a section's runs found in the other
MIN_RUNS = 4     # and at least this many runs in common
STRONG = 0.5     # a share at which the link is stated plainly (write)

# runs any two penal provisions may share: punishment formulas, not wording of the offence
_BOILERPLATE = re.compile(
    r"shall be punished with|imprisonment of either description|for a term which may extend to|"
    r"shall also be liable to fine|with fine which may extend to|or with fine or with both|"
    r"the provisions of this sanhita|the provisions of this code|for the purposes of this section",
    re.IGNORECASE)


def load(statutes: Path, code: str) -> dict[str, dict]:
    """section number -> {title, text} (parts joined), without schedules and omitted sections."""
    out: dict[str, dict] = {}
    for line in (statutes / f"{code}.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        if str(r["section_number"]).upper().startswith(("SCHEDULE", "FULL")):
            continue
        s = out.setdefault(r["section_number"], {"title": r.get("section_title") or "", "text": ""})
        s["text"] += " " + (r.get("text") or "")
    return {k: v for k, v in out.items() if not re.match(r"\[?(repealed|omitted)", v["title"], re.IGNORECASE)}


def words(text: str) -> list[str]:
    text = _BOILERPLATE.sub(" ", re.sub(r"\d{1,2}\[|[\[\]*]", " ", text))
    return re.findall(r"[a-z0-9]+", text.lower())


def runs(text: str) -> set[tuple[str, ...]]:
    w = words(text)
    return {tuple(w[i:i + RUN]) for i in range(len(w) - RUN + 1)}


def link(old: dict[str, dict], new: dict[str, dict]) -> dict[str, list[tuple[str, float]]]:
    """old section -> [(new section, score)], best first: the new sections that carry much of its
    wording, or whose wording it carries."""
    old_runs = {k: runs(v["text"]) for k, v in old.items()}
    new_runs = {k: runs(v["text"]) for k, v in new.items()}
    index: dict[tuple, list[str]] = defaultdict(list)
    for k, rs in new_runs.items():
        for r in rs:
            index[r].append(k)
    out: dict[str, list[tuple[str, float]]] = {}
    for ok, ors in old_runs.items():
        if not ors:
            continue
        common: dict[str, int] = defaultdict(int)
        for r in ors:
            for nk in index.get(r, ()):
                common[nk] += 1
        found = []
        for nk, n in common.items():
            share = max(n / len(ors), n / max(len(new_runs[nk]), 1))
            if n >= MIN_RUNS and share >= SHARE:
                found.append((nk, round(share, 3)))
        if found:
            out[ok] = sorted(found, key=lambda x: -x[1])
    # a short punishing section ("379. Punishment for theft") shares little wording with anything:
    # the new code puts the definition and its punishment in one section, the one the definition
    # (s.378, "Theft") went to
    defined = {_key(v["title"]): k for k, v in old.items()}
    for ok, v in old.items():
        m = re.match(r"punishment for (.+)", _key(v["title"]))
        if ok not in out and m and (source := defined.get(m.group(1))) in out:
            out[ok] = [(n, s) for n, s in out[source][:1]]
    return out


def _key(title: str) -> str:
    return " ".join(re.sub(r"[^a-z ]+", " ", title.lower()).split())


def known(old_code: str) -> dict[str, set[str]]:
    """The hand-checked links (app/search/legal_data.py): old section -> new sections."""
    new_code, table = OLD_CODES[old_code]
    out = {o: {n} for o, n in table.items()}
    for ref, targets in SPLIT_TARGETS.items():
        code, _, sec = ref.partition(" ")
        if code == old_code:
            out.setdefault(sec, set()).update(t.split()[1] for t in targets)
    return out


def check(statutes: Path) -> None:
    for old_code, new_code in PAIRS:
        old, new = load(statutes, old_code), load(statutes, new_code)
        got = link(old, new)
        truth = {o: n for o, n in known(old_code).items() if o in old}
        judged = [o for o in got if o in truth]
        top_right = sum(got[o][0][0] in truth[o] for o in judged)
        any_right = sum(bool({n for n, _ in got[o]} & truth[o]) for o in judged)
        wrong = [(o, got[o][:2], sorted(truth[o])) for o in judged if not ({n for n, _ in got[o]} & truth[o])]
        print(f"{old_code} -> {new_code}: {len(old)} old sections, {len(got)} linked "
              f"({100 * len(got) / len(old):.0f}%) | against the {len(truth)} known: found {len(judged)}, "
              f"first link right {top_right}, any link right {any_right}, wrong {len(wrong)}")
        for w in wrong[:8]:
            print(f"    {old_code} {w[0]}: got {w[1]}, known {w[2]}")
        missed = [o for o in truth if o not in got]
        print(f"    known but not found: {len(missed)} {missed[:12]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--statutes", type=Path, default=ROOT / "data" / "statutes")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    if args.check:
        check(args.statutes)
    if args.out:
        write(args.statutes, args.out)
    return 0


def title_of(statutes: Path, code: str) -> str:
    return json.loads((statutes / f"{code}.jsonl").open(encoding="utf-8").readline())["act_title"]


def write(statutes: Path, out: Path) -> None:
    """Two tables per code: links whose wording shows them (a share of STRONG or more: 513 of the
    514 that legal_data also lists agree) go in as correspondences; weaker ones as derived links,
    with their score ("probably corresponds" in answers). Sections legal_data lists keep its link."""
    tables = []
    for old_code, new_code in PAIRS:
        old, new = load(statutes, old_code), load(statutes, new_code)
        got, hand = link(old, new), known(old_code)
        strong = [[o, n] for o, ls in got.items() if o not in hand for n, s in ls if s >= STRONG]
        weak = [[o, n, s] for o, ls in got.items() if o not in hand for n, s in ls if s < STRONG]
        names = {"old_act": title_of(statutes, old_code), "new_act": title_of(statutes, new_code)}
        tables += [{**names, "sections": strong, "method": "matching wording"},
                   {**names, "sections": weak, "derived": True, "method": "matching wording (weak)"}]
        print(f"{old_code} -> {new_code}: {len(strong)} links, {len(weak)} derived "
              f"(besides the {len(hand)} in legal_data)")
    out.write_text(json.dumps({"method": __doc__.split("\n")[0], "tables": tables}, indent=0, ensure_ascii=False),
                   encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
