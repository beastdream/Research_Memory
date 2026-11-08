"""Read-only JSON/JSONL/PostgreSQL inspector; use --show ID for full record."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.dataset_inspector import DEFAULT, load_file, load_postgres, search


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=DEFAULT)
    parser.add_argument('--postgres', action='store_true')
    parser.add_argument('--search', default='')
    parser.add_argument('--year', type=int)
    parser.add_argument('--source', default='')
    parser.add_argument('--topic', default='')
    parser.add_argument('--show', help='Exact canonical ID; outputs complete source document')
    args = parser.parse_args()
    try:
        papers = load_postgres() if args.postgres else load_file(args.input)
        papers = search(papers, args.search, args.year, args.source, args.topic)
        if args.show:
            papers = [p for p in papers if p['canonical_id'] == args.show]
            if not papers:
                parser.exit(1, 'Paper not found\n')
            print(json.dumps(papers[0], ensure_ascii=True, indent=2))
        else:
            for p in papers:
                print(json.dumps({k: p.get(k) for k in ('canonical_id', 'title', 'year', 'source')}, ensure_ascii=True))
            print(f'{len(papers)} papers', file=sys.stderr)
    except Exception as error:
        parser.exit(1, 'Read failed: '+type(error).__name__+'\n')


if __name__ == '__main__':
    main()
