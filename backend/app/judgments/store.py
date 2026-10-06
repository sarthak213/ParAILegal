"""Supreme Court judgments: the offline store and its search.

Built by scripts/build_judgments_index.py from data/judgments/<year>.jsonl into data/judgments/index:

    judgments.sqlite   judgments  one row per judgment: the fields a result list shows, and the whole
                                  record (headnote, paragraphs, cases) zlib-compressed
                       cites      (ref, rowid, count): which judgments cite a provision, for the Case
                                  Builder ("judgments on IPC 498A")
                       jfts       contentless FTS5 over title, catchline, held, body
    vectors.npz        one bge-small vector per window of catchline + held (float16), with its owner

A judgment's text has three parts that matter for search: the catchline (the Reports' index line:
topic, facts and question in a few dashes), the holdings ("HELD: 1.1 ...", or "Held, that ..." in
older Reports) and the judgment itself. Holdings come from the Court's Editorial Section; they are
what a precedent stands for, so they are what the Case Builder quotes.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

INDEX_DIR = Path(__file__).resolve().parents[2] / "data" / "judgments" / "index"
CITATOR = Path(__file__).resolve().parents[2] / "data" / "judgments" / "citator.json"
RRF_K = 60
AUTHORITY = 0.08  # weight of how often later judgments cite a judgment (JudgmentStore.search)

# ── Parts of the headnote ─────────────────────────────────────────────────────────────────────

# where the headnote's holdings end: the case details that follow them
_DETAILS = re.compile(r"\b(?:CIVIL|CRIMINAL|ORIGINAL|APPELLATE|ADVISORY|EXTRAORDINARY) (?:APPELLATE )?JURISDICTION\b|"
                      r"\bCase Law Reference\b|\bAPPEAL (?:from|by special leave)\b|\bPETITION under\b|"
                      r"\bList of [Cc]itations\b")
_HELD = re.compile(r"\bthe Court\s+HELD\s*:|\bHELD\s*:|\bHeld(?:\s+(?:further|also))?\s*[,:]\s*(?:that\b)?")
_POINT = re.compile(r"(?:(?<=\s)|^)(\d{1,2}\.\d{1,2}\.?|\d{1,2}\.)\s+(?=[A-Z])")  # "1.1", "1.2." (older scans), "2."
# "[Paras 8 and 9][1028-H; 1029-A-D]"; short, since a scan may print "[Para 9)" and never close it
_PINPOINT = re.compile(r"\[(Paras? [^\]\[()]{1,40})[\])](?:\s*\[[^\]\[]{0,40}[\])])?")
_BENCH = re.compile(r"[\[(][A-Z][A-Za-z .,]+(?:C\.\s?J\.?|JJ?\.)\s*[\])]")


def catchline(record: dict) -> str:
    """The Reports' index line: the metadata's summary, or the headnote up to the first holding."""
    if record.get("summary"):
        return record["summary"]
    head = record.get("headnote") or ""
    if m := _BENCH.search(head[:600]):
        head = head[m.end():]
    m = _HELD.search(head)
    return " ".join(head[:m.start() if m else 1200].split()[:180])


def holdings(record: dict, limit: int = 8) -> list[dict]:
    """What the Court held, as the Editorial Section put it: [{"text", "paras"}], "paras" the
    judgment's paragraphs it rests on ("Paras 8 and 9"), when the headnote gives them."""
    head = record.get("headnote") or ""
    starts = list(_HELD.finditer(head))
    if not starts:
        return []
    # the numbered "the Court HELD:" block when there is one; else every "Held, that ..." phrase
    block = next((m for m in starts if "HELD" in m.group(0)), None)
    end = _DETAILS.search(head, (block or starts[0]).end())
    stop = end.start() if end else len(head)
    if block:
        text = head[block.end():stop]
        cuts = [m.start() for m in _POINT.finditer(text)] or [0]
        pieces = [text[a:b] for a, b in zip([0] + cuts, cuts + [len(text)], strict=False) if text[a:b].strip()]
        pieces = [_POINT.sub("", p, count=1) if _POINT.match(p.strip()) else p for p in pieces]
    else:
        pieces = [head[m.end():(starts[i + 1].start() if i + 1 < len(starts) else stop)]
                  for i, m in enumerate(starts) if m.start() < stop]
        pieces = [p.split(" – ")[0] if " – " in p and len(p) > 600 else p for p in pieces]
    out = []
    for p in pieces:
        paras = ", ".join(_PINPOINT.findall(p))
        text = " ".join(_PINPOINT.sub("", p).split()).strip(" –-")
        if len(text) > 30:
            out.append({"text": text, "paras": paras})
    return out[:limit]


