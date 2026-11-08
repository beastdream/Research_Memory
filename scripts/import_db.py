"""Import normalized JSONL to PostgreSQL. Repeated imports are safe."""
import json
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.storage.postgres import connect, init_schema, upsert_paper, stats


# Only explicitly approved retrieval corpora; never blindly glob *.jsonl:
# LongMemEval and QASPER validation should not be ingested as knowledge.
INPUTS = ("unarxive_sample.jsonl", "qasper_train.jsonl", "scifact_corpus.jsonl", "uploaded_pdfs.jsonl", "openalex_metadata.jsonl", "arxiv_master.jsonl")


def import_files(only=None, dry_run=False, limit=None):
    if only is not None and only not in INPUTS:
        raise ValueError('Input is outside retrieval import allowlist')
    folder = ROOT / "datasets" / "processed"
    paths = [folder / name for name in INPUTS if (folder / name).is_file() and (only is None or only == name)]
    if not paths:
        raise SystemExit("No processed JSONL. Run python scripts/normalize_data.py first.")

    prepared = []
    for path in paths:
        records = []
        with path.open(encoding='utf-8-sig') as stream:
            for line in stream:
                if line.strip():
                    record = json.loads(line)
                    if path.name == 'arxiv_master.jsonl':
                        from app.ingestion.master_schema import validate_master
                        validate_master(record)
                    records.append(record)
                    if limit is not None and len(records) >= limit:
                        break
        prepared.append((path, records))
    if any(p.name == 'arxiv_master.jsonl' for p, _ in prepared):
        from app.ingestion.arxiv_index import ExistingIndex
        excluded = ExistingIndex.load(ROOT, database=False).excluded
        for path, records in prepared:
            if path.name == 'arxiv_master.jsonl' and any(r['arxiv_id'] in excluded for r in records):
                raise ValueError('Held-out QASPER identities cannot enter retrieval corpus')
    if dry_run:
        return {'validated_for_import': {path.name: len(rows) for path, rows in prepared}, 'database_writes': 0}
    result = {}
    with connect() as db:
        init_schema(db)
        for path, records in prepared:
            count = 0
            for line_num, record in enumerate(records, 1):
                try:
                    upsert_paper(db, record)
                except (ValueError, KeyError, TypeError) as error:
                    raise ValueError(f"Bad paper in {path.name}:{line_num}: {error}") from error
                count += 1
            result[path.name] = count
    return {'committed_upserts': result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', choices=INPUTS)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    try:
        print(import_files(args.only, args.dry_run, args.limit))
    except Exception as exc:
        raise SystemExit('Import failed: ' + type(exc).__name__) from None


if __name__ == "__main__":
    main()
