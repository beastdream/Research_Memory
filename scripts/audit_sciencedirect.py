"""Audit ScienceDirect Master Dataset quality, verified DOIs, schemas, and rights.

Usage:
    python scripts/audit_sciencedirect.py [--target 150]
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.collect_sciencedirect import run_audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, default=150)
    args = parser.parse_args()

    result = run_audit(target=args.target)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
