# Master Dataset v2 — historical enrichment results, 25 October 2026

This records the earlier enrichment run. The subsequent [full audit and inspector guide](DATASET_AUDIT_AND_INSPECTOR.md)
and `reports/master_dataset_audit.json` supersede its current-dataset counts and parser revision.

The existing 150 selected/imported arXiv papers have been enriched, exported,
validated and stored in PostgreSQL. **Scientific extraction remains partial for
all 150 papers. This is not a claim of a fully verified scientific corpus.**

| Check | Verified result |
| --- | ---: |
| Selected / exported / PostgreSQL enriched masters | 150 / 150 / 150 |
| Matched / valid / missing PDFs | 150 / 150 / 0 |
| Full text complete / partial | 0 / 150 |
| Papers with heuristic section headings | 148 |
| Papers retaining fallback structure | 2 |
| Papers with figure candidates | 132 |
| Papers with table candidates / actual extracted cell arrays | 127 / 90 |
| Papers with bibliography fragments | 147 |
| Partial bibliography fragments / explicit identifier resolutions | 9,259 / 103 |
| Numeric citation-context candidates | 2,960 |
| Paragraphs with stable IDs | 53,958 |
| Papers with layout reordering | 114 |
| Papers with recorded NUL-glyph normalization / glyphs | 16 / 50 |
| Human / synthetic / human verified QA | 0 / 0 / 0 |
| Duplicate canonical IDs / missing year | 0 / 0 |
| Final import errors / extraction exceptions | 0 / 0 |
| Repeat PostgreSQL inserted / updated / unchanged / failed | 0 / 0 / 150 / 0 |
| JSONL records exactly equal to current PostgreSQL masters | 150 / 150 |
| Original tables' before/after fingerprints | All unchanged |
| Pytest | 63 passed, 4 skipped |

Counts for figures, tables and references describe **partial candidates**, not
verified complete objects. The 103 reference targets were resolved from explicit
unambiguous identifiers against this selected corpus; no semantic relationships
were invented. The 9,259 fragments are not guaranteed to correspond one-to-one
to bibliography entries. All absent QA remains `not_annotated`.

The first five-paper import inserted 5 enrichment records. The initial full
attempt inserted 129, updated 3 and left 2 unchanged; 16 failed because PDF
layout text contained NUL characters unsupported by JSONB. After adding
source-positioned glyph normalization, retry inserted those 16 and updated the
other 134. A final merge correction removed duplicates across parser revisions.
The final complete rerun reused all 150 enriched exports and returned **150
unchanged**, with zero timestamp rewrites. Older changed documents are retained
in 285 history rows; these are provenance snapshots, not duplicate canonical
papers. The original 2,061 papers, 2,061 source rows, 16,221 section rows and all
150 original source/PDF-version snapshots were preserved byte-for-byte at the
stored-row representation level.

All original PDF checksums match. There were no PDF downloads, candidate imports,
`.env` changes or QA API calls. Benign MuPDF color-space warnings occurred on
some source PDFs; text output still validated, but visual fidelity is not claimed.
The four skipped tests require a separately configured `researchmemory_test`
database. Application-database imports and read-only verification were performed;
no destructive tests ran against that database.

## Real paper and actual data excerpts

The complete schema-valid record is
[MemoryBank enriched v2](examples/memorybank_enriched_v2.json):
`arxiv:2305.10250`, version v3, 2023, with 13 sections, 5 figure/table candidates,
22 raw reference fragments and no QAs. The following is a shortened **display
excerpt**, not a standalone schema-valid record. Text and references are copied
from the actual retained extraction; paragraph/reference excerpts are truncated.

