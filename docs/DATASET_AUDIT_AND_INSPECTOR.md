# Dataset audit, inspector and conditional retrieval corpus

25 October 2026. The full selected corpus is **not scientifically complete**.
Branch A repaired demonstrable extraction defects; Branch B exports only papers
that pass the independent, provisional retrieval gate. No model was trained and
no synthetic QA service was called.

## Verified results

| Measure | Before | After |
| --- | ---: | ---: |
| Live imported / unique selected arXiv papers | 150 / 150 | 150 / 150 |
| Local PDFs opening and matching source checksums | 150 | 150 |
| Metadata ready | 150 | 150 |
| Provisionally retrieval ready | 128 | 147 |
| Papers with recognized section headings | 148 | 149 |
| Papers with figure candidates | 132 | 146 |
| Papers with table candidates | 127 | 137 |
| Papers with some extracted cell arrays | 90 | 131 |
| Papers with bibliography fragments | 147 | 148 |
| Human / synthetic / verified QA in selected corpus | 0 / 0 / 0 | 0 / 0 / 0 |
| Scientifically complete papers | 0 | 0 |

Candidate figures/tables, raw cell grids and reference fragments are **not verified
complete scientific objects**. In particular, fallback tables can split words,
merge several logical rows into one cell or omit headers. Headers and units are
left unknown instead of inferred. Cell-array counts measure availability only.
Five papers still have caption-like source lines not matched to object records;
their exact page/label is in `missing_caption_candidates` in the audit.

Three papers are excluded from the retrieval corpus:

| arXiv ID | Exact blocker |
| --- | --- |
| 2402.02764 | Excessive unmapped scientific glyphs |
| 2601.03258 | Section headings fail the font/structure gate despite visible Roman-numbered headings |
| 2602.00279 | PDF page 26 has less than 80% normalized word coverage |

All other selected papers are individually listed as ready in
`reports/master_dataset_audit.csv` and `.json`. Readiness requires a valid schema
and identity, a matching local PDF checksum, identified headings, valid paragraph
page ranges, mapped text-rich pages, per-page source word coverage and a glyph
quality gate. These checks establish provisional source-traceable retrieval
usability; they cannot establish mathematical correctness, complete reading order,
complete tables, or accurate visual interpretation. QA availability is independent.

## Evidence and verification boundaries

- **Repository:** the inventory parses local dataset JSON/JSONL, checks all master
  records against their schemas/invariants and records file hashes. The enriched
  preferred master, original 2.0 master, examples, pilot, manifests, candidate
  registries, normalized datasets and raw benchmark files are distinguished.
  Candidates are never used as the imported-paper selection.
- **Live PostgreSQL:** 2,061 papers, 2,061 source rows and 16,221 legacy section
  rows; 150 arXiv master snapshots, 150 PDF-version snapshots and 150 enriched
  documents. All 150 preferred exports equal their live enriched documents; all
  150 original JSONL masters equal their stored source snapshots. Read-only
  selection uses `paper_sources.source='arxiv'`.
- **Preservation:** legacy row-content fingerprints are unchanged. Changes use
  the existing additive enrichment/history storage. A final import reports
  150 unchanged, zero updates/inserts/failures. The 735 history rows are retained
  prior revisions, not duplicate papers. No DROP/TRUNCATE or destructive
  production test was run.
- **Export-only derived content:** chunks, the query index and alignment/training
  JSONL live under `datasets/retrieval_corpus`; they are not presented as imported
  PostgreSQL chunks. PostgreSQL remains the authoritative paper store. SQLite is
  a replaceable derived corpus index.
- **Visual verification:** the agent compared 14 rendered pages from 11 distinct
  papers across five topic groups, with paper lengths from 3 to 92 pages.
  See `reports/manual_pdf_review.md`, its JSON and `reports/visual`.
  This is an agent visual review, not independent human annotation or a review
  of every page. All accessible PDFs also underwent automated page comparisons.
- **Unknown:** equation semantics, OCR needs, reading order on every page,
  bibliography segmentation/title/author parsing, table headers/units and figure
  image associations still need specialist review. No journal date or DOI was
  fabricated. License information remains unknown where absent.

## MemoryBank: historical example versus current master

The original `docs/examples/memorybank_enriched_v2.json` is preserved. Its complete
audit is `reports/memorybank_example_audit.json`; the current export is separately
audited in `reports/memorybank_current_audit.json`.

Both are schema-valid and have necessary arXiv publication metadata, with 13
sections, 157 paragraphs, mapped text on all 11 PDF pages, 22 partial reference
fragments and zero QA. Identity is `arxiv:2305.10250`, version v3, first submitted
17 May 2023; authors/title/abstract are retained from source metadata. DOI and
journal publication facts are unavailable. The update date is not a journal date.

