# arXiv Master Dataset v2

This adds data preparation to the existing ResearchMemory project. It does not train models, build a vector database, or infer paper relationships. Similarity/relevance scores are selection heuristics, never evidence that one paper applies another paper.

## Repository audit and design

The existing pipeline normalizes QASPER train, unarXive, SciFact abstracts, uploaded PDFs and OpenAlex metadata into flat JSONL. PostgreSQL has `papers`, `paper_sources`, and source-specific `sections`; imports use an explicit allowlist. Existing upserts preserve full-text flags but formerly replaced same-source section bodies and shallow-merged metadata. Uploaded PDFs formerly used only a file hash as identity.

The audit found 888 QASPER train records, 1,000 SciFact abstracts, one unarXive sample, 20 OpenAlex records and two uploaded PDFs. These source files remain unchanged, verified against `logs/arxiv_before_hashes.json`. After the user started the local PostgreSQL service, the additive migration and import were verified: 150 arXiv full-text papers imported, 2,061 total papers, all original rows preserved, and a second import left counts/content unchanged. Verification evidence is in `logs/arxiv_postgres_verification.json`; see [the implementation report](ARXIV_IMPLEMENTATION_REPORT.md).

New code reuses requests, PyMuPDF, pyarrow and psycopg. `jsonschema` is the only new dependency. Master schema: [master_dataset_v2.schema.json](../schemas/master_dataset_v2.schema.json). The full validated real-local-PDF example is [memgpt_local_pdf_master_v2.json](examples/memgpt_local_pdf_master_v2.json).

## PowerShell workflow

