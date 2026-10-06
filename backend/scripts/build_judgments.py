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

from rapidfuzz import fuzz

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
_JUDGMENT_START = re.compile(r"^\s*(?:JUDGMENT|J U D G M E N T|ORDER)\b|(?:The Judgments? of the Court (?:was|were) delivered by|"
                             r"The following Judgments? (?:of the Court )?(?:was|were) delivered)"
                             # older reports: the judgment opens with its author, "KANIA C. J.-This is an appeal",
                             # or each opinion does, "SIKRI, C.J. I have had the advantage of reading"
                             r"|^[A-Z][A-Z. ]{2,40}\b,?\s*(?:C\.\s?J\.|J\.)(?:\s*[-—–:]|\s+(?=[A-Z][a-z]|I\s|\d{1,3}\.\s|[A-Z]\s?[A-Z]))", re.M)
# PDFium marks a hyphen the layout added at a line end ("struc￾ture") with U+FFFE; the word is one word
_LINE_HYPHEN = re.compile("[￾­]\\s*")
# a page header of the printed Reports, with its page number: "16 SUPREME COURT REPORTS [1950]",
# "S.C.R. SUPREME COURT REPORTS 17"; kept as a marker so older judgments (no numbered
# paragraphs) can be cited by page, as they are in practice: [1950] SCR 15, 17
_PAGE_HEADER = re.compile(r"^(?:(\d{1,4})\s+SUPREME COURT REPORTS\b.*|S\.\s?C\.\s?R\.\s+SUPREME COURT REPORTS\s+(\d{1,4}))$")
PAGE_MARK = "\x0cPAGE {}"
_PAGE_MARK = re.compile(r"^\x0cPAGE (\d+)$", re.M)
_MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"}


def _margin_noise(line: str, title_words: set[str]) -> bool:
    """Older Reports print the short title, year, date and author down the margin, and the scan
    reads them as lines of their own ("Ram Krisltna", "1950", "March 14", "Kania C.J."). A short
    line made up of such words, at least one of them a title word or a month, is dropped."""
    words = re.findall(r"[A-Za-z]{2,}|\d+", line)
    if not words or len(line.split()) > 4 or re.match(r"\d{1,3}\.\s", line):
        return False  # not a paragraph's first line ("19. Paragraph 298")

    def named(w: str) -> bool:  # a title word (as the scan misreads it) or a month
        lw = w.lower()
        return lw in _MONTHS or lw in title_words or any(
            fuzz.ratio(lw, t) >= 80 for t in title_words if abs(len(t) - len(lw)) <= 2)

    filler = {"v", "vs", "and", "of", "the", "others", "ors", "anr", "cj"}
    hits = [w.isdigit() or w.lower() in filler or named(w) for w in words]
    return any(named(w) for w in words if not w.isdigit()) and sum(hits) >= 0.6 * len(words)


def clean(text: str, title: str = "") -> str:
    text = _LIGATURE_GAP.sub(r"\1", unicodedata.normalize("NFKC", _LINE_HYPHEN.sub("", text)))
    title_words = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", title)}
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if page := _PAGE_HEADER.match(s):
            lines.append(PAGE_MARK.format(page.group(1) or page.group(2)))
            continue
        if _MARGIN.match(s) or _RUNNING.match(s) or (title_words and _margin_noise(s, title_words)):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines)


def split_parts(text: str) -> tuple[str, str]:
    """(headnote and case details, judgment body)."""
    m = _JUDGMENT_START.search(text)
    return (text[:m.start()], text[m.start():]) if m else ("", text)


_PARA = re.compile(r"^\s*(\d{1,3})\.\s*(?=[A-Z“\"(\[])", re.M)  # "10.While": the space is sometimes lost


