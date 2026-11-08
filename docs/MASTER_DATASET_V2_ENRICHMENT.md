# Existing arXiv corpus enrichment — 25 October 2026

This workflow upgrades the 150 **already imported** arXiv papers. It does not
download PDFs, discover candidates, import candidate registries, modify `.env`,
or generate QA by default. The resulting dataset remains **partial** scientific
extraction; presence of a field is never evidence of completeness.

## Phase 0: verified before implementation

The actual repository has `app/ingestion`, `app/storage/postgres.py`, the arXiv
collection/normalization/audit scripts, processed JSONL, candidate manifests and
`storage/pdfs/arxiv`. There is no standalone migrations directory: existing
additive migration statements live in `app/storage/postgres.py`.

Read-only inspection of the configured `researchmemory` PostgreSQL database found:

| Observation | Before enrichment |
| --- | ---: |
| Imported arXiv source records / distinct canonical papers | 150 / 150 |
| Local corresponding PDFs / PDFs opening successfully | 150 / 150 |
| arXiv papers with stored sections / section rows | 150 / 2,763 |
| arXiv papers with metadata only | 0 |
| Papers with figure detections / table detections | 134 / 128 |
| Papers with table cells | 90 |
| Papers with bibliography fragments | 116 |
| Papers with actual QA annotations in the arXiv master | 0 |
| All database papers / source rows / section rows | 2,061 / 2,061 / 16,221 |
| Original arXiv preferred snapshots / PDF-version snapshots | 150 / 150 |
| Current / initial-review metadata candidates | 350 / 349 |

Full text was already stored in `datasets/processed/arxiv_master.jsonl`,
`sections.body`, `paper_master_sources.master_json` and
`paper_master_versions.master_json`. These sources were retained. Original
parser output consisted of page fallback text, caption regex candidates, some
actual table cells, bibliography fragments and unresolved citation markers.
Its length/printability gate established text availability, **not** scientific
completeness. No original record had grounded QA. QASPER normalization discarded
QA content, retaining only a boolean annotation flag; the original parquet files
remain the authoritative source for annotations.

The new selected manifest is derived exclusively from PostgreSQL `paper_sources`
and its canonical IDs. The metadata registries are not used as import lists.

## Contract and provenance

`schemas/master_dataset_v2.schema.json` preserves the old 2.0 contract.
`schemas/master_dataset_v2_enriched.schema.json` defines its 2.1 upgrade;
`validate_master` dispatches to the appropriate validator. Version 2.1 contains:

- Identity, title, abstract, authors, categories, identifiers and publication.
- `full_text: {sections: [...], extraction_status: ...}`. Sections contain stable
  `section_id`, heading, page range and paragraph objects with stable
  `paragraph_id`, exact text and page ranges. Parent headings may have no direct
  paragraphs. A flat `sections` alias supports existing readers.
- Separate figure/table objects with type-specific IDs, partial captions/cells,
  nullable source bbox/section association, table headers/rows/units/raw text,
  and nullable figure image paths/descriptions.
- References with stable IDs, original raw text, nullable metadata, explicit
  DOI/arXiv identity resolution, target canonical IDs and numeric marker contexts.
- QA with original human annotation, dataset split, answer type, original
  evidence, exact alignment status and evidence references; synthetic records
  remain candidates with no training split.
- `research_metadata`, `equations`, provenance and quality. Unavailable research
  metadata/equations stay empty and pending, rather than fabricated.

`year` is the verified journal publication year when supplied; otherwise it is
the arXiv first-submission year. arXiv update dates never supply journal years.
No new journal publication facts were inferred in this run. `has_full_text`
and `extraction_quality` retain their old text-availability meaning for compatibility;
use `full_text.extraction_status` and `quality` for scientific completeness.

IDs hash canonical ID, paper version, parser revision, location and content.
Identical inputs produce identical IDs; parser/version changes deliberately
produce new extraction identities. Provenance records the original record hash,
PDF checksum/path, parser revision, reused-text source and reordered pages.
Original JSONL and PostgreSQL snapshots remain available for raw comparison.
PDF NUL glyphs cannot enter PostgreSQL JSONB: the enrichment represents them by
U+FFFD, records original page offsets/codepoint for reconstruction, and exposes
`quality.unmapped_glyph_count`. No missing mathematical symbol is guessed.

## Parser and five-paper review

Stored text is reused for ordinary pages. The PyMuPDF layout adapter reads font
and bounding-box evidence, joins heading numbers split from labels, and repairs
detected two-column pages using full-width separators and left/right column
bands. This is a heuristic structural parser, with no OCR, Docling or GROBID
service required. All sections and detected objects remain partial.

Five source PDFs were visually inspected on pages 1–2 each: Memory Networks
(`1410.3916`), RAG (`2005.11401`), MemoryBank (`2305.10250`), GraphRAG
(`2404.16130`) and the IEEE two-column scientific-RAG comparison (`2609.38473`).
The initial review found false heading matches and interleaved columns; the
adapter was corrected before acceptance. The final review is recorded in
`datasets/master/pilot/quality_review.json`, including observations and an exact
pilot digest. This approves scaling **partial enrichment**, not completeness.
Rendered evidence is in the pilot directory. It covers 10 sample pages, not
every page in the corpus. Font/numbering variants, header/footer contamination,
caption truncation, figure/table association, merged table cells, bibliography
boundaries and inline abstracts still need specialist/manual review.

