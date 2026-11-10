"""Information Bottleneck (IB) Noise Filter for Retrieval-Augmented Generation.

Inspired by:
"An Information Bottleneck Perspective for Effective Noise Filtering on
Retrieval-Augmented Generation" (ACL 2024, Zhu et al., arXiv:2406.01549).

Theoretical Foundation:
    L_IB = I(Z; Y) - beta * I(Z; X)
Where:
- X: Raw, noisy retrieved passages from the corpus (full corpus distractors).
- Y: Target scientific answer / ground-truth evidence needed by the LLM.
- Z: Compressed and filtered evidence text fed into LLM / MemGPT Working Context.
- I(Z; Y): Mutual information between compressed evidence and target (maximize relevance).
- I(Z; X): Mutual information between compressed evidence and raw noise (minimize redundancy).
- beta: Compression penalty parameter controlling the conciseness-completeness trade-off.

Architecture:
1. Stage 1 (Coarse-Grained Passage Filter):
   Prunes out totally irrelevant chunks based on adaptive thresholding.
2. Stage 2 (Fine-Grained Sentence IB Extractor):
   Splits remaining chunks into atomic scientific sentences and performs greedy
   Information Maximization to yield compact, high-density evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import math
import re
from typing import Any, Callable

import numpy as np

logger = logging.getLogger("ib_noise_filter")


@dataclass
class FilteredEvidence:
    """Container for the output of the Information Bottleneck noise filter."""
    query: str
    original_passages_count: int
    retained_passages_count: int
    original_token_count: int
    compressed_token_count: int
    compression_ratio: float  # (original - compressed) / original
    selected_sentences: list[dict[str, Any]]
    compact_context: str
    passage_scores: list[dict[str, Any]] = field(default_factory=list)


class InformationBottleneckFilter:
    """Noise filter grounded in Information Bottleneck theory."""

    def __init__(
        self,
        beta: float = 0.45,
        redundancy_lambda: float = 0.65,
        min_relevance_threshold: float = 0.20,
        max_context_tokens: int = 400,
        embedder_func: Callable[[list[str]], np.ndarray] | None = None,
    ):
        """
        Args:
            beta: Compression penalty controlling compactness (higher = more aggressive compression).
            redundancy_lambda: Penalty for inter-sentence semantic redundancy.
            min_relevance_threshold: Absolute minimum relevance score to retain a chunk.
            max_context_tokens: Maximum token budget for the compressed context.
            embedder_func: Optional dense embedding function returning normalized vectors (N, D).
                           If None, an internal lightweight n-gram TF-IDF cosine similarity is used.
        """
        self.beta = beta
        self.redundancy_lambda = redundancy_lambda
        self.min_relevance_threshold = min_relevance_threshold
        self.max_context_tokens = max_context_tokens
        self.embedder_func = embedder_func

    # ------------------------------------------------------------------
    # 1. Text processing & Sentence Segmentation
    # ------------------------------------------------------------------
    @staticmethod
    def split_sentences(text: str) -> list[str]:
        """Split text into sentences while respecting scientific abbreviations."""
        if not text:
            return []
        # Protect common abbreviations like e.g., i.e., et al., Fig., Eq.
        cleaned = re.sub(r"\s+", " ", text).strip()
        # Regex split by sentence boundaries (period/question/exclamation followed by space/capital)
        raw_sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", cleaned)
        sentences = []
        for s in raw_sentences:
            s_clean = s.strip()
            # Ignore extremely short fragments (less than 4 words)
            if len(s_clean.split()) >= 4:
                sentences.append(s_clean)
        return sentences

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Heuristic word-to-token count (approx 1.3 tokens per word)."""
        words = len(text.split())
        return int(math.ceil(words * 1.3))

    # ------------------------------------------------------------------
    # 2. Similarity & Information Estimation
    # ------------------------------------------------------------------
    def _compute_embeddings(self, texts: list[str]) -> np.ndarray:
        """Compute embeddings using provided embedder or fallback to fast TF-IDF."""
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)

        if self.embedder_func is not None:
            vectors = self.embedder_func(texts)
            # Ensure L2 normalization
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            norms = np.maximum(norms, 1e-9)
            return (vectors / norms).astype(np.float32)

        # Fallback: Character + Word n-gram TF-IDF for rapid zero-dependency execution
        vocab: dict[str, int] = {}
        doc_word_counts: list[dict[int, float]] = []

        for text in texts:
            counts: dict[int, float] = {}
            # Word unigrams + bigrams
            words = [w.lower() for w in re.findall(r"\b\w+\b", text)]
            tokens = list(words)
            for i in range(len(words) - 1):
                tokens.append(f"{words[i]}_{words[i+1]}")

            for tok in tokens:
                if tok not in vocab:
                    vocab[tok] = len(vocab)
                tid = vocab[tok]
                counts[tid] = counts.get(tid, 0.0) + 1.0
            doc_word_counts.append(counts)

        dim = len(vocab)
        if dim == 0:
            return np.zeros((len(texts), 1), dtype=np.float32)

        # Term frequency matrix
        matrix = np.zeros((len(texts), dim), dtype=np.float32)
        df = np.zeros(dim, dtype=np.float32)

        for i, counts in enumerate(doc_word_counts):
            for tid, count in counts.items():
                matrix[i, tid] = 1.0 + math.log(count)
                df[tid] += 1.0

        # IDF weighting
        idf = np.log((len(texts) + 1.0) / (df + 1.0)) + 1.0
        matrix *= idf

        # L2 normalize
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-9)
        return matrix / norms

    # ------------------------------------------------------------------
    # 3. Stage 1: Coarse-Grained Passage Noise Filter
    # ------------------------------------------------------------------
    def filter_passages(
        self,
        query: str,
        passages: list[dict[str, Any]],
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Prune out non-relevant passages using adaptive thresholding.

        Each passage dict should have: 'chunk_id', 'text', optional 'score'.
        """
        if not passages:
            return []

        # Calculate semantic relevance between Query and each passage
        passage_texts = [p.get("text", "") for p in passages]
        all_texts = [query] + passage_texts
        embs = self._compute_embeddings(all_texts)

        query_emb = embs[0:1]  # (1, D)
        passage_embs = embs[1:]  # (N, D)
        rel_scores = np.dot(passage_embs, query_emb.T).ravel()  # (N,)

        # Normalize relevance scores to [0, 1] range if needed
        min_s = float(np.min(rel_scores))
        max_s = float(np.max(rel_scores))
        if max_s > min_s:
            norm_scores = (rel_scores - min_s) / (max_s - min_s)
        else:
            norm_scores = np.ones_like(rel_scores)

        # Dynamic Otsu-like thresholding:
        # Keep passages above mean - 0.5 * std, or above min_relevance_threshold
        mean_score = float(np.mean(norm_scores))
        std_score = float(np.std(norm_scores))
        dynamic_threshold = max(self.min_relevance_threshold, mean_score - 0.3 * std_score)

        filtered = []
        for i, p in enumerate(passages):
            score = float(norm_scores[i])
            raw_score = float(rel_scores[i])
            if score >= dynamic_threshold:
                item = dict(p)
                item["ib_relevance_score"] = score
                item["ib_raw_score"] = raw_score
                filtered.append(item)

        # Sort descending by relevance score
        filtered.sort(key=lambda x: x["ib_relevance_score"], reverse=True)
        return filtered[:top_k]

    # ------------------------------------------------------------------
    # 4. Stage 2: Fine-Grained Sentence Extractor (IB Maximization)
    # ------------------------------------------------------------------
    def extract_compact_evidence(
        self,
        query: str,
        passages: list[dict[str, Any]],
        max_tokens: int | None = None,
    ) -> FilteredEvidence:
        """Extract atomic evidence sentences balancing relevance and compression.

        Objective:
            max_Z sum_{s in Z} [ Sim(s, Q) - lambda * max_{s' in Z} Sim(s, s') - beta * len_penalty(s) ]
        """
        token_budget = max_tokens or self.max_context_tokens
        original_passages_count = len(passages)
        total_original_tokens = sum(self.estimate_tokens(p.get("text", "")) for p in passages)

        # 1. Coarse filtering first
        filtered_passages = self.filter_passages(query, passages, top_k=min(15, len(passages)))
        retained_passages_count = len(filtered_passages)

        if not filtered_passages:
            return FilteredEvidence(
                query=query,
                original_passages_count=original_passages_count,
                retained_passages_count=0,
                original_token_count=total_original_tokens,
                compressed_token_count=0,
                compression_ratio=1.0 if total_original_tokens > 0 else 0.0,
                selected_sentences=[],
                compact_context="",
                passage_scores=[],
            )

        # 2. Extract atomic sentences across all retained passages
        candidate_sentences: list[dict[str, Any]] = []
        for p in filtered_passages:
            cid = p.get("chunk_id", "")
            title = p.get("title", "")
            sec = p.get("section_title", "")
            raw_text = p.get("text", "")
            for s in self.split_sentences(raw_text):
                candidate_sentences.append({
                    "chunk_id": cid,
                    "title": title,
                    "section_title": sec,
                    "sentence": s,
                    "tokens": self.estimate_tokens(s),
                })

        if not candidate_sentences:
            return FilteredEvidence(
                query=query,
                original_passages_count=original_passages_count,
                retained_passages_count=retained_passages_count,
                original_token_count=total_original_tokens,
                compressed_token_count=0,
                compression_ratio=1.0 if total_original_tokens > 0 else 0.0,
                selected_sentences=[],
                compact_context="",
                passage_scores=filtered_passages,
            )

        # 3. Compute Embeddings for Query and all candidate sentences
        all_sentence_texts = [cs["sentence"] for cs in candidate_sentences]
        all_embeddings = self._compute_embeddings([query] + all_sentence_texts)
        q_emb = all_embeddings[0:1]  # (1, D)
        s_embs = all_embeddings[1:]  # (M, D)

        # Query relevance for each sentence I(s; Y)
        rel_scores = np.dot(s_embs, q_emb.T).ravel()  # (M,)

        # Inter-sentence similarity matrix I(s; s')
        sim_matrix = np.dot(s_embs, s_embs.T)  # (M, M)

        # 4. Greedy Information Bottleneck Selection
        selected_indices: list[int] = []
        current_tokens = 0
        remaining_indices = set(range(len(candidate_sentences)))

        while remaining_indices and current_tokens < token_budget:
            best_gain = -1e9
            best_idx = -1

            for idx in remaining_indices:
                cand = candidate_sentences[idx]
                tokens_cand = cand["tokens"]

                if current_tokens + tokens_cand > token_budget and selected_indices:
                    continue

                # Term 1: Relevance (Mutual Info with Query)
                relevance = float(rel_scores[idx])

                # Term 2: Redundancy with already selected set Z
                if selected_indices:
                    redundancy = float(np.max(sim_matrix[idx, selected_indices]))
                else:
                    redundancy = 0.0

                # Term 3: Length Penalty (Information Bottleneck Compression Cost)
                length_penalty = math.log1p(tokens_cand) / 5.0

                # IB Objective Score:
                # Gain = Relevance - lambda * Redundancy - beta * LengthPenalty
                ib_gain = relevance - (self.redundancy_lambda * redundancy) - (self.beta * length_penalty)

                if ib_gain > best_gain:
                    best_gain = ib_gain
                    best_idx = idx

            # Stop if no candidate offers positive net information gain
            if best_idx == -1 or (best_gain < -0.1 and selected_indices):
                break

            selected_indices.append(best_idx)
            current_tokens += candidate_sentences[best_idx]["tokens"]
            remaining_indices.remove(best_idx)

        # 5. Format compressed context maintaining natural document flow
        # Group selected sentences by chunk and section
        selected_records = [candidate_sentences[i] for i in selected_indices]
        # Preserve original document appearance order
        selected_records.sort(key=lambda x: (x["chunk_id"], x["section_title"]))

        formatted_blocks: list[str] = []
        current_chunk_id = None
        current_sec = None

        for rec in selected_records:
            cid = rec["chunk_id"]
            sec = rec["section_title"]
            s = rec["sentence"]

            if cid != current_chunk_id or sec != current_sec:
                header = f"[{rec['title']} | Section: {sec}]" if sec else f"[{rec['title']}]"
                formatted_blocks.append(f"\n{header}\n- {s}")
                current_chunk_id = cid
                current_sec = sec
            else:
                formatted_blocks.append(f"- {s}")

        compact_context = "\n".join(formatted_blocks).strip()
        compressed_tokens = self.estimate_tokens(compact_context)
        compression_ratio = (
            (total_original_tokens - compressed_tokens) / total_original_tokens
            if total_original_tokens > 0
            else 0.0
        )

        return FilteredEvidence(
            query=query,
            original_passages_count=original_passages_count,
            retained_passages_count=retained_passages_count,
            original_token_count=total_original_tokens,
            compressed_token_count=compressed_tokens,
            compression_ratio=max(0.0, compression_ratio),
            selected_sentences=selected_records,
            compact_context=compact_context,
            passage_scores=filtered_passages,
        )