Run from `D:\ResearchMemory`, using the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/collect_arxiv.py discover --target 150 --dry-run
.\.venv\Scripts\python.exe scripts/collect_arxiv.py discover --target 150
# If the manifest already exists, --resume preserves it and all selected-column edits.
```

Discovery requests official Atom metadata, then keeps up to 350 unique candidates. It combines title/abstract phrase relevance, AI categories, foundational ID lookups and a year-diversity penalty. Foundational-paper metadata is fetched, never invented. Searches span historical papers and current research. Target 150 assigns 40 memory, 35 retrieval, 30 agents, 25 graph and 20 evaluation papers. Targets 100–200 use largest-remainder proportional allocation. Each candidate has one topic and counts once. A topic shortage is reported rather than filled with unrelated papers.

Open `datasets/manifests/arxiv_candidates.csv` in a spreadsheet editor, inspect titles/topics, and edit `selected` to `true`/`false`. Close the editor before running downloads. CSV handles commas/quotes in titles. The cached API metadata remains authoritative for bibliographic fields and URLs. Selection does not assert research quality or citation relationships.

Five-paper pilot (keep the full 150-paper selection; limit only execution):

```powershell
.\.venv\Scripts\python.exe scripts/collect_arxiv.py download --dry-run --limit 5
.\.venv\Scripts\python.exe scripts/collect_arxiv.py download --reviewed --limit 5 --max-pdf-mb 40 --max-total-download-mb 100
.\.venv\Scripts\python.exe scripts/normalize_arxiv.py --limit 5 --resume
.\.venv\Scripts\python.exe scripts/audit_arxiv.py --target 150
.\.venv\Scripts\python.exe scripts/collect_arxiv.py import --dry-run --limit 5
```

`--reviewed` attests that the manifest has been reviewed. The dry-run receipt records the manifest checksum and exact permitted IDs. Edits invalidate it; `--resume` cannot bypass review. Repeat the dry-run after edits or increasing the limit.

After checking the pilot, expand to all selected papers:

```powershell
.\.venv\Scripts\python.exe scripts/collect_arxiv.py download --dry-run
.\.venv\Scripts\python.exe scripts/collect_arxiv.py download --reviewed --resume --max-pdf-mb 40 --max-total-download-mb 2000
.\.venv\Scripts\python.exe scripts/normalize_arxiv.py --resume
.\.venv\Scripts\python.exe scripts/audit_arxiv.py --target 150
# Start PostgreSQL first, using Windows Services or an administrator PowerShell:
# Start-Service -Name postgresql-x64-18
.\.venv\Scripts\python.exe scripts/collect_arxiv.py import --dry-run
.\.venv\Scripts\python.exe scripts/collect_arxiv.py import
.\.venv\Scripts\python.exe scripts/audit_arxiv.py --target 150
```

Use the CLI's `normalize`, `audit`, and `import` stages or the wrapper scripts. `scripts/import_db.py --only arxiv_master.jsonl` imports only this dataset. Its default allowlist also includes the older corpora. Do not rerun `normalize_data.py` to prepare arXiv: that older command rewrites its own processed files.

## Safety, deduplication and resume

- Canonical JSONL ID is `arxiv:<base ID>`; version is a separate `vN` field. Modern IDs and legacy archive IDs are supported; legacy filename slashes become `__`.
- Cross-check existing allowlisted JSONL, verified API DOIs, canonical/arXiv IDs, PDF filename IDs, uploaded-PDF SHA256 source IDs, and read-only PostgreSQL identities. Fuzzy titles are never merged. Conflicting identities stop automatic enrichment.
- Existing full text is skipped. Metadata-only matches can receive a PDF. Exact legacy PDF checksums retain the old database key and gain arXiv source provenance; no destructive rekeying/merging occurs.
- QASPER validation/test parquet is read with `columns=['id']` only. Matching candidates are excluded. Evaluation QA labels, SciFact claims and LongMemEval are never loaded into this pipeline. Import checks held-out IDs again. No synthetic QA is produced.
- API and PDF requests are sequential with a process-wide lock, at least three seconds between request starts, timeouts, exponential backoff and Retry-After handling. Run only one collector process at a time. Official documentation: [API manual](https://info.arxiv.org/help/api/user-manual.html), [terms](https://info.arxiv.org/help/api/tou.html).
- PDFs stream into `.pdf.part`, pass `%PDF-` and parser-open checks, then atomically rename. Good PDFs are never overwritten. Interrupted transfers restart that file from byte zero; completed files are reused after checksum validation. This is file-level resume, not HTTP byte-range resume.
- The transfer budget applies per invocation and counts retry bytes; per-PDF limits apply to downloaded files. A failed request leaves no final PDF. An existing invalid final PDF is retained for inspection. Failures are recorded without logging response bodies or credentials.
- The download command attempts the PostgreSQL identity check. If unavailable, local identities remain usable; this does not verify database deduplication. Import always resolves identities again transactionally. `imported_postgres: null` means unverified, never zero confirmed imports.
- Discovery refuses to replace an existing manifest unless `--resume` is used; resume retains manual selections. To create a different reviewed selection, back up the arXiv manifest and cache first. Normalization requires `--resume` when output exists, validates previous records and preserves richer text. No previous source datasets are rewritten.

## Master Dataset v2 contract

Required fields are `schema_version`, `canonical_id`, `source`, `source_id`, `split`, `title`, `abstract`, `year`, `identifiers`, `publication`, `authors`, `categories`, `full_text`, `qas`, `figures_and_tables`, `references`, `has_full_text`, `provenance`, and `extraction_quality`. Compatibility aliases `sections`, `arxiv_id`, `doi`, `url`, `metadata` keep the existing importer usable. `validate_master()` applies Draft 2020-12 schema validation and cross-field identity, page-range, date/year and full-text checks.

`publication` separates arXiv first-submitted/updated dates from journal date, journal year, venue and publication status. `year` means arXiv first-submission year. Journal-reference strings do not establish a verified journal date or venue; these remain null until resolved. A DOI alone does not prove formal publication. Per-paper license is preserved when supplied, otherwise null with `license_status=unknown`; no redistribution permission is inferred.

`full_text` contains `section_id`, `heading`, `text`, paragraphs, `page_start`, `page_end`, and `extraction_status`. The CPU parser currently emits page-fallback sections, not verified scientific sections. Captions are heuristic, marked partial. Reference strings and citation markers are unresolved. No reference title or DOI is fabricated. Figures have no generated pixels; table contents are null unless a parser extracts actual cells.

Full-text acceptance requires at least 800 words, 4,000 characters, three times the abstract's length, two pages with at least 80 words each, and at least 98% printable characters. These are conservative digital-text heuristics, not a guarantee of reading order or semantic completeness. Abstract-only and scanned/image-only PDFs do not qualify. The fallback has no OCR, GROBID or Docling dependency.

Output is `datasets/processed/arxiv_master.jsonl`; only schema-valid records are written. Records with insufficient extracted text are retained with `has_full_text=false`; audit distinguishes them. Extraction failures go into `datasets/manifests/arxiv_normalize_failures.json`.

## PostgreSQL storage and pgAdmin checks

`init_schema()` uses additive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` and `CREATE TABLE IF NOT EXISTS`. Existing tables/keys and `paper_sources` remain. `papers.publication`, `papers.identifiers` are JSONB; `publication_year` is explicitly the journal-publication year. `paper_master_sources.master_json` stores the complete versioned record per canonical/source/source-ID identity, including sections, figures/tables, references, QAs, extraction quality, and provenance. Existing `sections` supports current source-specific text queries.