def paragraphs(body: str) -> list[dict]:
    """Numbered paragraphs ("12. The appellant..."), in order; numbers must rise by one, so a
    numbered list inside a paragraph stays in it. The first may be 2 or 3 ("Delay condoned.
    2. Leave granted."); text before it is paragraph 0."""
    pages = _PAGE_MARK.findall(body)
    body = _PAGE_MARK.sub("", body)
    marks, last = [], 0
    found = [(int(m.group(1)), m.start(), m.end()) for m in _PARA.finditer(body)]
    for i, (n, start, end) in enumerate(found):
        # one number the scan lost is skipped over when the number after it follows
        skipped = n == last + 2 and any(m[0] == n + 1 for m in found[i + 1:i + 4])
        if n == last + 1 or skipped or (last == 0 and n <= 3):
            marks.append((n, start, end))
            last = n
    if pages and (not marks or (len(marks) < 3 and len(body.split()) > 1500)):
        return []  # unnumbered (older Reports; a stray "1." in a quote aside): cited by page, see by_page
    if not marks:
        return [{"n": 0, "text": " ".join(body.split())}]
    out = []
    if body[:marks[0][1]].strip():
        out.append({"n": 0, "text": " ".join(body[:marks[0][1]].split())})
    for i, (n, start, end) in enumerate(marks):
        stop = marks[i + 1][1] if i + 1 < len(marks) else len(body)
        out.append({"n": n, "text": " ".join(body[end:stop].split())})
    return out


def by_page(body: str) -> list[dict]:
    """An unnumbered judgment as the pages of the Reports it is printed on, {"n": page, "page": True}:
    older judgments are cited by page ("[1950] SCR 15, at 17"). Text before the first page
    header continues the page the headnote began on."""
    out: list[dict] = []
    pieces = _PAGE_MARK.split(body)  # text, page, text, page, text ...
    first = int(pieces[1]) - 1 if len(pieces) > 1 else 0
    for n, text in [(first, pieces[0])] + [(int(pieces[i]), pieces[i + 1]) for i in range(1, len(pieces), 2)]:
        text = " ".join(text.split())
        if text:
            out.append({"n": n, "text": text, "page": True})
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
_PROVISION = re.compile(rf"{_SEC}(?P<nums>{_NUMS})\s*,?\s*(?:of\s+(?:the\s+)?)?(?P<act>{'|'.join(f'(?:{v})' for v in ACTS.values())})")
_ARTICLE = re.compile(rf"\b(?:Art(?:icle)?s?\.?)\s*(?P<nums>{_NUM}(?:\s*(?:,|and|&)\s*{_NUM})*)(?:\s+of\s+the\s+Constitution)?")


def _act_code(name: str) -> str | None:
    for code, pat in ACTS.items():
        if re.fullmatch(pat, name.strip()):
            return code
    return None


CODE_OF = {"CPC": "code_of_civil_procedure_1908"}  # corpus act codes that differ from the short name
OLD_CRPC_UNTIL = "1974-04-01"  # the 1973 Code came into force; before it, "Cr.P.C." is the 1898 Code


def _section(n: str) -> str:
    return re.sub(r"\(.*", "", n).replace("-", "").replace(" ", "").upper()


def provisions(text: str, decided: str = "", index: str = "", acts: ActNames | None = None) -> list[dict]:
    """Provisions the judgment cites, as corpus refs, most cited first: the big codes and the
    Constitution from the text, and any Act in the headnote's index line ("Indian Forest Act,
    1927: s. 4", "Central Provinces Municipalities Act (II of 1922), s. 66") when `acts` can
    name it. "Cr.P.C." before 1 April 1974 is the 1898 Code, which the corpus does not hold."""
    found: Counter = Counter()
    for m in _PROVISION.finditer(text):
        act = _act_code(m["act"])
        if not act:
            continue
        if act == "CRPC" and decided and decided < OLD_CRPC_UNTIL:
            act = "CRPC_1898"
        act = CODE_OF.get(act, act)
        for n in re.findall(_NUM, m["nums"]):
            found[f"{act} {_section(n)}"] += 1
    for m in _ARTICLE.finditer(text):
        for n in re.findall(_NUM, m["nums"]):
            found["ART " + _section(n)] += 1
    if index and acts:
        for m in _INDEXED.finditer(index):
            code = acts.code(m["name"], m["year"])  # the index gives the year: no 1898/1973 guess needed
            for n in re.findall(_NUM, m["nums"]) if code else []:
                found[f"{code} {_section(n)}"] = max(found[f"{code} {_section(n)}"], 1)
    return [{"ref": r, "count": c} for r, c in found.most_common()]


