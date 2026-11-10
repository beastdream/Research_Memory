#!/usr/bin/env python3
"""Import and unpack benchmark results downloaded from Google Colab.

Extracts colab_benchmark_results.zip into D:\ResearchMemory root:
- reports/retrieval_benchmark_report.json
- reports/retrieval_benchmark_report.md
- datasets/sciencedirect/benchmark/embeddings/...
"""
import argparse
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ZIP = ROOT / "colab_benchmark_results.zip"

def unpack_bundle(zip_path: Path):
    if not zip_path.exists():
        print(f"[ERROR] Result zip file not found at: {zip_path}", file=sys.stderr)
        print("Please place the downloaded 'colab_benchmark_results.zip' into D:\\ResearchMemory\\", file=sys.stderr)
        return 1

    print(f"[1/2] Unpacking results from: {zip_path} ...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        file_list = zf.namelist()
        for f in file_list:
            print(f"  -> Extracting: {f}")
            zf.extract(f, path=ROOT)

    print("[2/2] Import completed successfully!")
    print(f"  + Reports updated at: {ROOT / 'reports'}")
    print(f"  + Precomputed embeddings available at: {ROOT / 'datasets/sciencedirect/benchmark/embeddings'}")
    print("\nYou can now run benchmark locally in <1 second with precomputed embeddings:")
    print("  python scripts/benchmark_biencoders.py --eval-scope candidate_pool --device cpu")
    return 0

def main():
    parser = argparse.ArgumentParser(description="Import Colab Benchmark Results")
    parser.add_argument("--zip-file", type=Path, default=DEFAULT_ZIP, help="Path to downloaded zip file")
    args = parser.parse_args()
    return unpack_bundle(args.zip_file)

if __name__ == "__main__":
    sys.exit(main())