JSONB preserves heterogeneous extraction detail without forcing partial captions or unresolved references into fake relational entities. The per-source key makes repeated imports idempotent and preserves independent provenance. `paper_master_versions` additionally retains immutable extraction snapshots keyed by PDF SHA256, so a later PDF version cannot erase the older evidence. Text, figures/references, identifiers and PDF provenance in the preferred snapshot always come from the same extraction. Metadata merges retain populated existing scalar values and union arrays; full text cannot be downgraded by metadata-only arrivals. A transaction-scoped advisory lock serializes identity resolution. Existing arXiv/DOI/checksum matches reuse their existing canonical database key; multiple matching legacy keys require manual review.

In pgAdmin, connect to the configured ResearchMemory database, expand Schemas → public → Tables, refresh, and open Query Tool:

```sql
SELECT source, count(*) FROM paper_sources GROUP BY source ORDER BY source;
SELECT count(DISTINCT canonical_id) AS arxiv_imported
FROM paper_sources WHERE source='arxiv';
SELECT p.canonical_id, p.title, p.has_full_text,
       p.publication->>'arxiv_first_submitted_date' AS arxiv_submitted,
       p.publication_year AS journal_year
FROM papers p JOIN paper_sources s USING(canonical_id)
WHERE s.source='arxiv' ORDER BY p.title;
SELECT canonical_id,
       jsonb_array_length(master_json->'full_text') AS pages_or_sections,
       master_json->'extraction_quality' AS extraction,
       master_json->'provenance' AS provenance
FROM paper_master_sources WHERE source='arxiv';
SELECT canonical_id, source, source_id, dataset_split, count(*)
FROM paper_sources GROUP BY canonical_id, source, source_id, dataset_split
HAVING count(*) > 1;
```

Repeat the arXiv import and confirm counts stay fixed. This verification must actually run before claiming successful import. No production database integration tests are run automatically.

## Tests and audit

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Offline tests cover Atom parsing, identifier/version/DOI normalization, exact evidence deduplication, conflicts, retries, Retry-After, invalid/oversized PDFs, resume, preservation, schema/full-text gates, review receipts and evaluation-ID isolation. PostgreSQL tests require explicitly configured `TEST_DATABASE_URL`, verify the database name is exactly `researchmemory_test`, and roll back all changes. They are skipped otherwise.

`arxiv_audit.json` reports candidates, selection/topic shortages, duplicates, download success/failure, full text versus insufficient text, figures/tables/references/QAs, year/topic distributions, PDF integrity, imported count when verifiable, reserve candidates and target shortfall. A selected or downloaded metadata record never counts as successful full text. If short, select reviewed reserve candidates, repeat download dry-run, download and normalize with `--resume`.

Known limits: relevance ranking still benefits from domain review; page fallback cannot recover reliable scientific section boundaries or all two-column reading order. Caption/reference recognition is heuristic. Reference resolution, complete figure assets, OCR, GPU parsers, verified journal metadata/license enrichment and relationship graphs are not implemented. Database migration/import and integration verification depend on an available configured PostgreSQL service.