```json
{
  "canonical_id": "arxiv:2305.10250",
  "title": "MemoryBank: Enhancing Large Language Models with Long-Term Memory",
  "year": 2023,
  "identifiers": {
    "doi": null,
    "arxiv_id": "2305.10250",
    "openalex_id": null,
    "arxiv_version": "v3"
  },
  "full_text": {
    "extraction_status": "partial",
    "sections": [
      {
        "section_id": "sec-97f2f3748ffa5059b9c627ac",
        "heading": "1  Introduction",
        "paragraph_excerpt": {
          "paragraph_id": "para-6a2ebfc882d8e6334c0b67ff",
          "text": "The advent of Large Language Models (LLMs) such as ChatGPT (OpenAI, 2022) and GPT-4 (OpenAI,\n                     2023) has led to increasing inﬂuence across various sectors, from education and healthcare to customer\n                        service and entertainment. These powerful AI systems have demonstrated a remarkable ability to",
          "page_start": 1,
          "page_end": 1
        }
      }
    ]
  },
  "figure_example": {
    "data": null,
    "page": 3,
    "text": null,
    "type": "figure",
    "label": "Figure 1",
    "caption": "Overview of MemoryBank. The memory storage (§ 2.1) stores past conversations, summa-",
    "item_id": "figure-5c1fbf5564d0393329a1d54f",
    "figure_id": "figure-5c1fbf5564d0393329a1d54f",
    "image_path": null,
    "section_id": null,
    "description": null,
    "source_bbox": null,
    "detection_status": "unverified_caption_or_cells",
    "extraction_status": "partial"
  },
  "reference_excerpt": {
    "doi": null,
    "year": 2004,
    "title": null,
    "authors": [],
    "arxiv_id": "2004.04906",
    "reference_id": "ref-91d4a8d07f99bacbebac2862",
    "raw_reference": "Vladimir Karpukhin, Barlas O˘guz, Sewon Min, Patrick Lewis, Ledell Wu, Sergey Edunov, Danqi\n  Chen, and Wen-tau Yih. Dense passage retrieval for open-domain question answering. arXiv\n   preprint arXiv:2004.04906, 2020.",
    "target_paper_id": "arxiv:2004.04906",
    "citation_contexts": [],
    "extraction_status": "partial",
    "resolution_status": "resolved"
  },
  "qas": []
}
```

No actual QA example exists in these 150 papers. The code preserves original
QASPER annotations when exact identities match, but all 888 local train and
281 local validation IDs have zero matches with this selection. No test parquet
is locally present. No synthetic QA was generated to fill the gap.

## Delivered source changes

Created:

- `app/ingestion/enrichment.py`: source-preserving layout/structure adapter,
  stable IDs, reference resolution, original QASPER annotation import, validation.
- `app/ingestion/synthetic_qa.py`: explicitly opt-in provider adapter and grounded
  extractive candidate filtering; no default network calls.
- `app/storage/enrichment.py`: additive migration, query views, protected merges,
  content-based no-op imports and history.
- `scripts/enrich_arxiv.py`: selected-only manifest, five-paper gate, enrichment,
  cache/retry, exports, PostgreSQL upsert and audit.
- `schemas/master_dataset_v2_enriched.schema.json`: version 2.1 contract, retaining
  the existing 2.0 schema.
- `tests/test_enrichment.py`: offline fixtures for identity/version/structure,
  layout, real cells, partial records, evidence, QASPER splits, synthetic filtering,
  NUL glyphs, annotation preservation, deduplication and idempotency.
- `docs/MASTER_DATASET_V2_ENRICHMENT.md`, this results report, and the complete
  MemoryBank example above.

Updated:

- `app/ingestion/master_schema.py`: dispatch between old and enriched schemas.
- `app/storage/postgres.py`: initialize additive enrichment storage and route
  enriched records through its safe importer.
- `tests/test_postgres_integration.py`: isolated, rollback-only content/no-op/history test.
- `README_WINDOWS.md`: workflow/results links.
- `.gitignore`: excludes generated `datasets/master/` artifacts.

Generated local artifacts:

- `datasets/master/arxiv_master_v2.jsonl`
- `datasets/master/arxiv_enrichment_audit.json` (includes all 150 per-paper rows)
- `datasets/master/arxiv_enrichment_failures.jsonl` (empty after recovery)
- `datasets/master/qa_candidates.jsonl`, `qa_verified.jsonl`, `qa_human_annotated.jsonl`
  (intentionally empty)
- `datasets/master/pilot/`: five-paper export, rendered pages and visual review.
- `datasets/manifests/arxiv_enrichment_selected.jsonl` and pilot manifest.
- `logs/arxiv_enrichment_first_attempt.json`, `arxiv_enrichment_recovery.json`,
  `arxiv_enrichment_final_upsert.json`, `arxiv_enrichment_verification.json`.

## Remaining work and reproducibility

The five-paper visual review covers ten sample pages. It does not certify every
paragraph or object in 150 PDFs. Figures still lack verified raster/visual
interpretations; some captions are truncated; table headers/units and merged
cells need verification. Reference segmentation and author/title parsing remain
partial, especially author-year bibliographies. OCR, mathematical reconstruction,
verified publication/license enrichment and research metadata annotation are
pending. Docling/GROBID are not configured. No human QA overlaps this selection.
All these limits are represented in the data; none were filled with guesses.

See [the workflow guide](MASTER_DATASET_V2_ENRICHMENT.md) for the full schema,
quality-review criteria, exact PowerShell rerun commands, synthetic provider
contract and pgAdmin queries. The usual rerun is:

```powershell
.\.venv\Scripts\python.exe scripts/enrich_arxiv.py --import-db
.\.venv\Scripts\python.exe -m pytest -q
```

The generated audit and selected manifest are the authoritative per-paper report.
