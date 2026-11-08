# ResearchMemory - Week 1 Windows Starter

This folder provides **data foundation only**, not a finished QA or long-term-memory agent.

The current [dataset audit, inspector and retrieval corpus guide](docs/DATASET_AUDIT_AND_INSPECTOR.md)
describes per-paper readiness, source verification, remaining extraction limits and PowerShell commands.

For offline enrichment of the existing 150 imported papers, see [Master Dataset v2 enrichment](docs/MASTER_DATASET_V2_ENRICHMENT.md) and [verified results](docs/MASTER_DATASET_V2_RESULTS.md).

The additive arXiv Master Dataset v2 workflow is documented in [docs/ARXIV_INGESTION.md](docs/ARXIV_INGESTION.md). It provides reviewable discovery, bounded sequential downloads, PDF extraction, JSON Schema validation, PostgreSQL import and quality audits without rewriting existing source datasets.

## Requirements

- Windows 10/11, Python 3.11 x64, PowerShell, Internet access
- An existing drive such as `D:` with free storage
- Open PowerShell at this repository folder

## PowerShell commands (run from project root)

```powershell
Set-Location D:\ResearchMemory
py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
.\scripts\set_env.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pytest -q
```

### Day 2 - datasets

```powershell
python scripts/download_data.py
python scripts/inspect_data.py
```

Optional (15 MB LongMemEval Oracle; reserve for later memory evaluation):

```powershell
python scripts/download_data.py --with-longmemeval
```

The download script intentionally gets only unarXive's 53KB preview, not the 105GB archives; it downloads QASPER train and validation Parquet and the official SciFact tar.gz.

### Day 3 - Normalize to common JSONL

```powershell
python scripts/normalize_data.py --qasper-limit 120 --scifact-limit 100
Get-ChildItem datasets\processed
Get-Content datasets\processed\qasper_train.jsonl -TotalCount 1
```

Only QASPER train articles and SciFact corpus are imported into the prototype's retrieval storage. QASPER validation labels and SciFact claim labels must remain in evaluation data, not embedded into the RAG index.

### Day 4 - PDF + OpenAlex

Copy 1-2 PDFs (with permission, digital text preferable) to `storage\pdfs\`.

```powershell
python scripts/normalize_data.py
# Recommended: create a free key at https://openalex.org/settings/api
# Set the key ONLY in this terminal (do not paste it into public source code)
$env:OPENALEX_API_KEY = Read-Host "Enter your OpenAlex API key"
python scripts/search_openalex.py "long term memory language agents" --limit 20
```

OpenAlex results contain metadata and possibly abstracts **only**; they are not fulltext unless you separately obtain and ingest the paper legitimately. Re-running `normalize_data.py` overwrites the prepared corpus files; this is intentional. It does not delete raw downloads.

### Days 5-6 - Database, idempotent import, search

```powershell
python scripts/init_db.py
python scripts/import_db.py
python scripts/check_db.py
python scripts/check_db.py --search "memory"
python scripts/import_db.py
python scripts/check_db.py
```

Re-running import should not increase paper count when data haven't changed.

### Day 7 - Validation

```powershell
python -m pytest -q
python scripts/check_db.py
python scripts/audit_master_dataset.py
```

## Design decisions

- `paper_sources` preserves dataset provenance and split.
- `sections` keeps both QASPER/unarXive versions if a paper overlaps across sources.
- SciFact corpus includes abstracts; it is not a full paper corpus.
- `sessions`, `messages`, `memory_events` are placeholders for Weeks 4-8, not completed long-term memory logic.
- Stable arXiv identifiers allow cross-source dedup when recognized; DOI crosswalk and fuzzy title dedup will need subsequent refinement.
- Protect QASPER validation/test and LongMemEval from data leakage.
- Original dataset files should not be committed to Git.

## Common Windows issues

- `py -3.11` not found -> install Python 3.11 x64 and reopen PowerShell.
- PowerShell blocks activation -> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`.
- `code` command not found -> open VS Code through the Start Menu, File -> Open Folder.
- `ModuleNotFoundError` -> activate `.venv` and rerun `python -m pip install -r requirements.txt`.
- Hugging Face timeout -> run `.\scripts\set_env.ps1` and repeat download; downloads are cached.
- `D:` does not exist -> choose a real drive and change the project path in the instructions.
- OpenAlex 401/403/429 -> get a free API key from https://openalex.org/settings/api, set OPENALEX_API_KEY, and observe account usage/rate limits.
- PDF extraction returns no text -> PDF may be scanned; OCR is a later task.
