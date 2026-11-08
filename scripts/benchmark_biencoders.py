#!/usr/bin/env python3
"""Benchmark Bi-Encoder retrieval models on ScienceDirect dataset.

Models evaluated:
1. BAAI/bge-base-en-v1.5
2. allenai/specter2_base
3. facebook/contriever

Evaluates IR retrieval quality against ground truth judgments (qrels):
- Recall@5
- Recall@10
- Recall@20
- MRR@10
- nDCG@10 (graded relevance: score 2 for technical sections, score 1 for abstracts)

Outputs:
- JSON report: reports/retrieval_benchmark_report.json
- Markdown report: reports/retrieval_benchmark_report.md
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import datetime
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("benchmark_biencoders")

DEFAULT_MODELS = [
    "BAAI/bge-base-en-v1.5",
    "allenai/specter2_base",
    "facebook/contriever",
]

DEFAULT_QUERIES = ROOT / "datasets/sciencedirect/benchmark/queries.jsonl"
DEFAULT_QRELS = ROOT / "datasets/sciencedirect/benchmark/qrels.jsonl"
DEFAULT_SQLITE_CORPUS = ROOT / "datasets/sciencedirect/retrieval/corpus.sqlite"
DEFAULT_JSONL_CORPUS = ROOT / "datasets/sciencedirect/retrieval/corpus_chunks.jsonl"
DEFAULT_REPORT_JSON = ROOT / "reports/retrieval_benchmark_report.json"
DEFAULT_REPORT_MD = ROOT / "reports/retrieval_benchmark_report.md"
DEFAULT_EMBEDDINGS_DIR = ROOT / "datasets/sciencedirect/benchmark/embeddings"


# ----------------------------------------------------------------------
# Metrics calculation (pure NumPy / Python)
# ----------------------------------------------------------------------
def compute_recall_at_k(
    ranked_chunk_ids: list[str],
    relevant_dict: dict[str, int],
    k: int,
) -> float:
    """Compute Recall@K = |Retrieved_K ∩ Relevant| / |Relevant|."""
    if not relevant_dict:
        return 0.0
    top_k = ranked_chunk_ids[:k]
    hits = sum(1 for cid in top_k if cid in relevant_dict)
    return hits / float(len(relevant_dict))


def compute_mrr_at_k(
    ranked_chunk_ids: list[str],
    relevant_dict: dict[str, int],
    k: int = 10,
) -> float:
    """Compute Mean Reciprocal Rank (MRR@K) = 1 / first_relevant_rank (if <= k, else 0)."""
    if not relevant_dict:
        return 0.0
    for rank_idx, cid in enumerate(ranked_chunk_ids[:k], start=1):
        if cid in relevant_dict:
            return 1.0 / float(rank_idx)
    return 0.0


def compute_ndcg_at_k(
    ranked_chunk_ids: list[str],
    relevant_dict: dict[str, int],
    k: int = 10,
) -> float:
    """Compute Normalized Discounted Cumulative Gain (nDCG@K) with graded relevance.

    Graded relevance:
        gain = 2^{rel_score} - 1
        discount = log2(rank + 1)
        DCG@K = sum(gain / discount)
    """
    if not relevant_dict:
        return 0.0

    # DCG@K
    dcg = 0.0
    for rank_idx, cid in enumerate(ranked_chunk_ids[:k], start=1):
        rel = relevant_dict.get(cid, 0)
        if rel > 0:
            gain = (2.0 ** rel) - 1.0
            discount = math.log2(rank_idx + 1)
            dcg += gain / discount

    # IDCG@K (Ideal DCG)
    ideal_scores = sorted(relevant_dict.values(), reverse=True)[:k]
    idcg = 0.0
    for rank_idx, rel in enumerate(ideal_scores, start=1):
        gain = (2.0 ** rel) - 1.0
        discount = math.log2(rank_idx + 1)
        idcg += gain / discount

    if idcg <= 0.0:
        return 0.0
    return dcg / idcg


# ----------------------------------------------------------------------
# Model Wrappers
# ----------------------------------------------------------------------
class BiEncoderModel:
    """Unified wrapper supporting SentenceTransformers and Transformers fallbacks."""

    def __init__(self, model_name: str, device: str = "cpu"):
        self.model_name = model_name
        self.device = device
        self.is_bge = "bge" in model_name.lower()
        self.bge_query_prefix = "Represent this sentence for searching relevant passages: "
        self._load_model()

    def _load_model(self):
        logger.info(f"Loading Bi-Encoder [{self.model_name}] on device [{self.device}]...")
        try:
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(self.model_name, device=self.device)
            self.backend = "sentence_transformers"
            logger.info(f"Loaded via SentenceTransformer: {self.model_name}")
        except Exception as exc:
            logger.info(f"Primary load failed ({exc}); attempting with HF_HUB_OFFLINE=1 for {self.model_name}...")
            import os
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            try:
                from sentence_transformers import SentenceTransformer
                self.model = SentenceTransformer(self.model_name, device=self.device)
                self.backend = "sentence_transformers"
                logger.info(f"Loaded via SentenceTransformer (offline cache): {self.model_name}")
            except Exception as exc2:
                logger.warning(f"SentenceTransformer load failed ({exc2}); falling back to AutoModel: {self.model_name}")
                import torch
                from transformers import AutoModel, AutoTokenizer

                self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
                self.hf_model = AutoModel.from_pretrained(self.model_name)
                self.hf_model.eval()
                self.hf_model.to(self.device)
                self.backend = "transformers_hf"
                logger.info(f"Loaded via HuggingFace AutoModel: {self.model_name}")

    def encode_queries(self, queries: list[str], batch_size: int = 16) -> np.ndarray:
        """Encode queries with optional model-specific prefixes."""
        if self.is_bge:
            processed = [f"{self.bge_query_prefix}{q}" for q in queries]
        else:
            processed = queries

        return self._encode_texts(processed, batch_size=batch_size, show_progress=False)

    def encode_corpus(self, texts: list[str], batch_size: int = 16) -> np.ndarray:
        """Encode corpus chunks."""
        return self._encode_texts(texts, batch_size=batch_size, show_progress=True)

    def _encode_texts(self, texts: list[str], batch_size: int = 16, show_progress: bool = False) -> np.ndarray:
        if self.backend == "sentence_transformers":
            import torch
            device_type = "cuda" if "cuda" in str(self.device).lower() else "cpu"
            if device_type == "cuda":
                with torch.inference_mode():
                    with torch.autocast(device_type="cuda", dtype=torch.float16):
                        embeddings = self.model.encode(
                            texts,
                            batch_size=batch_size,
                            show_progress_bar=show_progress,
                            normalize_embeddings=True,
                            convert_to_numpy=True,
                        )
            else:
                embeddings = self.model.encode(
                    texts,
                    batch_size=batch_size,
                    show_progress_bar=show_progress,
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                )
            return embeddings.astype(np.float32)
        else:
            import torch
            all_embeddings = []
            num_batches = math.ceil(len(texts) / batch_size)
            iterator = range(num_batches)
            if show_progress:
                from tqdm import tqdm
                iterator = tqdm(iterator, desc=f"Encoding ({self.model_name})")

            for i in iterator:
                batch_texts = texts[i * batch_size : (i + 1) * batch_size]
                encoded = self.tokenizer(
                    batch_texts,
                    padding=True,
                    truncation=True,
                    max_length=512,
                    return_tensors="pt",
                ).to(self.device)
                with torch.no_grad():
                    output = self.hf_model(**encoded)
                    # Mean pooling
                    token_emb = output[0]
                    mask = encoded["attention_mask"].unsqueeze(-1).expand(token_emb.size()).float()
                    summed = torch.sum(token_emb * mask, 1)
                    denom = torch.clamp(mask.sum(1), min=1e-9)
                    pooled = summed / denom
                    # Normalize
                    normed = torch.nn.functional.normalize(pooled, p=2, dim=1)
                    all_embeddings.append(normed.cpu().numpy())
            return np.vstack(all_embeddings).astype(np.float32)


# ----------------------------------------------------------------------
# Data loading helpers
# ----------------------------------------------------------------------
def load_queries(queries_path: Path) -> list[dict]:
    queries = []
    with open(queries_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                queries.append(json.loads(line))
    return queries


def load_qrels(qrels_path: Path) -> dict[str, dict[str, int]]:
    """Return dict: query_id -> {chunk_id: score}."""
    qrels = defaultdict(dict)
    with open(qrels_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            qrels[item["query_id"]][item["chunk_id"]] = int(item["score"])
    return qrels


def load_corpus(
    sqlite_path: Path,
    jsonl_path: Path,
    eval_scope: str = "candidate_pool",
    target_chunk_ids: set[str] | None = None,
    pool_size: int = 1000,
    limit: int | None = None,
) -> tuple[list[str], list[str], list[dict]]:
    """Load corpus chunks. Returns (chunk_ids, corpus_texts, chunk_metadata).

    Scopes:
    - candidate_pool: loads all target_chunk_ids (ground truth qrels) + distractors up to pool_size.
    - full: loads all chunks from corpus.
    """
    raw_chunks: list[dict] = []

    if jsonl_path.is_file():
        logger.info(f"Reading corpus chunks from JSONL: {jsonl_path}")
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    raw_chunks.append(json.loads(line))
    elif sqlite_path.is_file():
        logger.info(f"Reading corpus chunks from SQLite: {sqlite_path}")
        conn = sqlite3.connect(sqlite_path)
        cur = conn.cursor()
        cur.execute("SELECT chunk_id, doi, topic_group, section_title, content_type, token_count, document FROM chunks ORDER BY chunk_id")
        for row in cur.fetchall():
            cid, doi, topic, sec_title, ctype, tokens, doc_str = row
            doc = json.loads(doc_str)
            raw_chunks.append({
                "chunk_id": cid,
                "doi": doi,
                "title": doc.get("title", ""),
                "section_title": sec_title,
                "content_type": ctype,
                "text": doc.get("text", ""),
                "token_count": tokens,
                "topic_group": topic,
            })
        conn.close()
    else:
        raise FileNotFoundError(f"Corpus file not found at {jsonl_path} or {sqlite_path}")

    # Apply scope filtering
    selected_chunks: list[dict] = []
    if eval_scope == "candidate_pool" and target_chunk_ids:
        relevant_chunks = [c for c in raw_chunks if c["chunk_id"] in target_chunk_ids]
        distractor_candidates = [c for c in raw_chunks if c["chunk_id"] not in target_chunk_ids]
        num_distractors = max(0, pool_size - len(relevant_chunks))
        distractors = distractor_candidates[:num_distractors]
        selected_chunks = relevant_chunks + distractors
        logger.info(
            f"Candidate Pool Scope: loaded {len(relevant_chunks)} ground-truth relevant chunks "
            f"+ {len(distractors)} distractor chunks (Total pool: {len(selected_chunks)})"
        )
    else:
        if limit:
            selected_chunks = raw_chunks[:limit]
        else:
            selected_chunks = raw_chunks
        logger.info(f"Full Corpus Scope: loaded {len(selected_chunks)} chunks")

    chunk_ids: list[str] = []
    corpus_texts: list[str] = []
    chunk_metadata: list[dict] = []

    for item in selected_chunks:
        cid = item["chunk_id"]
        title = item.get("title", "")
        sec_title = item.get("section_title", "")
        text = item.get("text", "")
        formatted_text = f"{title} | {sec_title}\n{text}" if sec_title else f"{title}\n{text}"
        chunk_ids.append(cid)
        corpus_texts.append(formatted_text)
        chunk_metadata.append({
            "chunk_id": cid,
            "doi": item.get("doi", ""),
            "title": title,
            "section_title": sec_title,
            "content_type": item.get("content_type", "text"),
            "token_count": item.get("token_count", 0),
            "topic_group": item.get("topic_group", "General"),
        })

    return chunk_ids, corpus_texts, chunk_metadata


# ----------------------------------------------------------------------
# Embedding cache management
# ----------------------------------------------------------------------
def get_safe_model_slug(model_name: str) -> str:
    return model_name.replace("/", "__").replace("-", "_").lower()


def load_cached_embeddings(
    cache_dir: Path,
    model_name: str,
    expected_chunk_ids: list[str],
) -> np.ndarray | None:
    slug = get_safe_model_slug(model_name)
    emb_path = cache_dir / slug / "corpus_embeddings.npy"
    ids_path = cache_dir / slug / "chunk_ids.json"

    if emb_path.is_file() and ids_path.is_file():
        try:
            with open(ids_path, "r", encoding="utf-8") as f:
                cached_ids = json.load(f)
            if cached_ids == expected_chunk_ids:
                logger.info(f"Found valid cached embeddings for {model_name} ({emb_path})")
                return np.load(emb_path)
        except Exception as exc:
            logger.warning(f"Error loading cache for {model_name}: {exc}")
    return None


def save_cached_embeddings(
    cache_dir: Path,
    model_name: str,
    chunk_ids: list[str],
    embeddings: np.ndarray,
):
    slug = get_safe_model_slug(model_name)
    target_dir = cache_dir / slug
    target_dir.mkdir(parents=True, exist_ok=True)
    emb_path = target_dir / "corpus_embeddings.npy"
    ids_path = target_dir / "chunk_ids.json"

    np.save(emb_path, embeddings)
    with open(ids_path, "w", encoding="utf-8") as f:
        json.dump(chunk_ids, f)
    logger.info(f"Cached {len(chunk_ids)} embeddings to {emb_path}")


# ----------------------------------------------------------------------
# Core Benchmark Runner
# ----------------------------------------------------------------------
def evaluate_model(
    model_name: str,
    model_wrapper: BiEncoderModel,
    queries: list[dict],
    qrels: dict[str, dict[str, int]],
    chunk_ids: list[str],
    corpus_texts: list[str],
    cache_dir: Path | None = None,
    use_cache: bool = True,
    batch_size: int = 64,
) -> dict[str, Any]:
    """Run full evaluation of a single Bi-Encoder model."""
    logger.info(f"=== Evaluating Model: {model_name} ===")
    start_time = time.time()

    # 1. Obtain corpus embeddings
    corpus_embeddings = None
    if use_cache and cache_dir:
        corpus_embeddings = load_cached_embeddings(cache_dir, model_name, chunk_ids)

    if corpus_embeddings is None:
        enc_start = time.time()
        logger.info(f"Encoding {len(corpus_texts)} corpus chunks with {model_name}...")
        corpus_embeddings = model_wrapper.encode_corpus(corpus_texts, batch_size=batch_size)
        enc_duration = time.time() - enc_start
        logger.info(f"Corpus encoding completed in {enc_duration:.2f}s ({len(corpus_texts)/enc_duration:.1f} chunks/sec)")
        if use_cache and cache_dir:
            save_cached_embeddings(cache_dir, model_name, chunk_ids, corpus_embeddings)
    else:
        enc_duration = 0.0

    # 2. Encode queries
    query_texts = [q["query"] for q in queries]
    q_start = time.time()
    query_embeddings = model_wrapper.encode_queries(query_texts, batch_size=32)
    q_duration = time.time() - q_start
    logger.info(f"Query encoding completed in {q_duration:.2f}s ({len(query_texts)/q_duration:.1f} q/sec)")

    # 3. Dense Retrieval: Cosine similarity via Dot Product
    logger.info("Computing dense retrieval similarity matrix...")
    ret_start = time.time()
    # (num_queries, dim) @ (dim, num_chunks) -> (num_queries, num_chunks)
    similarity_matrix = np.dot(query_embeddings, corpus_embeddings.T)
    ret_duration = time.time() - ret_start
    logger.info(f"Similarity matrix computation completed in {ret_duration:.4f}s")

    # 4. Measure metrics
    chunk_id_arr = np.array(chunk_ids)
    all_recall_5 = []
    all_recall_10 = []
    all_recall_20 = []
    all_mrr_10 = []
    all_ndcg_10 = []

    topic_metrics = defaultdict(lambda: {
        "recall_5": [],
        "recall_10": [],
        "recall_20": [],
        "mrr_10": [],
        "ndcg_10": [],
    })

    # Top-K indices for each query
    # Top 20 is sufficient for Recall@20, MRR@10, nDCG@10
    top_k = 20
    for q_idx, q_item in enumerate(queries):
        qid = q_item["query_id"]
        topic = q_item.get("topic_group", "General")
        rel_dict = qrels.get(qid, {})

        # Find top 20 chunk indices descending by similarity
        scores = similarity_matrix[q_idx]
        if len(scores) <= top_k:
            top_indices = np.argsort(scores)[::-1]
        else:
            # argpartition for speed
            candidate_part = np.argpartition(scores, -top_k)[-top_k:]
            sorted_part = candidate_part[np.argsort(scores[candidate_part])[::-1]]
            top_indices = sorted_part

        ranked_cids = chunk_id_arr[top_indices].tolist()

        r5 = compute_recall_at_k(ranked_cids, rel_dict, k=5)
        r10 = compute_recall_at_k(ranked_cids, rel_dict, k=10)
        r20 = compute_recall_at_k(ranked_cids, rel_dict, k=20)
        mrr10 = compute_mrr_at_k(ranked_cids, rel_dict, k=10)
        ndcg10 = compute_ndcg_at_k(ranked_cids, rel_dict, k=10)

        all_recall_5.append(r5)
        all_recall_10.append(r10)
        all_recall_20.append(r20)
        all_mrr_10.append(mrr10)
        all_ndcg_10.append(ndcg10)

        tm = topic_metrics[topic]
        tm["recall_5"].append(r5)
        tm["recall_10"].append(r10)
        tm["recall_20"].append(r20)
        tm["mrr_10"].append(mrr10)
        tm["ndcg_10"].append(ndcg10)

    total_time = time.time() - start_time

    # Macro averages
    macro_metrics = {
        "recall_at_5": float(np.mean(all_recall_5)),
        "recall_at_10": float(np.mean(all_recall_10)),
        "recall_at_20": float(np.mean(all_recall_20)),
        "mrr_at_10": float(np.mean(all_mrr_10)),
        "ndcg_at_10": float(np.mean(all_ndcg_10)),
    }

    # Per-topic summary
    topic_summary = {}
    for topic, vals in topic_metrics.items():
        topic_summary[topic] = {
            "num_queries": len(vals["recall_10"]),
            "recall_at_5": float(np.mean(vals["recall_5"])),
            "recall_at_10": float(np.mean(vals["recall_10"])),
            "recall_at_20": float(np.mean(vals["recall_20"])),
            "mrr_at_10": float(np.mean(vals["mrr_10"])),
            "ndcg_at_10": float(np.mean(vals["ndcg_10"])),
        }

    logger.info(
        f"Result for {model_name}: "
        f"R@5={macro_metrics['recall_at_5']:.4f}, "
        f"R@10={macro_metrics['recall_at_10']:.4f}, "
        f"R@20={macro_metrics['recall_at_20']:.4f}, "
        f"MRR@10={macro_metrics['mrr_at_10']:.4f}, "
        f"nDCG@10={macro_metrics['ndcg_at_10']:.4f}"
    )

    return {
        "model_name": model_name,
        "macro_metrics": macro_metrics,
        "topic_metrics": topic_summary,
        "timing": {
            "corpus_encoding_seconds": round(enc_duration, 2),
            "query_encoding_seconds": round(q_duration, 3),
            "retrieval_seconds": round(ret_duration, 4),
            "total_eval_seconds": round(total_time, 2),
        },
    }


def generate_markdown_report(report_data: dict, output_path: Path):
    """Generate Markdown comparison report with GitHub Flavored Markdown."""
    models_results = report_data["models"]
    meta = report_data["metadata"]

    lines = []
    lines.append("# ScienceDirect Bi-Encoder Retrieval Benchmark Report")
    lines.append("")
    lines.append(f"**Execution Timestamp:** `{meta['timestamp']}`  ")
    lines.append(f"**Evaluation Scope:** `{meta.get('eval_scope', 'candidate_pool')}` ({meta['num_corpus_chunks']:,} evaluated chunks across {meta['num_papers']} ScienceDirect papers)  ")
    lines.append(f"**Benchmark Test Set:** `{meta['num_queries']}` formulated scientific queries across `{meta['num_topics']}` topic groups  ")
    lines.append(f"**Relevance Judgments (qrels):** `{meta['num_qrels']:,}` pairs (Score 2: primary body evidence, Score 1: abstract/background)  ")
    lines.append(f"**Hardware Device:** `{meta['device']}`  ")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 1. Overall Performance Comparison (Macro-Averages)")
    lines.append("")
    lines.append("| Rank | Model | Recall@5 | Recall@10 | Recall@20 | MRR@10 | nDCG@10 | Total Eval Time (s) |")
    lines.append("|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|")

    # Sort models by nDCG@10 descending
    sorted_models = sorted(
        models_results,
        key=lambda m: (m["macro_metrics"]["ndcg_at_10"], m["macro_metrics"]["recall_at_10"]),
        reverse=True,
    )

    for rank, m in enumerate(sorted_models, start=1):
        name = m["model_name"]
        mm = m["macro_metrics"]
        timing = m["timing"]
        crown = " 🥇" if rank == 1 else (" 🥈" if rank == 2 else " 🥉")
        lines.append(
            f"| {rank} | **{name}**{crown} | {mm['recall_at_5']:.4f} | {mm['recall_at_10']:.4f} | "
            f"{mm['recall_at_20']:.4f} | {mm['mrr_at_10']:.4f} | **{mm['ndcg_at_10']:.4f}** | {timing['total_eval_seconds']}s |"
        )

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 2. Per-Topic Group Performance Breakdown")
    lines.append("")

    topics = list(sorted_models[0]["topic_metrics"].keys())
    for topic in topics:
        lines.append(f"### Topic: {topic}")
        lines.append("")
        lines.append("| Model | Recall@5 | Recall@10 | Recall@20 | MRR@10 | nDCG@10 |")
        lines.append("|:---|:---:|:---:|:---:|:---:|:---:|")
        for m in sorted_models:
            tm = m["topic_metrics"].get(topic, {})
            lines.append(
                f"| `{m['model_name']}` | {tm.get('recall_at_5', 0.0):.4f} | {tm.get('recall_at_10', 0.0):.4f} | "
                f"{tm.get('recall_at_20', 0.0):.4f} | {tm.get('mrr_at_10', 0.0):.4f} | {tm.get('ndcg_at_10', 0.0):.4f} |"
            )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 3. Engineering Insights & Pipeline Recommendations")
    lines.append("")
    best_model = sorted_models[0]
    lines.append(f"1. **Primary Retriever Selection:** **`{best_model['model_name']}`** achieved the highest overall retrieval effectiveness with an nDCG@10 of **{best_model['macro_metrics']['ndcg_at_10']:.4f}** and Recall@10 of **{best_model['macro_metrics']['recall_at_10']:.4f}**.")
    lines.append("2. **Two-Stage Architecture (Bi-Encoder + Cross-Encoder):**")
    lines.append(f"   - **Stage 1 (Fast Retrieval):** Deploy `{best_model['model_name']}` to retrieve candidate Top-20 chunks per query from the 21k+ ScienceDirect corpus in <10ms.")
    lines.append("   - **Stage 2 (High-Precision Reranking):** Apply `BAAI/bge-reranker-large` on the Top-20 candidates to re-score cross-attention between Question and Chunk, eliminating spurious false positives before context synthesis.")
    lines.append("3. **Integration with MemGPT Adaptive Memory:**")
    lines.append("   - **Archival Storage:** Persist chunks and dense embeddings in PostgreSQL + `pgvector` indexed via HNSW.")
    lines.append("   - **Sufficiency Evaluator:** If maximum reranker score < threshold or essential technical evidence is absent, generate targeted follow-up query and search again before generating final scientific response.")
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info(f"Generated Markdown report at {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Benchmark Bi-Encoder retrieval models on ScienceDirect dataset")
    parser.add_argument("--sqlite-corpus", type=Path, default=DEFAULT_SQLITE_CORPUS, help="Path to corpus.sqlite")
    parser.add_argument("--jsonl-corpus", type=Path, default=DEFAULT_JSONL_CORPUS, help="Path to corpus_chunks.jsonl")
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES, help="Path to queries.jsonl")
    parser.add_argument("--qrels", type=Path, default=DEFAULT_QRELS, help="Path to qrels.jsonl")
    parser.add_argument("--output-json", type=Path, default=DEFAULT_REPORT_JSON, help="Path to JSON report")
    parser.add_argument("--output-md", type=Path, default=DEFAULT_REPORT_MD, help="Path to MD report")
    parser.add_argument("--embeddings-dir", type=Path, default=DEFAULT_EMBEDDINGS_DIR, help="Cache dir for embeddings")
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS, help="List of model names to evaluate")
    parser.add_argument("--device", type=str, default="auto", help="Execution device ('cuda', 'cpu', or 'auto')")
    parser.add_argument("--eval-scope", type=str, default="candidate_pool", choices=["candidate_pool", "full"], help="Evaluation scope: 'candidate_pool' (ground-truth chunks + distractors) or 'full' (all corpus chunks)")
    parser.add_argument("--pool-size", type=int, default=1000, help="Candidate pool size when using candidate_pool scope")
    parser.add_argument("--batch-size", type=int, default=16, help="Batch size for corpus encoding")
    parser.add_argument("--no-cache", action="store_true", help="Disable embedding cache")
    parser.add_argument("--corpus-limit", type=int, default=None, help="Limit number of corpus chunks (for dry-run/debug)")
    args = parser.parse_args()

    # Determine device
    import torch
    if args.device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    else:
        device = args.device
    logger.info(f"Target execution device: {device} (CUDA available: {torch.cuda.is_available()})")

    # Load data
    logger.info(f"Loading queries from {args.queries}...")
    queries = load_queries(args.queries)
    logger.info(f"Loaded {len(queries)} benchmark queries.")

    logger.info(f"Loading qrels from {args.qrels}...")
    qrels = load_qrels(args.qrels)
    total_judgments = sum(len(cids) for cids in qrels.values())
    target_chunk_ids = set(cid for cids in qrels.values() for cid in cids)
    logger.info(f"Loaded {len(qrels)} qrel queries with {total_judgments} relevance judgments across {len(target_chunk_ids)} unique target chunks.")

    chunk_ids, corpus_texts, chunk_metadata = load_corpus(
        sqlite_path=args.sqlite_corpus,
        jsonl_path=args.jsonl_corpus,
        eval_scope=args.eval_scope,
        target_chunk_ids=target_chunk_ids,
        pool_size=args.pool_size,
        limit=args.corpus_limit,
    )

    models_results = []
    use_cache = not args.no_cache

    for model_name in args.models:
        try:
            model_wrapper = BiEncoderModel(model_name=model_name, device=device)
            res = evaluate_model(
                model_name=model_name,
                model_wrapper=model_wrapper,
                queries=queries,
                qrels=qrels,
                chunk_ids=chunk_ids,
                corpus_texts=corpus_texts,
                cache_dir=args.embeddings_dir,
                use_cache=use_cache,
                batch_size=args.batch_size,
            )
            models_results.append(res)
            # Free memory between models
            del model_wrapper
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception as exc:
            logger.error(f"Failed benchmark evaluation for model {model_name}: {exc}", exc_info=True)

    # Compile report
    report_data = {
        "metadata": {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "device": device,
            "eval_scope": args.eval_scope,
            "pool_size": len(chunk_ids),
            "num_papers": len(set(m["doi"] for m in chunk_metadata if m["doi"])),
            "num_corpus_chunks": len(chunk_ids),
            "num_queries": len(queries),
            "num_topics": len(set(q.get("topic_group", "") for q in queries)),
            "num_qrels": total_judgments,
            "models_tested": [m["model_name"] for m in models_results],
        },
        "models": models_results,
    }

    # Save JSON report
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)
    logger.info(f"Saved JSON report to {args.output_json}")

    # Save Markdown report
    generate_markdown_report(report_data, args.output_md)


if __name__ == "__main__":
    main()