The historical example has two figure candidates and three table candidates,
including a prose mention incorrectly recorded as Table 2. Its Figure 1 caption
is truncated and Figures 3/4 on PDF page 7 are missing. The current master has
four figure candidates and two table candidates, longer PDF-block captions and
partial table grids with source bounding boxes. It still has no verified figure
image crops, complete semantic table structure, or separate equation extraction.

The example and current master are usable for provisional retrieval, structurally
extracted only by heuristics, and **not ready for retriever fine-tuning or LLM
SFT**. Raw references preserve unresolved text; explicit identifiers alone resolve
targets. Citation contexts remain candidates, not semantic research relationships.
Scientific claims about simulated dialogue QA inside the paper are paper text,
not training annotations for this dataset.

## Extraction and merge fixes

`app/ingestion/enrichment.py` now preserves PDF abstract wording in a separate
section rather than dropping all text until the next recognized heading. It
recognizes additional heading patterns, rejects a false prose-caption pattern,
recovers multi-line PDF caption blocks and adds previously missed caption
candidates. `scripts/repair_caption_objects.py` supplements existing documents
without changing paragraph identities, and uses an optional ruled-row/text-column
PyMuPDF fallback to retain raw table grids. Failed table extraction retains the
caption and partial status. No PDFs are downloaded.

The storage merge now preserves exact historical paragraph evidence for retained
resolved-reference contexts, and clears/remaps stale object-to-section links.
This repaired 11 initially rejected merges; all imports subsequently succeeded.
Canonical identity, date formats and paragraph page bounds are also validated.
No previous human-verified evidence is downgraded. Prior source snapshots remain.

## Run the inspector in PowerShell

```powershell
Set-Location D:\ResearchMemory
.\.venv\Scripts\python.exe -m streamlit run app/ui/dataset_inspector.py
```

Open the local URL printed by Streamlit. The sidebar selects a JSON/JSONL file,
raw QASPER Parquet, or PostgreSQL. Search title/DOI/arXiv ID and filter year,
source and topic. The eight tabs show metadata, abstract, expandable full-text
sections/paragraphs, figures, tables, references, QAs and quality audit. The full
text tab can render a chosen original PDF page and show the original raw master
extraction. Figures show an image when an image path exists; captions/status are
shown otherwise. Tables display raw cell grids and explicit header/unit status.
Every PostgreSQL read transaction starts with `SET TRANSACTION READ ONLY`.
The UI has no source-data editing controls.

Examples of input paths:

```text
datasets/master/arxiv_master_v2.jsonl
docs/examples/memorybank_enriched_v2.json
datasets/processed/qasper_train.jsonl
datasets/raw/qasper/qasper/train/0000.parquet
datasets/raw/qasper/qasper/validation/0000.parquet
datasets/processed/scifact_corpus.jsonl
datasets/processed/unarxive_sample.jsonl
datasets/raw/unarxive/sample/preview.jsonl
```

QASPER has 888 local train papers and 281 validation papers, with zero exact
identifier matches to the selected arXiv corpus. No test Parquet is present.
The older normalized QASPER file discarded QA annotations; use the raw Parquet
path to view original human questions/answers/evidence. No labels from that
benchmark were merged by title similarity. SciFact is an abstract corpus;
its claim labels are separate evaluation data. unarXive here is one preview
paper, not the full dataset. LongMemEval remains reserved evaluation data.

CLI examples (complete paper output uses `--show`):

```powershell
.\.venv\Scripts\python.exe scripts/inspect_master_dataset.py
.\.venv\Scripts\python.exe scripts/inspect_master_dataset.py --search "2305.10250"
.\.venv\Scripts\python.exe scripts/inspect_master_dataset.py --source arxiv --topic memory --year 2023
.\.venv\Scripts\python.exe scripts/inspect_master_dataset.py --postgres --show arxiv:2305.10250
```

To inspect five other real papers, search the following IDs in the UI or export
their complete records using this loop:

```powershell
$paperIds = @('1410.3916','2005.11401','2404.16130','2402.01622','2105.07624')
foreach ($paperId in $paperIds) {
    .\.venv\Scripts\python.exe scripts/inspect_master_dataset.py --show "arxiv:$paperId" |
        Set-Content -Encoding UTF8 "reports\inspect_$paperId.json"
}
```

These are Memory Networks, RAG, GraphRAG, TravelPlanner and TAT-QA.

## Retrieval corpus and training boundaries

The corpus uses the real cached `cl100k_base` tokenizer, a 400-token target and
60-token overlap. It prefers paragraph boundaries within sections, retains
paragraph IDs and normalized section offsets, and records actual source pages
where known. NFC/whitespace normalization preserves scientific characters,
citations and numeric values; no speculative equation reconstruction or
dehyphenation is performed. Raw PDF character spans remain null. Metadata-only
abstract chunks have null page numbers.

