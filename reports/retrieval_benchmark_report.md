# ScienceDirect Bi-Encoder Retrieval Benchmark Report

**Execution Timestamp:** `2026-11-09T11:06:32.490641+00:00`  
**Evaluation Scope:** `full` (21,538 evaluated chunks across 297 ScienceDirect papers)  
**Benchmark Test Set:** `125` formulated scientific queries across `5` topic groups  
**Relevance Judgments (qrels):** `691` pairs (Score 2: primary body evidence, Score 1: abstract/background)  
**Hardware Device:** `cuda`  

---

## 1. Overall Performance Comparison (Macro-Averages)

| Rank | Model | Recall@5 | Recall@10 | Recall@20 | MRR@10 | nDCG@10 | Total Eval Time (s) |
|:---:|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| 1 | **BAAI/bge-base-en-v1.5** 🥇 | 0.1124 | 0.1787 | 0.3358 | 0.2530 | **0.1220** | 150.53s |
| 2 | **facebook/contriever** 🥈 | 0.0387 | 0.0837 | 0.1482 | 0.0979 | **0.0615** | 158.23s |
| 3 | **allenai/specter2_base** 🥉 | 0.0218 | 0.0606 | 0.1179 | 0.0712 | **0.0383** | 152.97s |

---

## 2. Per-Topic Group Performance Breakdown

### Topic: AI Agents and Long-Term Memory

| Model | Recall@5 | Recall@10 | Recall@20 | MRR@10 | nDCG@10 |
|:---|:---:|:---:|:---:|:---:|:---:|
| `BAAI/bge-base-en-v1.5` | 0.0942 | 0.1522 | 0.3183 | 0.1937 | 0.1172 |
| `facebook/contriever` | 0.0314 | 0.0905 | 0.1771 | 0.1212 | 0.0679 |
| `allenai/specter2_base` | 0.0124 | 0.0495 | 0.1124 | 0.1035 | 0.0385 |

### Topic: RAG and Information Retrieval

| Model | Recall@5 | Recall@10 | Recall@20 | MRR@10 | nDCG@10 |
|:---|:---:|:---:|:---:|:---:|:---:|
| `BAAI/bge-base-en-v1.5` | 0.0761 | 0.1637 | 0.2769 | 0.2438 | 0.1030 |
| `facebook/contriever` | 0.0390 | 0.0590 | 0.1061 | 0.0847 | 0.0511 |
| `allenai/specter2_base` | 0.0248 | 0.0448 | 0.0976 | 0.0723 | 0.0367 |

### Topic: Large Language Models

| Model | Recall@5 | Recall@10 | Recall@20 | MRR@10 | nDCG@10 |
|:---|:---:|:---:|:---:|:---:|:---:|
| `BAAI/bge-base-en-v1.5` | 0.0857 | 0.1514 | 0.2757 | 0.2300 | 0.0968 |
| `facebook/contriever` | 0.0157 | 0.0529 | 0.1223 | 0.0431 | 0.0269 |
| `allenai/specter2_base` | 0.0214 | 0.0629 | 0.1351 | 0.0550 | 0.0323 |

### Topic: Hallucination and Faithfulness

| Model | Recall@5 | Recall@10 | Recall@20 | MRR@10 | nDCG@10 |
|:---|:---:|:---:|:---:|:---:|:---:|
| `BAAI/bge-base-en-v1.5` | 0.1223 | 0.1808 | 0.3069 | 0.3217 | 0.1254 |
| `facebook/contriever` | 0.0638 | 0.0970 | 0.1662 | 0.1670 | 0.0870 |
| `allenai/specter2_base` | 0.0214 | 0.0599 | 0.1007 | 0.0841 | 0.0442 |

### Topic: Scientific NLP and QA

| Model | Recall@5 | Recall@10 | Recall@20 | MRR@10 | nDCG@10 |
|:---|:---:|:---:|:---:|:---:|:---:|
| `BAAI/bge-base-en-v1.5` | 0.1838 | 0.2452 | 0.5014 | 0.2759 | 0.1676 |
| `facebook/contriever` | 0.0433 | 0.1190 | 0.1690 | 0.0734 | 0.0743 |
| `allenai/specter2_base` | 0.0290 | 0.0857 | 0.1438 | 0.0409 | 0.0398 |

---

## 3. Engineering Insights & Pipeline Recommendations

1. **Primary Retriever Selection:** **`BAAI/bge-base-en-v1.5`** achieved the highest overall retrieval effectiveness with an nDCG@10 of **0.1220** and Recall@10 of **0.1787**.
2. **Two-Stage Architecture (Bi-Encoder + Cross-Encoder):**
   - **Stage 1 (Fast Retrieval):** Deploy `BAAI/bge-base-en-v1.5` to retrieve candidate Top-20 chunks per query from the 21k+ ScienceDirect corpus in <10ms.
   - **Stage 2 (High-Precision Reranking):** Apply `BAAI/bge-reranker-large` on the Top-20 candidates to re-score cross-attention between Question and Chunk, eliminating spurious false positives before context synthesis.
3. **Integration with MemGPT Adaptive Memory:**
   - **Archival Storage:** Persist chunks and dense embeddings in PostgreSQL + `pgvector` indexed via HNSW.
   - **Sufficiency Evaluator:** If maximum reranker score < threshold or essential technical evidence is absent, generate targeted follow-up query and search again before generating final scientific response.
