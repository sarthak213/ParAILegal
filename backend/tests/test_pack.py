import os
from types import SimpleNamespace

import numpy as np

from app.search import pack
from app.search.bm25 import BM25Index
from app.search.engine import SearchEngine


def chunk(i, section, title, text, domain="statutes"):
    return {"chunk_id": f"bns::{section}::1", "section": section, "section_title": title,
            "citation": f"Section {section} — {title}", "text": text, "full_text": text,
            "_embed_text": f"Section {section} — {title}\n{text}", "_domain": domain, "_act": "BNS",
            "act_code": "BNS", "document_title": "The Bharatiya Nyaya Sanhita, 2023", "status": "in force"}


CHUNKS = [chunk(0, "103", "Punishment for murder", "Whoever commits murder shall be punished with death."),
          chunk(1, "318", "Cheating", "Whoever cheats shall be punished with imprisonment up to three years."),
          chunk(2, "303", "Theft", "Whoever commits theft shall be punished with imprisonment.")]


def settings(tmp_path):
    src = tmp_path / "bns.jsonl"
    src.write_text("{}", encoding="utf-8")
    return SimpleNamespace(CONSTITUTION_FILE=tmp_path / "none.jsonl", STATUTE_FILES=[src],
                           JUDGEMENT_FILES=[], PACK_DIR=tmp_path / "pack"), src


class Dense:  # stands in for DenseIndex: what pack.write reads
    def __init__(self, n):
        self.vectors = np.eye(n, 4, dtype=np.float32)
        self.owner = np.arange(n)


def test_compact_and_expand_round_trip():
    odd = dict(CHUNKS[0], _embed_text="Article 2A\nPart I\ntext", full_text="longer text")
    for c in (CHUNKS[0], odd):
        small = pack.compact(c)
        assert pack.expand(dict(small)) == c
    assert "full_text" not in pack.compact(CHUNKS[0]) and "_embed_text" not in pack.compact(CHUNKS[0])


def test_write_then_load_gives_the_same_search(tmp_path):
    s, _ = settings(tmp_path)
    built = SearchEngine([dict(c) for c in CHUNKS])
    built.dense = Dense(len(CHUNKS))
    pack.write(s.PACK_DIR, s, built, "bge-small")

    loaded = pack.load(s.PACK_DIR, s, "bge-small")
    assert loaded is not None and loaded.chunks == CHUNKS
    assert np.array_equal(loaded.owner, np.arange(3)) and loaded.vectors.dtype == np.float32
    opened = BM25Index.open(str(loaded.db_path), loaded.vocabulary)
    assert opened.search(["cheats"]) == built.bm25.search(["cheats"])
    assert opened.vocabulary == built.bm25.vocabulary


def test_stale_pack_is_not_loaded(tmp_path):
    s, src = settings(tmp_path)
    pack.write(s.PACK_DIR, s, SearchEngine([dict(c) for c in CHUNKS]), None)
    assert pack.load(s.PACK_DIR, s, None) is not None
    st = src.stat()
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))  # the corpus changed
    assert pack.load(s.PACK_DIR, s, None) is None


def test_pack_without_sources_is_trusted(tmp_path):
    s, src = settings(tmp_path)
    pack.write(s.PACK_DIR, s, SearchEngine([dict(c) for c in CHUNKS]), None)
    src.unlink()  # a desktop install ships the pack, not the JSONL
    assert pack.load(s.PACK_DIR, s, None) is not None


def test_vectors_must_belong_to_the_index(tmp_path):
    s, _ = settings(tmp_path)
    pack.write(s.PACK_DIR, s, SearchEngine([dict(c) for c in CHUNKS]), None)
    assert pack.load(s.PACK_DIR, s, "bge-small") is None  # this index was written without vectors
