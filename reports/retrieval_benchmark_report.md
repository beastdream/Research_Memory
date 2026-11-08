# ScienceDirect Bi-Encoder Retrieval Benchmark Report

**Execution Timestamp:** `2026-11-08T09:42:59.940129+00:00`  
**Evaluation Scope:** `candidate_pool` (1,000 evaluated chunks across 126 ScienceDirect papers)  
**Benchmark Test Set:** `125` formulated scientific queries across `5` topic groups  
**Relevance Judgments (qrels):** `691` pairs (Score 2: primary body evidence, Score 1: abstract/background)  
**Hardware Device:** `cuda`  

---

## 1. Overall Performance Comparison (Macro-Averages)

| Rank | Model | Recall@5 | Recall@10 | Recall@20 | MRR@10 | nDCG@10 | Total Eval Time (s) |
|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | **BAAI/bge-base-en-v1.5** 🥇 | 0.6985 | 0.8469 | 0.8757 | 0.8859 | **0.7297** | 234.7s |
| 2 | **facebook/contriever** 🥈 | 0.4475 | 0.5902 | 0.7067 | 0.7615 | **0.4921** | 257.47s |
| 3 | **allenai/specter2_base** 🥉 | 0.3222 | 0.4302 | 0.5520 | 0.6401 | **0.3625** | 236.31s |

---

## 2. Per-Topic Group Performance Breakdown

### Topic: AI Agents and Long-Term Memory

| Model | Recall@5 | Recall@10 | Recall@20 | MRR@10 | nDCG@10 |
|:---|:---:|:---:|:---:|:---:|:---:|
| `BAAI/bge-base-en-v1.5` | 0.6157 | 0.7557 | 0.7714 | 0.7707 | 0.6758 |
| `facebook/contriever` | 0.4204 | 0.5522 | 0.6631 | 0.8097 | 0.4624 |
| `allenai/specter2_base` | 0.2481 | 0.3348 | 0.4864 | 0.6302 | 0.3035 |

### Topic: RAG and Information Retrieval

| Model | Recall@5 | Recall@10 | Recall@20 | MRR@10 | nDCG@10 |
|:---|:---:|:---:|:---:|:---:|:---:|
| `BAAI/bge-base-en-v1.5` | 0.6140 | 0.8219 | 0.8819 | 0.8850 | 0.6732 |
| `facebook/contriever` | 0.3928 | 0.4994 | 0.6592 | 0.7025 | 0.4160 |
| `allenai/specter2_base` | 0.2774 | 0.3684 | 0.5144 | 0.5907 | 0.3192 |

### Topic: Large Language Models

| Model | Recall@5 | Recall@10 | Recall@20 | MRR@10 | nDCG@10 |
|:---|:---:|:---:|:---:|:---:|:---:|
| `BAAI/bge-base-en-v1.5` | 0.7031 | 0.8346 | 0.8797 | 0.8900 | 0.7280 |
| `facebook/contriever` | 0.4369 | 0.6249 | 0.7386 | 0.6573 | 0.4997 |
| `allenai/specter2_base` | 0.3626 | 0.4654 | 0.5606 | 0.6600 | 0.4099 |

### Topic: Hallucination and Faithfulness

| Model | Recall@5 | Recall@10 | Recall@20 | MRR@10 | nDCG@10 |
|:---|:---:|:---:|:---:|:---:|:---:|
| `BAAI/bge-base-en-v1.5` | 0.7033 | 0.8624 | 0.8852 | 0.9240 | 0.7381 |
| `facebook/contriever` | 0.3711 | 0.5011 | 0.6187 | 0.7473 | 0.4313 |
| `allenai/specter2_base` | 0.2489 | 0.3565 | 0.4760 | 0.5418 | 0.2996 |

### Topic: Scientific NLP and QA

| Model | Recall@5 | Recall@10 | Recall@20 | MRR@10 | nDCG@10 |
|:---|:---:|:---:|:---:|:---:|:---:|
| `BAAI/bge-base-en-v1.5` | 0.8562 | 0.9600 | 0.9600 | 0.9600 | 0.8333 |
| `facebook/contriever` | 0.6162 | 0.7733 | 0.8538 | 0.8907 | 0.6514 |
| `allenai/specter2_base` | 0.4738 | 0.6257 | 0.7229 | 0.7780 | 0.4804 |

---

## 3. Engineering Insights & Pipeline Recommendations

1. **Primary Retriever Selection:** **`BAAI/bge-base-en-v1.5`** achieved the highest overall retrieval effectiveness with an nDCG@10 of **0.7297** and Recall@10 of **0.8469**.
2. **Two-Stage Architecture (Bi-Encoder + Cross-Encoder):**
   - **Stage 1 (Fast Retrieval):** Deploy `BAAI/bge-base-en-v1.5` to retrieve candidate Top-20 chunks per query from the 21k+ ScienceDirect corpus in <10ms.
   - **Stage 2 (High-Precision Reranking):** Apply `BAAI/bge-reranker-large` on the Top-20 candidates to re-score cross-attention between Question and Chunk, eliminating spurious false positives before context synthesis.
3. **Integration with MemGPT Adaptive Memory:**
   - **Archival Storage:** Persist chunks and dense embeddings in PostgreSQL + `pgvector` indexed via HNSW.
   - **Sufficiency Evaluator:** If maximum reranker score < threshold or essential technical evidence is absent, generate targeted follow-up query and search again before generating final scientific response.
