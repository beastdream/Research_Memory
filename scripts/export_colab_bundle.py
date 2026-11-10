#!/usr/bin/env python3
"""Export benchmark files to a single ZIP bundle for Google Colab execution.

Creates: colab_benchmark_bundle.zip containing:
- scripts/benchmark_biencoders.py
- datasets/sciencedirect/benchmark/queries.jsonl
- datasets/sciencedirect/benchmark/qrels.jsonl
- datasets/sciencedirect/retrieval/corpus_chunks.jsonl
"""
import zipfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ZIP = ROOT / "colab_benchmark_bundle.zip"

FILES_TO_PACK = [
    Path("scripts/benchmark_biencoders.py"),
    Path("datasets/sciencedirect/benchmark/queries.jsonl"),
    Path("datasets/sciencedirect/benchmark/qrels.jsonl"),
    Path("datasets/sciencedirect/retrieval/corpus_chunks.jsonl"),
]

def create_bundle():
    print(f"[1/2] Packing benchmark bundle to: {OUTPUT_ZIP} ...")
    with zipfile.ZipFile(OUTPUT_ZIP, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel_path in FILES_TO_PACK:
            abs_path = ROOT / rel_path
            if not abs_path.exists():
                print(f"  [ERROR] Missing required file: {abs_path}", file=sys.stderr)
                return 1
            file_size_mb = abs_path.stat().st_size / (1024 * 1024)
            print(f"  + Adding {rel_path} ({file_size_mb:.2f} MB)...")
            zf.write(abs_path, arcname=str(rel_path).replace("\\", "/"))

    zip_size_mb = OUTPUT_ZIP.stat().st_size / (1024 * 1024)
    print(f"[2/2] Bundle created successfully! Total size: {zip_size_mb:.2f} MB")
    print(f"\nUpload this file to Google Colab: {OUTPUT_ZIP}")
    return 0

if __name__ == "__main__":
    sys.exit(create_bundle())

