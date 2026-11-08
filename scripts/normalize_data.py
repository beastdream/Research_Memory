"""Prepare standardized JSONL from small corpora, selected train data and PDF files."""
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.ingestion.normalize import (normalize_qasper, normalize_unarxive,
                                     normalize_scifact, normalize_pdf, write_jsonl)

RAW = ROOT / 'datasets' / 'raw'
OUT = ROOT / 'datasets' / 'processed'


def first_file(folder: Path, pattern: str):
    files = sorted(folder.rglob(pattern))
    if not files:
        raise FileNotFoundError(f'Could not find {pattern} in {folder}. Run download_data.py first.')
    return files[0]


def jsonl_rows(filename: Path):
    with filename.open('r', encoding='utf-8') as file:
        for line in file:
            if line.strip():
                yield json.loads(line)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--qasper-limit', type=int, default=120,
                        help='Number of QASPER TRAIN papers to normalize')
    parser.add_argument('--scifact-limit', type=int, default=100,
                        help='Number of SciFact corpus papers to normalize')
    args = parser.parse_args()

    unarxive_file = first_file(
        RAW / 'unarxive',
        'preview.jsonl'
    )

    with unarxive_file.open('r', encoding='utf-8') as file:
        preview = json.load(file)

    count = write_jsonl(
        [normalize_unarxive(preview)],
        OUT / 'unarxive_sample.jsonl'
    )

    print('unarXive sample:', count)

    import pyarrow.parquet as pq
    qasper_file = first_file(RAW / 'qasper' / 'qasper' / 'train', '*.parquet')
    table = pq.read_table(qasper_file)
    rows = table.slice(0, max(0, args.qasper_limit)).to_pylist()
    count = write_jsonl((normalize_qasper(r, 'train') for r in rows),
                        OUT / 'qasper_train.jsonl')
    print('QASPER train:', count)

    scifact_file = first_file(RAW / 'scifact', 'corpus.jsonl')
    def sample_scifact():
        for n, row in enumerate(jsonl_rows(scifact_file)):
            if n >= args.scifact_limit:
                break
            yield normalize_scifact(row)
    count = write_jsonl(sample_scifact(), OUT / 'scifact_corpus.jsonl')
    print('SciFact corpus:', count)

    pdfs = list((ROOT / 'storage' / 'pdfs').glob('*.pdf'))
    if pdfs:
        count = write_jsonl((normalize_pdf(f) for f in pdfs),
                            OUT / 'uploaded_pdfs.jsonl')
        print('PDFs:', count)
    else:
        print('PDFs: 0 (put PDFs in storage/pdfs and rerun)')
    print('DONE: processed JSONL in', OUT)

if __name__ == '__main__':
    main()
