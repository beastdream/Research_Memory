# Repository audit — 25 October 2026

The repository is a data-foundation prototype, not a complete research agent.
The selected arXiv master has 150 papers; the candidate registry is not its
selection authority. PostgreSQL `paper_sources` and the selected manifest define
the audited set. No external document or user communication tools were needed.

| Area | Current files | Finding / action |
| --- | --- | --- |
| Project instructions | README_WINDOWS.md; POSTGRES_SETUP_WINDOWS.md; docs/ARXIV_INGESTION.md; docs/MASTER_DATASET_V2_ENRICHMENT.md; earlier results | Earlier results correctly called scientific extraction partial, but did not establish per-paper retrieval readiness. New audit separates the gates. README's obsolete SQLite application-DB check was corrected. |
| Collection | scripts/download_data.py; scripts/collect_arxiv.py; app/ingestion/arxiv_client.py; arxiv_pipeline.py; arxiv_index.py | arXiv metadata candidates and reviewed download selection are distinct. Existing valid, version-matched PDFs can be reused. DOI/arXiv identities, checksum reuse and held-out IDs are explicit; title similarity is not identity evidence. No new paper/PDF collection occurred. |
| Normalization | scripts/normalize_data.py; normalize_arxiv.py; app/ingestion/normalize.py; master_schema.py | SciFact is abstract-only. QASPER normalized records lose QA annotations; raw train/validation Parquet remains authoritative and can now be inspected directly. unarXive is one preview record, not a full corpus. |
| PDF extraction | app/ingestion/arxiv_parser.py; enrichment.py | Original full-text gate was length/printability availability only. Page-word comparisons and source checksums are now separate. Fixed abstract/body loss, additional heading recognition, multi-line captions and missed caption candidates. Raw fallback table grids are explicitly partial. No OCR, equation reconstruction, chart interpretation or verified figure image segmentation is claimed. |
| Schemas | schemas/master_dataset_v2.schema.json; master_dataset_v2_enriched.schema.json | 2.0 and 2.1 contracts coexist. Enriched validation now also checks canonical identity, date formats and paragraph page bounds. Schema validity does not establish scientific completeness. |
| Database | app/storage/postgres.py; enrichment.py; scripts/init_db.py; import_db.py; check_db.py | Existing additive schema/history storage reused. Read-only audit and inspector transactions verified; production test DB isolation retained. Fixed stale section links in retained reference contexts and objects. Legacy data fingerprints remain unchanged. |
| QAs / synthetic adapter | enrichment.py:qasper_annotations/attach_qasper; synthetic_qa.py | Exact identifiers only, original annotations/splits retained when matched. None of 888 local QASPER train or 281 validation IDs matches these 150 papers. Optional synthetic adapter remains off and was not called. |
| Existing audit/import scripts | audit_arxiv.py; audit_fulltext.py; enrich_arxiv.py; import_db.py | Existing scripts remain available. Enrichment export is refreshed from authoritative merged database documents. A final rerun returns 150 unchanged and no errors. |
| New inspection | app/dataset_inspector.py; app/ui/dataset_inspector.py; scripts/inspect_master_dataset.py | Read-only file/DB adapters, title/DOI/arXiv search, filters, full-record CLI, eight UI tabs, source-page rendering and raw/structured content. AppTest covers local, live PostgreSQL and raw QASPER inputs. |
| New audit | app/master_audit.py; scripts/audit_master_dataset.py; inventory_datasets.py | All selected documents and accessible PDF pages compared. Per-paper decisions, issues and database/export consistency exported in CSV/JSON/Markdown/JSONL. Source inventory hashes and schema checks cover master examples, pilot, original and preferred exports as well as other local JSON/JSONL. |
| Retrieval | app/retrieval_corpus.py; scripts/build_retrieval_corpus.py; validate_retrieval_corpus.py | Real tokenizer, deterministic section/paragraph-aware windows, exact source links, atomic tables/captions, derived indexed storage and exact-evidence alignment/export gates. No embedding model, vector service or fine-tuning was invoked. |
| Tests | tests/test_pipeline.py; test_arxiv_pipeline.py; test_enrichment.py; test_postgres_integration.py; test_dataset_tools.py | Offline tests cover normalization, identity, raw extraction, conservative merges, source evidence, leakage controls, Unicode and deterministic corpus/index replacement. PostgreSQL tests require a separate test DB; none writes test fixtures into the application database. |
| Configuration | requirements.txt; .env.example; pytest.ini; .gitignore | Streamlit/tiktoken dependencies added; generated corpus/tokenizer cache ignored. `.env` was loaded only by the existing connection helper; its contents and credentials were never printed or edited. |

All selected master papers passed schema validation. Scientific extraction is
still partial for all 150. Provisional retrieval readiness improved from 128 to
147. The exact three blockers and five unmatched caption candidates are retained
in `master_dataset_audit.json`, not hidden by aggregate counts.

There is no independent migrations directory; additive statements remain in the
existing storage modules. No alternate app persistence backend was introduced:
SQLite contains only the rebuildable retrieval index. Large local datasets are
generated artifacts, not source-controlled examples. Historical documentation
and the original MemoryBank example are distinguished from current live results.

See `docs/DATASET_AUDIT_AND_INSPECTOR.md` for execution commands, five-paper
inspection examples, current statistics, output paths and remaining human review.