def citation(record: dict) -> str:
    """"[2023] 10 SCR 1001 : 2023 INSC 600"."""
    return " : ".join(c for c in (record.get("scr", "").replace("S.C.R.", "SCR"), record.get("insc", "")) if c)


# ── Passages ──────────────────────────────────────────────────────────────────────────────────

_TERM = re.compile(r"[a-z]{3,}|\d+[a-z]?")
_STOP = set("the and for that this with which was were are has have had not but from into its his her their they "
            "been any all can may shall should would could under said such than then there these those also only "
            "upon other who whom what when where whether".split())


def terms(text: str) -> list[str]:
    return [t for t in _TERM.findall(text.lower()) if t not in _STOP]


def passages(record: dict, query: str, n: int = 2, words: int = 120) -> list[dict]:
    """The judgment's paragraphs (or pages) that best match the query, BM25 within the judgment:
    [{"n", "page", "text"}], the text cut to `words` around the densest match."""
    paras = [p for p in record.get("paragraphs") or [] if p.get("text")]
    q = set(terms(query))
    if not paras or not q:
        return []
    docs = [Counter(terms(p["text"])) for p in paras]
    avg = sum(sum(d.values()) for d in docs) / len(docs) or 1
    df = Counter(t for d in docs for t in q if t in d)
    scores = []
    for p, d in zip(paras, docs, strict=True):
        length = sum(d.values()) or 1
        s = sum(math.log(1 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5)) * d[t] * 2.2 /
                (d[t] + 1.2 * (0.25 + 0.75 * length / avg)) for t in q if t in d)
        scores.append(s)
    best = sorted(range(len(paras)), key=lambda i: -scores[i])[:n]
    return [{"n": paras[i]["n"], "page": bool(paras[i].get("page")), "text": _window(paras[i]["text"], q, words)}
            for i in sorted(best) if scores[i] > 0]


def _window(text: str, q: set[str], size: int) -> str:
    w = text.split()
    if len(w) <= size:
        return text
    hits = [i for i, x in enumerate(w) if re.sub(r"\W", "", x.lower()) in q]
    if not hits:
        return " ".join(w[:size]) + " …"
    # the start whose window holds the most matching words
    start = max(range(0, len(w) - size + 1, 10), key=lambda s: sum(s <= h < s + size for h in hits))
    return ("… " if start else "") + " ".join(w[start:start + size]) + (" …" if start + size < len(w) else "")


# ── The store ─────────────────────────────────────────────────────────────────────────────────

@dataclass
class Hit:
    id: str
    title: str
    citation: str
    decided: str
    bench_size: str
    catchline: str
    score: float
    matched_refs: list[str] = field(default_factory=list)
    status: dict = field(default_factory=dict)  # citator: cited_by, followed_by, overruled_noted_in, ...

    def to_dict(self) -> dict:
        return self.__dict__.copy()


SCHEMA = """
CREATE TABLE judgments (rowid INTEGER PRIMARY KEY, id TEXT UNIQUE, title TEXT, citation TEXT, decided TEXT,
                        bench_size TEXT, catchline TEXT, record BLOB);
CREATE TABLE cites (ref TEXT, rowid INTEGER, count INTEGER);
CREATE INDEX cites_ref ON cites(ref);
CREATE VIRTUAL TABLE jfts USING fts5(title, catch, held, body, content='', tokenize='porter unicode61 remove_diacritics 2');
"""
FTS_WEIGHTS = (2.0, 3.0, 3.0, 1.0)  # title, catchline, holdings, judgment text


def embed_text(record: dict) -> str:
    """What the dense index reads: catchline and holdings (about 600 words at most)."""
    held = " ".join(h["text"] for h in holdings(record))
    return " ".join(f"{record.get('title', '')}. {catchline(record)} {held}".split()[:600])