Abstract/text, figure-caption and table chunks are included. No separate equation
chunks are invented where equation extraction is unavailable. Tables/captions
remain atomic; oversized objects are flagged instead of losing cells, headers
or units through arbitrary slicing. Unclassified source text is retained;
bibliography and historical evidence-only sections are excluded from ordinary
retrieval chunks. Stable IDs include content, paper/source, location, parser,
tokenizer/chunker version and window settings.

The current output contains **8,919 chunks from 147 papers**: 150 abstract chunks
(some abstracts need multiple windows), 6,809 text chunks, 870 figure-caption
chunks and 1,090 partial table chunks. Median length is 300 tokens, p95 400,
maximum 3,315; 379 atomic objects exceed 400. This is an exploratory retrieval
corpus from partial extraction, not a gold scientific corpus.

`corpus.sqlite` indexes paper ID, year, section, content type and source ID. It
is built separately and replaced after closing the Windows file handle.

```powershell
.\.venv\Scripts\python.exe scripts/build_retrieval_corpus.py --paper-id arxiv:2305.10250 --content-type table
# Supply an actual ID from chunks.jsonl:
.\.venv\Scripts\python.exe scripts/build_retrieval_corpus.py --chunk-id "chunk-..."
```

QA alignment requires exact source evidence references and interval coverage,
supports multiple chunks, and preserves source/split. Similarity does not create
gold QA. Human annotations with exact alignment remain candidates until verified.
Training exports require human-verified, human-annotated, eligible train labels;
validation/test and synthetic candidates are excluded. Negative lists stay empty
until a justified negative-mining process is supplied. No QA answer is injected
into corpus content. Because no selected paper has QA, alignment and both
training exports are intentionally empty.

## Reproduce and validate

Dependencies are in `requirements.txt`. The tokenizer vocabulary was downloaded
once to `storage/tokenizers`; subsequent corpus builds and tests used that cache.
On another machine the first tokenizer initialization requires network access.

```powershell
.\.venv\Scripts\python.exe scripts/inventory_datasets.py
.\.venv\Scripts\python.exe scripts/audit_master_dataset.py --output reports\new_baseline
# Repairs are optional reruns; these two commands write enriched PostgreSQL documents.
.\.venv\Scripts\python.exe scripts/enrich_arxiv.py --import-db
.\.venv\Scripts\python.exe scripts/repair_caption_objects.py
.\.venv\Scripts\python.exe scripts/audit_master_dataset.py
.\.venv\Scripts\python.exe scripts/build_retrieval_corpus.py
.\.venv\Scripts\python.exe scripts/validate_retrieval_corpus.py
.\.venv\Scripts\python.exe -m pytest -q
```

Do not overwrite the retained `reports/before` baseline when reproducing the
final audit; choose another output directory for a new comparison. The audit
itself is read-only against PostgreSQL and continues from the selected offline
manifest if the database cannot connect, marking live verification blocked.

Offline pytest: **74 passed, 4 skipped**. The four skipped tests require an
explicitly configured isolated `researchmemory_test` database. Streamlit AppTest
checks the UI, and corpus validation rebuilds deterministic chunks, rechecks real
token counts, source links, evidence IDs, SQLite integrity and index contents.
These are engineering checks, not proof of complete scientific interpretation.

## Delivered artifacts and next review

- `reports/master_dataset_audit.csv`, `.json`, `.md`: all 150 per-paper decisions,
  exact issues, live database consistency and per-page coverage evidence.
- `reports/master_dataset_failures.jsonl`: quality issues, including partial
  extraction; this is broader than the three retrieval exclusions.
- `reports/before/`: retained initial audit for comparison.
- `reports/dataset_inventory.json`: source/export/schema inventory and hashes.
- `reports/memorybank_example_audit.json`, `memorybank_current_audit.json`:
  separate historical-example and current-master findings.
- `reports/manual_pdf_review.md`, `.json`, `visual/`: 11-paper source review.
- `reports/caption_repair.json`, `inspector_validation.json`,
  `corpus_validation.json`: repair and validation results.
- `datasets/master/arxiv_master_v2.jsonl`: current enriched preferred documents;
  original `datasets/processed/arxiv_master.jsonl` remains intact.
- `datasets/retrieval_corpus/chunks.jsonl`, `corpus.sqlite`, `manifest.json`.
- `datasets/retrieval_corpus/qa_evidence_alignment.jsonl`,
  `retriever_train.jsonl`, `llm_train.jsonl`: currently empty by design.

Next, review the three excluded papers and the five unmatched caption candidates,
then validate table structure/headers, figure associations, bibliography boundaries
and scientific equations against the PDFs. Evaluate retrieval on a separately
held-out, evidence-annotated set before using this corpus for training. Obtain
grounded human QA labels with explicit splits before exporting training data.