No chart values were inferred. Existing real cells remain rows with **unverified
header semantics**, rather than assuming the first row is a valid header.
Obvious prose mentions such as “Figure 1 illustrates ...” are excluded from
object candidates. Some remaining caption candidates may still be prose.
No figure raster extraction, OCR, equation-to-LaTeX conversion or automatic
methods/datasets/metrics/contribution annotation is claimed.

References are **partial raw fragments**; a fragment count is not a verified
bibliography-entry count. Only explicit unambiguous DOI/arXiv identifiers can
resolve a target. Fragments containing multiple identifier mentions remain
unresolved. Numeric citation contexts are labeled marker candidates; there is
no semantic-similarity citation inference or APPLIES/EXTENDS/IMPROVES inference.

## QA and split protection

Local QASPER contains 888 train papers and 281 validation papers, with **zero
identifier matches** to these 150 papers. No test parquet is present. Hence zero
human QA in this master is expected (`not_annotated`), not an extraction failure.
Matching uses arXiv identities, never fuzzy title similarity. Original human
questions/answers/annotations/evidence are retained. Exact evidence alignment
does not grant human verification. Validation/test QA is never training-eligible.

Synthetic generation is disabled by default. An optional explicitly configured
`grounded-json-http` adapter accepts a user-managed service implementing the
documented request/response contract in `app/ingestion/synthetic_qa.py`.
`--generate-qa --qa-provider grounded-json-http --qa-model MODEL --qa-endpoint URL`
is required to call it; an optional token is read from `QA_API_KEY`. The adapter
requests 3–5 diverse candidates, deduplicates questions and accepts only answers
that are exact spans of quoted source evidence. Candidates require further
semantic/human review. The main execution made **no QA/API calls**.

## Storage, retry and preservation

`app/storage/enrichment.py` supplies additive `CREATE TABLE IF NOT EXISTS`
migrations for `paper_enrichments` and `paper_enrichment_history`, with relational
views `enriched_sections`, `enriched_objects`, `enriched_references`, `enriched_qas`.
The current enriched document is stored once in JSONB; views do not copy its
text. History stores the superseded document only when content changes.
The original full text and source mappings remain intact for compatibility.

Imports require an existing exact arXiv source mapping, serialize changes using
an advisory transaction lock, and isolate each paper with a savepoint. Reports
distinguish inserted/updated/unchanged/failed. Content hashes make repeat imports
true no-ops, including `updated_at`. Extraction arrays are replaced as units
instead of blindly appended. Richer complete data, missing metadata, prior
resolved references and human verified annotations are preserved; referenced
old paragraphs are retained when needed. Older documents remain in history.
Legacy table counts and content fingerprints are checked before committing.

## Outputs and rerun commands

Run from PowerShell in `D:\ResearchMemory`:

```powershell
# Already executed: five-paper pilot; output stays separate from the full export.
.\.venv\Scripts\python.exe scripts/enrich_arxiv.py --limit 5

# Full enrichment; requires the accepted matching pilot review.
.\.venv\Scripts\python.exe scripts/enrich_arxiv.py --import-db

# Repeating this is safe: unchanged content is not rewritten.
.\.venv\Scripts\python.exe scripts/enrich_arxiv.py --import-db

# Offline unit tests; PostgreSQL tests skip without the separate test database.
.\.venv\Scripts\python.exe -m pytest -q
```

`--import-db` is optional; without it the script exports only and performs
read-only database selection/auditing. `--limit 5` writes under `pilot/` and never
overwrites the full export. Negative/zero limits are rejected. If a changed
parser or input invalidates the pilot digest, review the new pilot before scaling.
No tests create, clear or reset the application database. Optional PostgreSQL
tests refuse any database name other than `researchmemory_test` and roll back.

Outputs:

- `datasets/master/arxiv_master_v2.jsonl`
- `datasets/master/arxiv_enrichment_audit.json` (all 150 per-paper summaries)
- `datasets/master/arxiv_enrichment_failures.jsonl`
- `datasets/master/qa_candidates.jsonl`
- `datasets/master/qa_verified.jsonl`
- `datasets/master/qa_human_annotated.jsonl`
- `datasets/manifests/arxiv_enrichment_selected.jsonl`
- `datasets/master/pilot/` (five-paper output and visual review evidence)

Final execution counts, idempotency results, tests and a real example are recorded
in `docs/MASTER_DATASET_V2_RESULTS.md` and the machine-readable audit. Empty QA
exports are intentional. Large local datasets are ignored by Git.

## pgAdmin checks

Connect to the configured application database and use Query Tool:

```sql
SELECT count(*) FROM paper_enrichments;
SELECT document->'full_text'->>'extraction_status' AS status, count(*)
FROM paper_enrichments GROUP BY 1;
SELECT object_type, count(DISTINCT canonical_id) AS papers, count(*) AS candidates
FROM enriched_objects GROUP BY object_type;
SELECT count(DISTINCT canonical_id) AS papers, count(*) AS fragments,
       count(*) FILTER (WHERE target_paper_id IS NOT NULL) AS resolved
FROM enriched_references;
SELECT dataset_split, count(*) FROM enriched_qas GROUP BY dataset_split;
-- Any future training export must explicitly select eligible train QA:
SELECT * FROM enriched_qas
WHERE dataset_split='train' AND (qa_json->>'training_eligible')::boolean;
SELECT canonical_id, section_id, heading, paragraphs
FROM enriched_sections WHERE canonical_id='arxiv:2305.10250';
SELECT document FROM paper_enrichments WHERE canonical_id='arxiv:2305.10250';
SELECT count(*) FROM paper_enrichment_history;
```

Do not interpret zero complete documents as a missing export: it accurately
reflects the remaining scientific-structure verification work.
