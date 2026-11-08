# Implementation report — 24 October 2026

The existing flat JSONL normalizers, source-specific PostgreSQL sections, paper provenance and import allowlist were extended in place. No model training, vector database or relationship graph was added. The original source files were preserved; final verified counts are recorded below after the extraction audit.

## Verified delivery

| Check | Result |
| --- | --- |
| Official arXiv metadata candidates | 350 |
| Selected unique papers | 150 |
| Verified final PDFs | 150, totaling 306.29 MiB |
| Failed transfer attempts | 1 oversized CVQA PDF; replaced by reviewed scientific-QA reserve |
| Existing duplicates detected | 2, excluded from the new dataset |
| Schema-valid records passing the full-text gate | 150 |
| Abstract-only / insufficient-text records | 0 / 0 |
| Invalid records / failed PDF integrity checks | 0 / 0 |
| Papers with figure captions/detections | 134, partial extraction |
| Papers with table captions/detections | 128, partial extraction |
| Papers with extracted table cells | 90, partial extraction |
| Papers with bibliography fragments | 116, partial/unresolved |
| Papers containing ground-truth QAs | 0 |
| Topic counts: memory / retrieval / agents / graph / evaluation | 40 / 35 / 30 / 25 / 20 |
| Target shortfall | 0 |
| Reserve candidates | 196 |
| Original data/PDF checksums checked and unchanged | 30 / 30 |
| Import dry-run | 150 validated; zero database writes |
| Actual PostgreSQL imports | 150 source records and 150 full-text papers verified |
| Repeat import | Same counts and content; only normal update timestamps may change |
| Existing database rows | All 1,911 papers, 1,911 source rows and 13,458 sections preserved |
| Database totals after import | 2,061 papers; 2,061 source rows; 16,221 sections |
| Master JSONB snapshots | 150 preferred source records and 150 immutable PDF-version records |
| JSONL versus PostgreSQL master records | All 150 exactly equal |
| Pytest | 46 passed, 3 skipped |

Full text means the explicit conservative text-quality gate passed. It does not assert complete scientific structure, perfect reading order or resolved citations. Year distribution spans 2014–2026 and is included in the audit JSON. Master JSONL is 70,126,791 bytes and remains excluded from Git. The real five-paper pilot passed before expansion. A Unicode JSONL line-separator defect discovered in the larger corpus was corrected and regression-tested.

Example excerpt from the downloaded, processed MemoryBank paper (the complete record is the first JSONL record):

```json
{
  "canonical_id": "arxiv:2305.10250",
  "title": "MemoryBank: Enhancing Large Language Models with Long-Term Memory",
  "identifiers": {"arxiv_id": "2305.10250", "arxiv_version": "v3", "doi": null},
  "has_full_text": true,
  "extraction_quality": {"page_count": 11, "word_count": 6918, "structure_status": "page_fallback"}
}
```

This is a display excerpt, not a standalone schema-valid record. A complete, schema-valid real-PDF example is provided in `docs/examples/memgpt_local_pdf_master_v2.json`.

## Files created

- `app/ingestion/arxiv_client.py`: official Atom API, identifier normalization, rate limiting, retry/backoff and bounded PDF transfers.
- `app/ingestion/arxiv_index.py`: exact local/PostgreSQL identity checks, legacy PDF checksums and held-out ID exclusions.
- `app/ingestion/arxiv_parser.py`: page/paragraph extraction, caption/table/reference/citation fallback and full-text quality criteria.
- `app/ingestion/arxiv_pipeline.py`: topic quotas, discovery/ranking, editable manifests, review receipts, resume, normalization and audit.
- `app/ingestion/master_schema.py`: JSON Schema and cross-field validation.
- `schemas/master_dataset_v2.schema.json`: complete machine-readable v2 contract.
- `scripts/collect_arxiv.py`, `scripts/normalize_arxiv.py`, `scripts/audit_arxiv.py`: independent workflow CLI stages.
- `tests/test_arxiv_pipeline.py`: offline Atom/PDF/mocked-transport/storage regression tests.
- `docs/ARXIV_INGESTION.md`: PowerShell pilot/expansion commands, schema/storage rationale, limitations and pgAdmin SQL.
- `docs/examples/memgpt_local_pdf_master_v2.json`: complete validated record extracted from the pre-existing real MemGPT PDF, including its actual PDF abstract; unknown dates/license remain null.
- `docs/ARXIV_IMPLEMENTATION_REPORT.md`: this delivery record.

## Files changed

- `app/ingestion/normalize.py`: uploaded PDF arXiv filename identity, retained SHA256 provenance and conservative full-text flags.
- `app/storage/postgres.py`: additive migration, exact identity reuse, protected metadata/sections/full text, source-level JSONB master records and immutable PDF-version evidence.
- `scripts/import_db.py`: arXiv allowlist entry, validated dry-run, file/limit selection and evaluation exclusions.
- `scripts/init_db.py`: reports the added tables after successful initialization.
- `tests/test_postgres_integration.py`: isolated, rollback-only v2 preservation/idempotency test.
- `requirements.txt`: adds only `jsonschema`.
- `README_WINDOWS.md`: links the arXiv workflow.
- `.gitignore`: also excludes PDFs/partial PDFs globally.

## Generated local data and evidence (ignored by Git)

- `datasets/manifests/arxiv_candidates.csv`: reviewed, editable selection and per-paper status.
- `datasets/raw/arxiv/metadata.json`: actual API metadata, not generated bibliographic content.
- `storage/pdfs/arxiv/`: verified version-named downloads; one oversized partial transfer is retained.
- `datasets/processed/arxiv_master.jsonl`: schema-valid extracted records.
- `datasets/manifests/arxiv_audit.json`: final quality counts, integrity check, year/topic distributions and reserves.
- `datasets/manifests/arxiv_candidate_review.json`: candidate/pilot review and the oversized-paper substitution.
- `datasets/manifests/arxiv_download_dry_run.json`: checksum-scoped review receipt.
- `datasets/manifests/arxiv_normalize_failures.json`: extraction failures, if any.
- `logs/arxiv_before_hashes.json`: checksum inventory of the 30 original data/PDF files.
- `logs/arxiv_postgres_verification.json`: before/after database hashes, two committed import results, preservation checks and exact JSONL/JSONB comparison.
- `logs/verify_arxiv_import.py`: one-off authorized import verification runner, excluded from Git.

## Remaining limitations

After the user started `postgresql-x64-18`, the configured `researchmemory` database at `127.0.0.1:5432` was verified. None of the 150 incoming IDs/DOIs matched an existing paper. The additive migration and two imports committed successfully. All original database row contents were preserved; paper/source/section counts and stored content were unchanged by the second import (normal `updated_at` timestamps excluded from the idempotency comparison). Every stored arXiv master JSONB record exactly matches its input JSONL record. All 30 original data/PDF files also retain their original checksums. Tests requiring the separate `researchmemory_test` database remain skipped because no test database is configured; no integration tests were run against the application database.

PDF extraction uses CPU PyMuPDF page fallback. Scientific sections, complete figure images, reliable two-column ordering, complete bibliography/citation resolution, OCR, and verified journal/license enrichment remain incomplete. Partial extractions are labeled; no QA labels or citation relationships are synthesized.
