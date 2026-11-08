"""Normalize ScienceDirect candidate records into Master Dataset format.

Usage:
    python scripts/normalize_sciencedirect.py [--target 150] [--dry-run]
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.collect_sciencedirect import run_normalize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, default=150, choices=range(100, 201))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    result = run_normalize(target=args.target, dry_run=args.dry_run, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