class JudgmentStore:
    def __init__(self, db: sqlite3.Connection, dense: Any = None, citator: dict | None = None) -> None:
        self.db = db
        self.dense = dense  # DenseIndex over embed_text, owner = rowid - 1
        self.status = (citator or {}).get("status", {})
        self.size = db.execute("SELECT COUNT(*) FROM judgments").fetchone()[0]

    # building

    @staticmethod
    def build(records: list[dict], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.unlink(missing_ok=True)
        db = sqlite3.connect(tmp)
        db.executescript(SCHEMA)
        for rowid, r in enumerate(records, 1):
            catch = catchline(r)
            held = " ".join(h["text"] for h in holdings(r))
            db.execute("INSERT INTO judgments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (rowid, r["id"], r.get("title", ""), citation(r), r.get("decided", ""), r.get("bench_size", ""),
                        catch, zlib.compress(json.dumps(r, ensure_ascii=False).encode("utf-8"), 6)))
            db.executemany("INSERT INTO cites VALUES (?, ?, ?)",
                           [(p["ref"], rowid, p["count"]) for p in r.get("provisions") or []])
            body = " ".join(p["text"] for p in r.get("paragraphs") or [])
            db.execute("INSERT INTO jfts(rowid, title, catch, held, body) VALUES (?, ?, ?, ?, ?)",
                       (rowid, r.get("title", ""), catch, held, body))
        db.commit()
        db.execute("VACUUM")
        db.close()
        tmp.replace(path)

    @classmethod
    def open(cls, directory: Path = INDEX_DIR, dense_key: str | None = "bge-small",
             citator_path: Path = CITATOR) -> JudgmentStore | None:
        db_path = directory / "judgments.sqlite"
        if not db_path.exists():
            return None
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
        dense = None
        vec_path = directory / "vectors.npz"
        if dense_key and vec_path.exists():
            from app.search.dense import DenseIndex

            with np.load(vec_path) as v:
                dense = DenseIndex.from_vectors(dense_key, v["vectors"], v["owner"], int(v["n_docs"]))
        citator = json.loads(citator_path.read_text(encoding="utf-8")) if citator_path.exists() else None
        return cls(db, dense, citator)

    # reading

    def record(self, judgment_id: str) -> dict | None:
        row = self.db.execute("SELECT record FROM judgments WHERE id = ?", (judgment_id,)).fetchone()
        return json.loads(zlib.decompress(row[0])) if row else None

    def _rows(self, rowids: list[int]) -> dict[int, tuple]:
        if not rowids:
            return {}
        marks = ",".join("?" * len(rowids))
        rows = self.db.execute(f"SELECT rowid, id, title, citation, decided, bench_size, catchline FROM judgments "
                               f"WHERE rowid IN ({marks})", rowids).fetchall()
        return {r[0]: r for r in rows}

    # searching

    def _keyword(self, query: str, limit: int) -> list[int]:
        words = list(dict.fromkeys(terms(query)))[:40]
        if not words:
            return []
        match = " OR ".join(f'"{w}"' for w in words)
        rows = self.db.execute("SELECT rowid FROM jfts WHERE jfts MATCH ? ORDER BY bm25(jfts, ?, ?, ?, ?) LIMIT ?",
                               (match, *FTS_WEIGHTS, limit)).fetchall()
        return [r[0] for r in rows]

    def _meaning(self, query: str, limit: int) -> list[int]:
        if self.dense is None:
            return []
        return [i + 1 for i, _ in self.dense(query, limit)]

    def _citing(self, refs: list[str], limit: int) -> tuple[list[int], dict[int, list[str]]]:
        """Judgments citing any of the refs, those citing more of them (and more often) first."""
        if not refs:
            return [], {}
        marks = ",".join("?" * len(refs))
        rows = self.db.execute(f"SELECT rowid, ref, count FROM cites WHERE ref IN ({marks})", refs).fetchall()
        score: Counter = Counter()
        matched: dict[int, list[str]] = {}
        for rowid, ref, count in rows:
            score[rowid] += 1 + min(count, 10) / 10
            matched.setdefault(rowid, []).append(ref)
        return [r for r, _ in score.most_common(limit)], matched

    def search(self, query: str, k: int = 10, refs: list[str] | None = None, before: str | None = None,
               pool: int = 200) -> list[Hit]:
        """Keyword, meaning and (for the Case Builder) the provisions the facts engage, fused by
        reciprocal rank. A judgment noted as overruled stays in the list, ranked lower and marked."""
        lists = [self._keyword(query, pool), self._meaning(query, pool)]
        citing, matched = self._citing(refs or [], pool)
        lists.append(citing)
        fused: Counter = Counter()
        for ranked in lists:
            for rank, rowid in enumerate(ranked):
                fused[rowid] += 1 / (RRF_K + rank + 1)
        rows = self._rows(list(fused))
        hits = []
        for rowid, score in fused.most_common():
            r = rows.get(rowid)
            if r is None or (before and r[4] and r[4] > before):
                continue
            status = self.status.get(r[1], {})
            # authority: a judgment later judgments keep citing (Maneka Gandhi, 224 times) is the one a
            # lawyer needs; a moderate lift, so relevance still leads (x1.2 at 10 citations, x1.4 at 200)
            score *= 1 + AUTHORITY * math.log1p(len(status.get("cited_by", [])))
            if status.get("overruled_noted_in"):
                score *= 0.5
            hits.append(Hit(r[1], r[2], r[3], r[4], r[5], r[6], score, sorted(matched.get(rowid, [])), status))
        hits.sort(key=lambda h: -h.score)
        return hits[:k]
