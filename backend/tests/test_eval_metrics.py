"""Unit tests for the retrieval evaluation metrics (offline, no server needed)."""

import math

from eval.retrieval import ref_of, score_question


def test_perfect_ranking():
    m = score_question({"BNS 103": 2, "BNS 101": 1}, ["BNS 103", "BNS 101", "BNS 1"])
    assert m["hit@5"] and m["mrr@10"] == 1.0
    assert m["recall@5"] == 1.0
    assert math.isclose(m["ndcg@10"], 1.0)


def test_essential_at_rank_three_and_duplicates_count_once():
    m = score_question({"BNS 103": 2}, ["BNS 1", "BNS 1", "BNS 2", "BNS 103"])
    assert m["first_essential_rank"] == 3
    assert math.isclose(m["mrr@10"], 1 / 3)
    assert 0 < m["ndcg@10"] < 1


def test_supporting_only_is_not_a_hit():
    m = score_question({"BNS 103": 2, "BNS 101": 1}, ["BNS 101"])
    assert not m["hit@10"] and m["recall@10"] == 0.5 and m["mrr@10"] == 0


def test_miss():
    m = score_question({"ART 21": 2}, ["ART 22", None])
    assert m["ndcg@10"] == 0 and m["recall@10"] == 0


def test_ref_of_maps_every_source():
    assert ref_of({"source_type": "statutes", "chunk_id": "bnss_482_1", "section": "482"}) == "BNSS 482"
    assert ref_of({"source_type": "statutes", "chunk_id": "bns_103_Section103_2", "section": "103"}) == "BNS 103"
    assert ref_of({"source_type": "constitution", "section": "21A"}) == "ART 21A"
    assert ref_of({"source_type": "judgements", "chunk_id": "maneka_gandhi_ratio_0"}) == "CASE maneka_gandhi"
    assert ref_of({"source_type": "statutes", "chunk_id": "x", "section": ""}) is None
