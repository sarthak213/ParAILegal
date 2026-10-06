"""Build the Supreme Court judgments corpus from the AWS Open Data dataset (CC-BY-4.0).

    python -m scripts.build_judgments <SC>/pdf/2023 <SC>/metadata-json/2023 --out data/judgments/2023.jsonl

where <SC> is harvester-data/sc-judgments (PDFs from data/tar/year=YYYY/english/english.tar, metadata
from metadata/tar/year=YYYY/metadata.tar of s3://indian-supreme-court-judgments).

One record per judgment:
    id, title, petitioner, respondent, decided (YYYY-MM-DD), case_no, bench (judges), author,
    bench_size, disposal, citations {scr, insc}, summary (the Supreme Court Reports catchline),
    headnote (by the Court's Editorial Section), paragraphs [{n, text}],
    provisions [{ref, act, section, count}]    statutes the judgment cites, as corpus refs
    cases [{citation, kind, name, para, treatment}]   judgments it cites, with how it treats them

The citator (which judgment cites which, and whether it overrules, distinguishes or follows it)
is built from `cases` across all years by scripts/build_citator.py.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import unicodedata
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

# ── Metadata (the Supreme Court website's search-result row, kept as raw_html) ──────────────

def _cells(raw_html: str) -> list[str]:
    t = re.sub(r"<script.*?</script>|<select.*?</select>", " ", raw_html, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", "\x00", t))
    cells = (" ".join(x.split()).strip("| ").strip() for x in t.split("\x00"))
    return [c for c in cells if c]


def parse_metadata(meta: dict) -> dict:
    cells = _cells(meta.get("raw_html") or "")
    out: dict = {"id": meta["path"], "insc": (meta.get("nc_display") or "").replace("INSC", " INSC ").strip()}
    if "versus" in cells:
        i = cells.index("versus")
        out["petitioner"], out["respondent"] = cells[i - 1], cells[i + 1]
        out["title"] = f"{cells[i - 1]} v. {cells[i + 1]}"
    for c in cells:
        if re.fullmatch(r"\[\d{4}\] (?:Supp\.? )?\d* ?S\.C\.R\. \d+", c):
            out["scr"] = c
    labels = {"Decision Date :": "decided", "Case No :": "case_no", "Disposal Nature :": "disposal",
              "Bench :": "bench_size", "Direction Issue :": "direction"}
    for i, c in enumerate(cells[:-1]):
        if c in labels:
            out[labels[c]] = cells[i + 1]
        if c.startswith("Coram :"):
            # "Coram : KRISHNA MURARI", "*", ", AHSANUDDIN AMANULLAH": the author is starred
            judges, author, j = [], None, i
            names = [c[len("Coram :"):]] + cells[i + 1:i + 8]
            for n in names:
                if n == "*":
                    author = judges[-1] if judges else None
                    continue
                if re.match(r"^(Decision Date|[A-Z][a-z]+ [A-Z][a-z]+, \d{4}:|.*–)", n) or len(n) > 80:
                    break
                for name in n.strip(", ").split(","):
                    if name.strip():
                        judges.append(name.strip())
            out["bench"], out["author"] = judges, author
        if "–" in c and len(c) > 60 and "summary" not in out:
            out["summary"] = c
    if "decided" in out:
        d, m, y = out["decided"].split("-")
        out["decided"] = f"{y}-{m}-{d}"
    return out


# ── Text ─────────────────────────────────────────────────────────────────────────────────────

_LIGATURE_GAP = re.compile(r"(ff|fi|fl|ffi|ffl)\s{1,3}(?=[a-z])")
_MARGIN = re.compile(r"^[A-H]$")
_RUNNING = re.compile(r"^(?:\d+\s+SUPREME COURT REPORTS\s+\[\d{4}\].*|\[\d{4}\]\s+(?:Supp\.?\s+)?\d*\s*S\.C\.R\.\s+\d+(?:\s*:\s*\d{4} INSC \d+)?|\d{1,4}"
                      # the case title and the author repeated at the top of each page
                      r"|[A-Z0-9 .&@'’()/,\-]+ v\. [A-Z0-9 .&@'’()/,\-]+|\[[A-Z .,]+,\s*(?:C\.)?JJ?\.\])$")
_JUDGMENT_START = re.compile(r"^\s*(?:JUDGMENT|J U D G M E N T|ORDER|The Judgment of the Court was delivered by|"
                             r"The following Judgments? of the Court (?:was|were) delivered)", re.M)


def clean(text: str) -> str:
    text = _LIGATURE_GAP.sub(r"\1", unicodedata.normalize("NFKC", text))
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if _MARGIN.match(s) or _RUNNING.match(s):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines)


def split_parts(text: str) -> tuple[str, str]:
    """(headnote and case details, judgment body)."""
    m = _JUDGMENT_START.search(text)
    return (text[:m.start()], text[m.start():]) if m else ("", text)


_PARA = re.compile(r"^\s*(\d{1,3})\.\s+(?=[A-Z“\"(\[])", re.M)


def paragraphs(body: str) -> list[dict]:
    """Numbered paragraphs ("12. The appellant..."), in order; numbers must rise by one, so a
    numbered list inside a paragraph stays in it. The first may be 2 or 3 ("Delay condoned.
    2. Leave granted."); text before it is paragraph 0."""
    marks, last = [], 0
    for m in _PARA.finditer(body):
        n = int(m.group(1))
        if n == last + 1 or (last == 0 and n <= 3):
            marks.append((n, m.start(), m.end()))
            last = n
    if not marks:
        return [{"n": 0, "text": " ".join(body.split())}]
    out = []
    if body[:marks[0][1]].strip():
        out.append({"n": 0, "text": " ".join(body[:marks[0][1]].split())})
    for i, (n, start, end) in enumerate(marks):
        stop = marks[i + 1][1] if i + 1 < len(marks) else len(body)
        out.append({"n": n, "text": " ".join(body[end:stop].split())})
    return out


# ── Provisions cited ─────────────────────────────────────────────────────────────────────────

ACTS = {  # how judgments name the big codes -> corpus act codes
    "IPC": r"I\.?\s?P\.?\s?C\.?|Indian Penal Code|Penal Code",
    "CRPC": r"Cr\.?\s?P\.?\s?C\.?|Code of Criminal Procedure|Criminal Procedure Code",
    "IEA": r"(?:Indian )?Evidence Act",
    "BNS": r"BNS|Bharatiya Nyaya Sanhita",
    "BNSS": r"BNSS|Bharatiya Nagarik Suraksha Sanhita",
    "BSA": r"BSA|Bharatiya Sakshya Adhiniyam",
    "CPC": r"C\.?\s?P\.?\s?C\.?|Code of Civil Procedure",
}
_SEC = r"(?:[Ss]ections?|[Ss]ecs?\.|[Ss]s?\.|u/[Ss]s?\.?)\s*"
_NUM = r"\d{1,3}[A-Z]{0,2}(?:-[A-Z])?(?:\s*\(\d+\))*"
_NUMS = rf"{_NUM}(?:\s*(?:,|/|and|r/w|read with|&)\s*(?:{_SEC})?{_NUM})*"
_PROVISION = re.compile(rf"{_SEC}(?P<nums>{_NUMS})\s*(?:of\s+(?:the\s+)?)?(?P<act>{'|'.join(f'(?:{v})' for v in ACTS.values())})")
_ARTICLE = re.compile(rf"\b(?:Art(?:icle)?s?\.?)\s*(?P<nums>{_NUM}(?:\s*(?:,|and|&)\s*{_NUM})*)(?:\s+of\s+the\s+Constitution)?")


def _act_code(name: str) -> str | None:
    for code, pat in ACTS.items():
        if re.fullmatch(pat, name.strip()):
            return code
    return None


def provisions(text: str) -> list[dict]:
    found: Counter = Counter()
    for m in _PROVISION.finditer(text):
        act = _act_code(m["act"])
        if not act:
            continue
        for n in re.findall(_NUM, m["nums"]):
            section = re.sub(r"\(.*", "", n).replace("-", "").replace(" ", "").upper()
            found[f"{act} {section}"] += 1
    for m in _ARTICLE.finditer(text):
        for n in re.findall(_NUM, m["nums"]):
            found["ART " + re.sub(r"\(.*", "", n).replace(" ", "").upper()] += 1
    return [{"ref": r, "count": c} for r, c in found.most_common()]


# ── Cases cited, and how ─────────────────────────────────────────────────────────────────────

_CITATIONS = [
    ("scr", re.compile(r"\[(?P<y>\d{4})\]\s*(?:(?P<supp>Supp\.?)\s*)?(?P<v>\d{0,2})\s*S\.?\s?C\.?\s?R\.?\s*(?P<p>\d+)")),
    ("scc", re.compile(r"\((?P<y>\d{4})\)\s*(?P<v>\d{1,2})\s*S\.?\s?C\.?\s?C\.?\s*(?P<p>\d+)")),
    ("air", re.compile(r"AIR\s*(?P<y>\d{4})\s*SC\s*(?P<p>\d+)")),
    ("insc", re.compile(r"(?P<y>\d{4})\s*INSC\s*(?P<p>\d+)")),
]
TREATMENTS = {  # checked in the sentence that cites the case; the first match wins
    "overruled": r"\boverrul|\bno longer good law|\bnot (?:lay down )?(?:the )?(?:good|correct) law|\bdoes not lay down the correct law",
    "per incuriam": r"\bper incuriam",
    "doubted": r"\bdoubt(?:ed|ful)\b|\breferred to a larger bench|\brequires reconsideration",
    "distinguished": r"\bdistinguish",
    "followed": r"\bfollow(?:ed|ing)\b|\brelied (?:up)?on|\breliance (?:is|was) placed|\baffirm|\bapprov|\breiterat",
}


def citation_key(kind: str, m: re.Match) -> str:
    g = m.groupdict()
    if kind == "scr":
        return f"[{g['y']}] {'Supp. ' if g.get('supp') else ''}{g['v'] + ' ' if g.get('v') else ''}SCR {g['p']}"
    if kind == "scc":
        return f"({g['y']}) {g['v']} SCC {g['p']}"
    if kind == "air":
        return f"AIR {g['y']} SC {g['p']}"
    return f"{g['y']} INSC {g['p']}"


_NAME = re.compile(r"([A-Z][\w.&'()\- ]{2,80}?\s+v(?:s)?\.?\s+[A-Z][\w.&'()\- ]{2,80}?)[\s,(]*$")
# lead-in words before a case name: "In the case of", "Court in", "reliance was placed on"
_NAME_LEAD = re.compile(r"^.*?\b(?:[Ii]n (?:the )?(?:case of|matter of)|[Ii]n|[Ss]ee|[Cc]f\.?|on|of)\s+(?=[A-Z])")


def case_name(before: str) -> str:
    m = _NAME.search(before)
    if not m:
        return ""
    name = m.group(1).strip()
    while (lead := _NAME_LEAD.match(name)) and " v" in name[lead.end():]:
        name = name[lead.end():]
    return name.strip(" ,(")


def cases(paras: list[dict], own: set[str]) -> list[dict]:
    out, seen = [], set()
    for p in paras:
        text = p["text"]
        for kind, pat in _CITATIONS:
            for m in pat.finditer(text):
                key = citation_key(kind, m)
                if key in own:
                    continue  # the judgment's own citation, in its running header
                # the words around the citation (full stops are no guide: "v.", "S.C.C.", "J.")
                sentence = text[max(0, m.start() - 200): m.end() + 120]
                name = case_name(text[max(0, m.start() - 160):m.start()])
                treatment = next((t for t, pat in TREATMENTS.items() if re.search(pat, sentence, re.I)), None)
                item = (key, p["n"])
                if item in seen:
                    continue
                seen.add(item)
                out.append({"citation": key, "kind": kind, "name": name,
                            "para": p["n"], "treatment": treatment})
    return out


# ── One judgment ─────────────────────────────────────────────────────────────────────────────

def build(pdf: Path, meta_path: Path) -> dict | None:
    from scripts.pdf_text import pdf_text

    meta = parse_metadata(json.loads(meta_path.read_text(encoding="utf-8"))) if meta_path.exists() else {"id": pdf.stem}
    text = clean(pdf_text(pdf))
    front, body = split_parts(text)
    paras = paragraphs(body)
    own = {c for c in (meta.get("scr", "").replace("S.C.R.", "SCR"), meta.get("insc", "")) if c}
    headnote = " ".join(front.split())
    return {**meta, "headnote": headnote, "paragraphs": paras,
            "provisions": provisions(body), "cases": cases(paras, own),
            "words": sum(len(p["text"].split()) for p in paras)}


def _build_one(args: tuple[str, str]) -> dict | None:
    pdf, meta = map(Path, args)
    try:
        return build(pdf, meta)
    except Exception as e:  # one bad PDF must not stop a year
        return {"id": pdf.stem, "error": f"{type(e).__name__}: {e}"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf_dir", type=Path)
    ap.add_argument("meta_dir", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    pdfs = sorted(args.pdf_dir.glob("*.pdf"))[: args.limit or None]
    jobs = [(str(p), str(args.meta_dir / (p.stem.removesuffix("_EN") + ".json"))) for p in pdfs]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    errors = 0
    with ProcessPoolExecutor(args.workers) as pool, args.out.open("w", encoding="utf-8") as f:
        for i, rec in enumerate(pool.map(_build_one, jobs, chunksize=4), 1):
            errors += "error" in rec
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if i % 100 == 0:
                print(f"  {i}/{len(jobs)}", flush=True)
    print(f"{len(jobs)} judgments -> {args.out} ({errors} errors)")


if __name__ == "__main__":
    main()
