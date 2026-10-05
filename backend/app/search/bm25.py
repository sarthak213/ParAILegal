"""BM25 keyword search over the corpus with SQLite FTS5 (built into Python, no server).

Each chunk is indexed in four fields, weighted at query time (BM25F-style):
    title    section / article / case name
    heading  citation line, act name and number ("section 103 bns bharatiya nyaya sanhita")
    body     the provision text
    aliases  old-code numbers ("ipc 302 indian penal code"), so old citations find new sections

The Porter stemmer matches "punishable" with "punishment"; tokens such as "498a" survive whole.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from dataclasses import dataclass

TOKENIZER = "porter unicode61 remove_diacritics 2"


@dataclass(frozen=True)
class FieldWeights:
    title: float = 3.0
    heading: float = 2.0
    body: float = 1.0
    aliases: float = 1.5


class BM25Index:
    def __init__(self, docs: list[dict[str, str]]) -> None:
        """docs: one dict per chunk with keys title, heading, body, aliases (row id = list position)."""
        self._db = sqlite3.connect(":memory:", check_same_thread=False)
        self._db.execute(
            f"CREATE VIRTUAL TABLE chunks USING fts5(title, heading, body, aliases, tokenize='{TOKENIZER}')"
        )
        self._db.executemany(
            "INSERT INTO chunks(rowid, title, heading, body, aliases) VALUES (?, ?, ?, ?, ?)",
            [(i, d.get("title", ""), d.get("heading", ""), d.get("body", ""), d.get("aliases", ""))
             for i, d in enumerate(docs)],
        )
        self._db.commit()
        self.vocabulary = self._vocabulary(docs)

    @staticmethod
    def _vocabulary(docs: list[dict[str, str]]) -> dict[str, int]:
        """Word -> count (words seen at least twice); the counts let typo correction prefer a
        common word over a rare one."""
        counts: Counter[str] = Counter()
        for d in docs:
            for field in ("title", "heading", "body"):
                counts.update(re.findall(r"[a-z]{4,}", d.get(field, "").lower()))
        return {w: n for w, n in counts.items() if n >= 2}

    def search(self, keywords: list[str], limit: int = 50,
               weights: FieldWeights = FieldWeights()) -> list[tuple[int, float]]:
        """(row id, score) best first. Keywords are ORed: BM25 rewards documents matching more of them."""
        terms = [re.sub(r'["*^:()]', "", k) for k in keywords]
        terms = [t for t in terms if t]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        rows = self._db.execute(
            "SELECT rowid, bm25(chunks, ?, ?, ?, ?) AS s FROM chunks WHERE chunks MATCH ? ORDER BY s LIMIT ?",
            (weights.title, weights.heading, weights.body, weights.aliases, match, limit),
        ).fetchall()
        return [(rowid, -score) for rowid, score in rows]  # FTS5 bm25() is lower-is-better