# the headnote's index of statutes: "Penal Code, 1860 – ss. 34, 302", "Forest Act, 1927: s. 4",
# "Central Excises and Salt Act (I of 1944), ss. 2, 3"
_INDEXED = re.compile(
    r"(?P<name>(?:[A-Z][\w'’&.-]*\s+(?:(?:and|of|for|the|in|on|to|&)\s+)*){0,10}?(?:Act|Code|Ordinance|Sanhita|Adhiniyam))"
    r"\s*(?:,\s*|\(\s*[IVXLC\d]+\s+of\s+|\s)(?P<year>1[789]\d\d|20\d\d)\)?\s*[,:–—-]?\s*"
    rf"(?:{_SEC}|S(?=\d))(?P<nums>{_NUMS})")  # older scans: "Central Sales Tax Act 1956 S2(h)"


class ActNames:
    """Act name and year as judgments write them -> the corpus act code (data/statutes file names:
    "indian_forest_act_1927"; the big codes by their short names)."""

    SHORT = {"ipc": "IPC", "crpc": "CRPC", "iea": "IEA", "bns": "BNS", "bnss": "BNSS", "bsa": "BSA"}
    ALIASES = {("penal code", "1860"): "IPC", ("indian penal code", "1860"): "IPC",
               ("code of criminal procedure", "1973"): "CRPC", ("evidence act", "1872"): "IEA",
               ("indian evidence act", "1872"): "IEA", ("code of criminal procedure", "1898"): "CRPC_1898"}

    def __init__(self, codes: list[str]) -> None:
        self.codes = [self.SHORT.get(c, c) for c in codes]
        self.by_year: dict[str, list[str]] = {}
        for c in self.codes:
            if m := re.search(r"_(\d{4})$", c):
                self.by_year.setdefault(m.group(1), []).append(c)

    @classmethod
    def from_dir(cls, directory: Path) -> ActNames:
        return cls([f.stem for f in directory.glob("*.jsonl") if not f.stem.startswith("_")])

    def code(self, name: str, year: str) -> str | None:
        name = re.sub(r"^(?:the|of|and|under|with|under the)\s+", "", " ".join(name.split()), flags=re.I)
        key = (name.lower(), year)
        if key in self.ALIASES:
            return self.ALIASES[key]
        candidates = self.by_year.get(year, [])
        words = name.split()
        # the name may carry a stray word in front ("Price' Forest Act"): try it shorter too
        for start in range(max(1, len(words) - 1)):
            slug = re.sub(r"[^a-z0-9]+", "_", " ".join(words[start:]).lower()).strip("_")
            for c in (f"{slug}_{year}", f"indian_{slug}_{year}"):
                if c in candidates:
                    return c
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        best = max(candidates, key=lambda c: fuzz.ratio(c, f"{slug}_{year}"), default=None)
        return best if best and fuzz.ratio(best, f"{slug}_{year}") >= 92 else None


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
    from scripts.pdf_text import fast_text, pdf_text

    meta = parse_metadata(json.loads(meta_path.read_text(encoding="utf-8"))) if meta_path.exists() else {"id": pdf.stem}
    try:
        raw = fast_text(pdf)
    except Exception:  # a PDF PDFium cannot open: the slower readers often can
        raw = pdf_text(pdf)
    text = clean(raw, meta.get("title", ""))
    front, body = split_parts(text)
    paras = paragraphs(body) or by_page(body)
    own = {c for c in (meta.get("scr", "").replace("S.C.R.", "SCR"), meta.get("insc", "")) if c}
    headnote = " ".join(_PAGE_MARK.sub("", front).split())
    # the headnote lists the cases it relies on too ("Boddu Paidanna (1942) F.C.R. 90 referred to.")
    cited = cases([{"n": 0, "text": headnote}] + paras, own)
    return {**meta, "headnote": headnote, "paragraphs": paras,
            "provisions": provisions(body, meta.get("decided", ""), headnote, _acts()),
            "cases": cited,
            "words": sum(len(p["text"].split()) for p in paras)}


_ACT_NAMES: ActNames | None = None
STATUTES = Path(__file__).resolve().parents[1] / "data" / "statutes"


def _acts() -> ActNames:
    global _ACT_NAMES
    if _ACT_NAMES is None:
        _ACT_NAMES = ActNames.from_dir(STATUTES)
    return _ACT_NAMES


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
