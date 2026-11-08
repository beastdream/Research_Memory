"""Independent arXiv workflow stages. Run from Windows PowerShell."""
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.ingestion.arxiv_pipeline import discover, download, normalize, audit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['discover', 'download', 'normalize', 'import', 'audit'])
    parser.add_argument('--target', type=int, choices=range(100, 201), default=150, metavar='100..200')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--max-pdf-mb', type=float, default=40)
    parser.add_argument('--max-total-download-mb', type=float, default=2000)
    parser.add_argument('--reviewed', action='store_true', help='Attest candidate CSV reviewed after dry-run')
    args = parser.parse_args(argv)
    if (args.limit is not None and args.limit < 1) or args.max_pdf_mb <= 0 or args.max_total_download_mb <= 0:
        parser.error('Limits must be positive')
    try:
        common = dict(dry_run=args.dry_run, resume=args.resume, limit=args.limit)
        if args.stage == 'discover':
            result = discover(ROOT, target=args.target, **common)
        elif args.stage == 'download':
            result = download(ROOT, reviewed=args.reviewed, max_pdf_mb=args.max_pdf_mb,
                              max_total_download_mb=args.max_total_download_mb, **common)
        elif args.stage == 'normalize':
            result = normalize(ROOT, **common)
        elif args.stage == 'audit':
            result = audit(ROOT, target=args.target)
        else:
            from scripts.import_db import import_files
            result = import_files(only='arxiv_master.jsonl', dry_run=args.dry_run, limit=args.limit)
        print(json.dumps(result, ensure_ascii=True, indent=2))
    except Exception as exc:
        # Connection exceptions can include credentials/DSNs; never print arbitrary exception messages.
        print('arXiv stage failed:', type(exc).__name__, file=sys.stderr)
        if isinstance(exc, ValueError):
            print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
