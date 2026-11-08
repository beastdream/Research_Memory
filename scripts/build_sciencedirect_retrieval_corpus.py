#!/usr/bin/env python3
"""Build deterministic, section-aware retrieval corpus for ScienceDirect dataset.

Processes all 306 enriched ScienceDirect papers and produces:
- datasets/sciencedirect/retrieval/corpus_chunks.jsonl: Line-by-line chunks with metadata.
- datasets/sciencedirect/retrieval/corpus.sqlite: SQLite index for high-speed deterministic lookups.
- datasets/sciencedirect/retrieval/manifest.json: Statistical breakdown and token distributions.

Conforms strictly to section boundaries and cl100k_base tokenization windows (400 tokens, 60 overlap).
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ingestion.enrichment import digest
from app.retrieval_corpus import VERSION, build_chunks, tokenizer

ENRICHED_INPUT = ROOT / "datasets/sciencedirect/enriched/sciencedirect_enriched_v2.jsonl"
DEFAULT_OUTPUT_DIR = ROOT / "datasets/sciencedirect/retrieval"


def build_corpus(
    input_path: Path = ENRICHED_INPUT,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    size: int = 400,
    overlap: int = 60,
) -> dict:
    start_time = time.time()
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[Corpus] Loading enriched records from: {input_path}")
    lines = [line.strip() for line in input_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    papers = [json.loads(line) for line in lines]
    print(f"[Corpus] Loaded {len(papers)} papers. Initializing tokenizer...")

    enc = tokenizer()
    all_chunks: list[dict] = []
    seen_ids: set[str] = set()
    topics_counter: Counter = Counter()

    print(f"[Corpus] Chunking papers with window={size}, overlap={overlap}...")
    for idx, paper in enumerate(papers, 1):
        doi = paper.get("doi")
        topic_group = (paper.get("metadata") or {}).get("topic_group") or paper.get("topic_group") or "Unclassified"
        journal = (paper.get("publication") or {}).get("journal") or "Unknown"

        chunks = build_chunks(paper, enc=enc, size=size, overlap=overlap)
        for c in chunks:
            cid = c["chunk_id"]
            if cid in seen_ids:
                raise ValueError(f"Duplicate chunk ID detected: {cid}")
            seen_ids.add(cid)

            # Enrich chunk with science-specific fields
            c["doi"] = doi
            c["topic_group"] = topic_group
            c["journal"] = journal
            all_chunks.append(c)

        topics_counter[topic_group] += len(chunks)

        if idx % 50 == 0 or idx == len(papers):
            print(f"[{idx}/{len(papers)}] Processed {doi} -> Cumulative chunks: {len(all_chunks)}")

    print(f"[Corpus] Total chunks generated: {len(all_chunks)}")

    # 1. Write corpus_chunks.jsonl atomically
    jsonl_path = output_dir / "corpus_chunks.jsonl"
    tmp_jsonl = jsonl_path.with_suffix(".jsonl.tmp")
    with open(tmp_jsonl, "w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    tmp_jsonl.replace(jsonl_path)
    print(f"[Corpus] Saved JSONL to: {jsonl_path}")

    # 2. Build derived SQLite index for fast key-based lookups
    sqlite_path = output_dir / "corpus.sqlite"
    tmp_sqlite = output_dir / "corpus.build.sqlite"
    if tmp_sqlite.exists():
        tmp_sqlite.unlink()

    with sqlite3.connect(tmp_sqlite) as db:
        db.execute(
            """
            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY,
                paper_id TEXT,
                doi TEXT,
                topic_group TEXT,
                year INTEGER,
                section_id TEXT,
                section_title TEXT,
                content_type TEXT,
                token_count INTEGER,
                document TEXT
            )
            """
        )
        db.executemany(
            """
            INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    c["chunk_id"],
                    c["paper_id"],
                    c.get("doi"),
                    c.get("topic_group"),
                    c.get("year"),
                    c.get("section_id"),
                    c.get("section_title"),
                    c.get("content_type"),
                    c.get("token_count"),
                    json.dumps(c, ensure_ascii=False),
                )
                for c in all_chunks
            ],
        )
        for field in ("paper_id", "doi", "topic_group", "year", "content_type", "section_id"):
            db.execute(f"CREATE INDEX idx_chunks_{field} ON chunks({field})")

    db.close()
    tmp_sqlite.replace(sqlite_path)
    print(f"[Corpus] Saved SQLite index to: {sqlite_path}")

    # 3. Create manifest
    lengths = sorted(c["token_count"] for c in all_chunks)
    elapsed = round(time.time() - start_time, 2)
    manifest = {
        "dataset_name": "ScienceDirect LLM Retrieval Corpus",
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "build_duration_sec": elapsed,
        "chunker_version": VERSION,
        "tokenizer": enc.name,
        "target_window_tokens": size,
        "window_overlap_tokens": overlap,
        "total_papers": len(papers),
        "total_chunks": len(all_chunks),
        "chunks_per_topic": dict(topics_counter),
        "content_types": dict(Counter(c["content_type"] for c in all_chunks)),
        "token_distribution": {
            "min": lengths[0] if lengths else 0,
            "p50": lengths[len(lengths) // 2] if lengths else 0,
            "p95": lengths[int(len(lengths) * 0.95)] if lengths else 0,
            "max": lengths[-1] if lengths else 0,
        },
        "oversized_atomic_objects": sum(c.get("oversized_atomic", False) for c in all_chunks),
        "corpus_digest": digest(all_chunks),
    }

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[Corpus] Saved manifest to: {manifest_path}")

    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ENRICHED_INPUT, help="Enriched ScienceDirect JSONL file")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory for corpus")
    parser.add_argument("--size", type=int, default=400, help="Target chunk size in tokens")
    parser.add_argument("--overlap", type=int, default=60, help="Token overlap between consecutive chunks")
    args = parser.parse_args()

    manifest = build_corpus(args.input, args.output, size=args.size, overlap=args.overlap)
    print(f"\n[DONE] Built ScienceDirect retrieval corpus in {manifest['build_duration_sec']}s:")
    print(f"       - Papers: {manifest['total_papers']}")
    print(f"       - Total Chunks: {manifest['total_chunks']}")
    print(f"       - Breakdown by Topic: {manifest['chunks_per_topic']}")

