"""Unit tests for ScienceDirect retrieval corpus and benchmark evaluation suite."""
import json
from pathlib import Path
import sqlite3
import pytest

ROOT = Path(__file__).resolve().parents[1]
CORPUS_JSONL = ROOT / "datasets/sciencedirect/retrieval/corpus_chunks.jsonl"
CORPUS_SQLITE = ROOT / "datasets/sciencedirect/retrieval/corpus.sqlite"
CORPUS_MANIFEST = ROOT / "datasets/sciencedirect/retrieval/manifest.json"
BENCHMARK_QUERIES = ROOT / "datasets/sciencedirect/benchmark/queries.jsonl"
BENCHMARK_QRELS = ROOT / "datasets/sciencedirect/benchmark/qrels.jsonl"
BENCHMARK_TSV = ROOT / "datasets/sciencedirect/benchmark/qrels.tsv"
BENCHMARK_MANIFEST = ROOT / "datasets/sciencedirect/benchmark/manifest.json"


def test_retrieval_corpus_files_exist():
    assert CORPUS_JSONL.is_file(), "corpus_chunks.jsonl must exist"
    assert CORPUS_SQLITE.is_file(), "corpus.sqlite must exist"
    assert CORPUS_MANIFEST.is_file(), "manifest.json must exist"


def test_retrieval_corpus_manifest_and_counts():
    manifest = json.loads(CORPUS_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["total_papers"] == 306
    assert manifest["total_chunks"] >= 20000
    assert manifest["target_window_tokens"] == 400
    assert manifest["window_overlap_tokens"] == 60

    # Ensure all 5 topics are well represented
    topics = manifest["chunks_per_topic"]
    assert len(topics) == 5
    for topic, count in topics.items():
        assert count >= 2000, f"Topic {topic} has too few chunks: {count}"


def test_sqlite_corpus_queryable():
    with sqlite3.connect(CORPUS_SQLITE) as db:
        cnt = db.execute("SELECT count(*) FROM chunks").fetchone()[0]
        assert cnt >= 20000

        # Query a chunk by DOI
        row = db.execute("SELECT chunk_id, doi, document FROM chunks WHERE doi = '10.1016/j.aei.2026.105154' LIMIT 1").fetchone()
        assert row is not None
        chunk_data = json.loads(row[2])
        assert chunk_data["doi"] == "10.1016/j.aei.2026.105154"


def test_benchmark_dataset_structure():
    assert BENCHMARK_QUERIES.is_file()
    assert BENCHMARK_QRELS.is_file()
    assert BENCHMARK_TSV.is_file()
    assert BENCHMARK_MANIFEST.is_file()

    queries = [json.loads(l) for l in BENCHMARK_QUERIES.read_text(encoding="utf-8").splitlines() if l.strip()]
    qrels = [json.loads(l) for l in BENCHMARK_QRELS.read_text(encoding="utf-8").splitlines() if l.strip()]
    manifest = json.loads(BENCHMARK_MANIFEST.read_text(encoding="utf-8"))

    assert len(queries) == 125
    assert len(qrels) >= 500
    assert manifest["total_queries"] == 125

    # Exactly 25 queries per topic
    for topic, qcnt in manifest["queries_per_topic"].items():
        assert qcnt == 25, f"Topic {topic} does not have exactly 25 queries: {qcnt}"


def test_benchmark_referential_integrity():
    with sqlite3.connect(CORPUS_SQLITE) as db:
        qrels = [json.loads(l) for l in BENCHMARK_QRELS.read_text(encoding="utf-8").splitlines() if l.strip()]
        for qr in qrels:
            cid = qr["chunk_id"]
            row = db.execute("SELECT 1 FROM chunks WHERE chunk_id = ?", (cid,)).fetchone()
            assert row is not None, f"Chunk {cid} referenced in qrels not found in corpus!"

