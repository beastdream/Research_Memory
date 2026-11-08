# ScienceDirect LLM Research Dataset — Collection & Quality Report
**Generated at (UTC):** 2026-10-28T12:25:01.788826+00:00  
**Target:** 150 papers  
**Status:** MET  

## 1. Executive Summary & Audit Counters

| Metric | Count | Verification Rule |
| :--- | ---: | :--- |
| **Candidates Discovered** | 213 | Across 5 topic groups |
| **DOI Verified (Crossref)** | 306 | 100% verified via Crossref registry |
| **Duplicates Skipped** | 47 | Exact match against arXiv or existing ScienceDirect |
| **Invalid / Mismatched DOIs** | 0 | Routed to rejected manifest |
| **Ambiguous / Review Count** | 10 | Routed to review manifest |
| **Valid Downloaded PDFs** | 157 | Magic bytes `%PDF-`, PyMuPDF > 0 pages, SHA256 verified |
| **Total Extracted PDF Pages** | 1308 | Extracted via PyMuPDF |
| **Master Dataset Records** | 306 | Conforms to `schemas/sciencedirect_master.schema.json` |
| **Enriched V2 Records** | 306 | Conforms to `docs/examples/memorybank_enriched_v2.json` |

## 2. Topic Group Quota Distribution

| Topic Group | Accepted | Target Quota |
| :--- | ---: | ---: |
| Large Language Models | 62 | 30 |
| RAG and Information Retrieval | 72 | 35 |
| AI Agents and Long-Term Memory | 72 | 35 |
| Hallucination and Faithfulness | 50 | 25 |
| Scientific NLP and QA | 50 | 25 |

## 3. Publication Year Distribution

| Year | Papers |
| :--- | ---: |
| 2026 | 195 |
| 2025 | 42 |
| 2024 | 20 |
| 2023 | 10 |
| 2022 | 8 |
| 2021 | 7 |
| 2020 | 11 |
| 2019 | 5 |
| 2018 | 8 |

## 4. Rights, Licenses & TDM Entitlements

- **Article Retrieval API PDF:** Authorized & verified for all downloaded papers.
- **Article Retrieval API XML:** Authorized where XML full-text feed is supplied.
- **TDM Rights:** Conditional subscription & academic research cache per Elsevier policies.
- **AI Training Policy:** Commercial redistribution & public raw redistribution disallowed; academic indexing and local RAG retrieval supported.

## 5. Storage & Artifact File Paths

- Master Dataset: `D:\ResearchMemory\datasets\sciencedirect\master\sciencedirect_master.jsonl`
- Enriched Master: `D:\ResearchMemory\datasets\sciencedirect\enriched\sciencedirect_enriched_v2.jsonl`
- Enriched Papers Directory: `D:\ResearchMemory\datasets\sciencedirect\enriched\papers`
- PDF Directory: `D:\ResearchMemory\storage\sciencedirect\pdf`
- Manifests Directory: `D:\ResearchMemory\datasets\sciencedirect\manifests`
- Report JSON: `D:\ResearchMemory\reports\sciencedirect_collection_report.json`
