#!/usr/bin/env python3
"""Build scientifically grounded Information Retrieval (IR) benchmark for ScienceDirect.

Produces standard IR evaluation suites:
- datasets/sciencedirect/benchmark/queries.jsonl: Formulated scientific research queries.
- datasets/sciencedirect/benchmark/qrels.jsonl: Ground-truth chunk relevance judgments.
- datasets/sciencedirect/benchmark/qrels.tsv: Standard TREC/BEIR format (query_id, chunk_id, score).
- datasets/sciencedirect/benchmark/manifest.json: Benchmark balance & statistical metrics.

Relevance grading:
- Score 2 (Primary Evidence): Chunks from core technical body sections (Methods, Architecture, Experiments).
- Score 1 (Secondary Evidence): Abstract chunk or introductory background chunks.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SQLITE_CORPUS = ROOT / "datasets/sciencedirect/retrieval/corpus.sqlite"
BENCHMARK_DIR = ROOT / "datasets/sciencedirect/benchmark"

TOPICS = [
    "AI Agents and Long-Term Memory",
    "RAG and Information Retrieval",
    "Large Language Models",
    "Hallucination and Faithfulness",
    "Scientific NLP and QA",
]


def is_valid_english_paper(title: str, abstract: str) -> bool:
    """Filter out retracted, withdrawn or non-English papers."""
    if re.search(r"\b(withdrawn|corrigendum|erratum|retracted)\b", title, re.I):
        return False
    # Check ASCII / English ratio in title
    ascii_chars = sum(1 for c in title if ord(c) < 128)
    if len(title) > 0 and (ascii_chars / len(title)) < 0.75:
        return False
    # Avoid short or empty abstracts
    if not abstract or len(abstract.strip()) < 80:
        return False
    return True


def formulate_scientific_query(title: str, abstract: str) -> tuple[str, str]:
    """Formulate natural scientific question from paper title and abstract."""
    title_clean = re.sub(r"^(?:WITHDRAWN|CORRIGENDUM|ERRATUM)\s*:\s*", "", title, flags=re.I).strip()
    parts = re.split(r"\s*[:—]\s*", title_clean, maxsplit=1)

    if len(parts) == 2:
        prefix, main = parts[0].strip(), parts[1].strip()
        # If prefix is short acronym (<= 15 chars) like PTMR-ECM, RP-LLM
        if len(prefix) <= 15 and re.match(r"^[A-Z0-9\-\s]+$", prefix):
            query = f"How does the {prefix} framework address {main.lower()}?"
            qtype = "architectural_design"
        else:
            query = f"What are the methodology and key findings regarding {main.lower()}?"
            qtype = "methodology"
    else:
        t_clean = title_clean.strip()
        if re.search(r"\b(?:survey|review|overview)\b", t_clean, re.I):
            query = f"What is the comprehensive overview and state of the art in {t_clean.lower()}?"
            qtype = "literature_survey"
        elif re.search(r"\b(?:evaluation|empirical|benchmark)\b", t_clean, re.I):
            query = f"What are the empirical evaluation results and performance metrics for {t_clean.lower()}?"
            qtype = "empirical_evaluation"
        elif re.search(r"\b(?:framework|architecture|system|model)\b", t_clean, re.I):
            query = f"What architectural design and methodology are introduced for {t_clean.lower()}?"
            qtype = "architectural_design"
        elif re.search(r"\b(?:impact|effect|bias|influence)\b", t_clean, re.I):
            query = f"What is the observed impact and underlying mechanism in {t_clean.lower()}?"
            qtype = "comparative_analysis"
        else:
            query = f"How does scientific research investigate {t_clean.lower()}?"
            qtype = "methodology"

    # Normalize whitespace & capital
    query = re.sub(r"\s+", " ", query).strip()
    if not query.endswith("?"):
        query += "?"
    query = query[0].upper() + query[1:]
    return query, qtype


def build_benchmark(
    corpus_db_path: Path = SQLITE_CORPUS,
    output_dir: Path = BENCHMARK_DIR,
    queries_per_topic: int = 25,
) -> dict:
    corpus_db_path = Path(corpus_db_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not corpus_db_path.is_file():
        raise FileNotFoundError(f"Corpus SQLite index not found at: {corpus_db_path}")

    db = sqlite3.connect(corpus_db_path)
    queries: list[dict] = []
    qrels: list[dict] = []
    qrels_tsv_lines: list[str] = ["query_id\tchunk_id\tscore"]

    q_idx = 1
    selected_dois: set[str] = set()

    for topic in TOPICS:
        print(f"[Benchmark] Selecting papers for topic: {topic} (target={queries_per_topic})...")
        # Find candidate papers in this topic that have abstract and body chunks
        candidates = db.execute(
            """
            SELECT DISTINCT c.doi, json_extract(c.document, '$.title') AS title,
                   (SELECT json_extract(document, '$.text') FROM chunks WHERE doi = c.doi AND content_type = 'abstract' LIMIT 1) AS abstract
            FROM chunks c
            WHERE c.topic_group = ? AND c.content_type = 'text'
            ORDER BY c.doi ASC
            """,
            (topic,),
        ).fetchall()

        topic_count = 0
        for doi, title, abstract in candidates:
            if topic_count >= queries_per_topic:
                break
            if not doi or doi in selected_dois or not title or not abstract:
                continue
            if not is_valid_english_paper(title, abstract):
                continue

            query_text, qtype = formulate_scientific_query(title, abstract)
            qid = f"sci_query_{q_idx:03d}"

            # Fetch relevant chunks for this paper
            chunks = db.execute(
                """
                SELECT chunk_id, section_title, content_type
                FROM chunks
                WHERE doi = ?
                """,
                (doi,),
            ).fetchall()

            if not chunks:
                continue

            # Identify primary chunks (score=2) and secondary chunks (score=1)
            primary_chunks = []
            secondary_chunks = []

            for cid, stitle, ctype in chunks:
                stitle_lower = (stitle or "").lower()
                if ctype == "abstract":
                    secondary_chunks.append(cid)
                elif re.search(r"\b(method|methodology|proposed|model|architecture|framework|experiment|result|approach)\b", stitle_lower):
                    primary_chunks.append(cid)
                elif re.search(r"\b(introduction|background|literature)\b", stitle_lower):
                    secondary_chunks.append(cid)

            # Ensure we have at least 1 primary chunk
            if not primary_chunks:
                # Fallback to the first non-abstract text chunks
                text_chunks = [cid for cid, _, ctype in chunks if ctype == "text"]
                if text_chunks:
                    primary_chunks = text_chunks[:3]

            if not primary_chunks and not secondary_chunks:
                continue

            selected_dois.add(doi)
            topic_count += 1
            q_idx += 1

            queries.append({
                "query_id": qid,
                "query": query_text,
                "topic_group": topic,
                "query_type": qtype,
                "target_doi": doi,
                "target_title": title,
            })

            # Record qrels: score=2 for primary chunks (cap at top 5 to keep evaluation dense)
            for cid in primary_chunks[:5]:
                qrels.append({"query_id": qid, "chunk_id": cid, "score": 2})
                qrels_tsv_lines.append(f"{qid}\t{cid}\t2")

            # Record qrels: score=1 for secondary chunks (cap at top 2)
            for cid in secondary_chunks[:2]:
                qrels.append({"query_id": qid, "chunk_id": cid, "score": 1})
                qrels_tsv_lines.append(f"{qid}\t{cid}\t1")

    db.close()

    # Write queries.jsonl
    queries_path = output_dir / "queries.jsonl"
    with open(queries_path, "w", encoding="utf-8") as f:
        for q in queries:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    print(f"[Benchmark] Saved {len(queries)} queries to: {queries_path}")

    # Write qrels.jsonl
    qrels_path = output_dir / "qrels.jsonl"
    with open(qrels_path, "w", encoding="utf-8") as f:
        for qr in qrels:
            f.write(json.dumps(qr, ensure_ascii=False) + "\n")
    print(f"[Benchmark] Saved {len(qrels)} qrels to: {qrels_path}")

    # Write qrels.tsv
    qrels_tsv_path = output_dir / "qrels.tsv"
    qrels_tsv_path.write_text("\n".join(qrels_tsv_lines) + "\n", encoding="utf-8")
    print(f"[Benchmark] Saved TSV qrels to: {qrels_tsv_path}")

    # Compute manifest stats
    topic_dist = dict(Counter(q["topic_group"] for q in queries))
    type_dist = dict(Counter(q["query_type"] for q in queries))
    score_dist = dict(Counter(qr["score"] for qr in qrels))
    q_to_chunks = defaultdict(list)
    for qr in qrels:
        q_to_chunks[qr["query_id"]].append(qr["chunk_id"])

    manifest = {
        "benchmark_name": "ScienceDirect Multi-Topic IR Evaluation Benchmark",
        "total_queries": len(queries),
        "queries_per_topic": topic_dist,
        "queries_by_type": type_dist,
        "total_qrels": len(qrels),
        "qrels_by_score": {f"score_{k}": v for k, v in score_dist.items()},
        "avg_qrels_per_query": round(len(qrels) / len(queries), 2) if queries else 0,
        "unique_target_papers": len(selected_dois),
        "unique_target_chunks": len({qr["chunk_id"] for qr in qrels}),
        "corpus_reference": str(corpus_db_path),
    }

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[Benchmark] Saved manifest to: {manifest_path}")

    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=SQLITE_CORPUS, help="Path to corpus.sqlite")
    parser.add_argument("--output", type=Path, default=BENCHMARK_DIR, help="Output directory for benchmark")
    parser.add_argument("--count", type=int, default=25, help="Number of queries per topic group")
    args = parser.parse_args()

    m = build_benchmark(args.corpus, args.output, queries_per_topic=args.count)
    print("\n[DONE] Built ScienceDirect Benchmark Dataset:")
    print(f"       - Total Queries: {m['total_queries']}")
    print(f"       - Breakdown by Topic: {m['queries_per_topic']}")
    print(f"       - Total Qrels: {m['total_qrels']}")
    print(f"       - Avg Qrels / Query: {m['avg_qrels_per_query']}")

