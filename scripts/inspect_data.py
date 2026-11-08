"""Check files, field names, and first rows without printing all paper text."""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'datasets' / 'raw'


def inspect():
    for directory in ['unarxive', 'qasper', 'scifact', 'longmemeval']:
        folder = RAW / directory
        files = [f for f in folder.rglob('*') if f.is_file() and '.cache' not in f.parts] if folder.exists() else []
        print(f'\n[{directory}]: {len(files)} files')
        for path in files[:12]:
            print(' ', path.relative_to(ROOT), f'({path.stat().st_size:,} bytes)')

    qtrain = RAW / 'qasper' / 'qasper' / 'train'
    qfiles = sorted(qtrain.rglob('*.parquet')) if qtrain.exists() else []
    if qfiles:
        import pyarrow.parquet as pq
        table = pq.read_table(qfiles[0])
        print('\nQASPER train rows:', table.num_rows)
        print('QASPER columns:', table.column_names)
        print('Sample title:', table['title'][0].as_py())

    unarxive = list((RAW / 'unarxive').rglob('preview.jsonl')) if (RAW / 'unarxive').exists() else []
    if unarxive:
        with unarxive[0].open(encoding='utf-8') as file:
            first = json.load(file)
        print('\nunarXive keys:', list(first))
        print('unarXive title:', first.get('metadata', {}).get('title'))

    sci = list((RAW / 'scifact').rglob('corpus.jsonl')) if (RAW / 'scifact').exists() else []
    if sci:
        with sci[0].open(encoding='utf-8') as file:
            first = json.loads(file.readline())
        print('\nSciFact keys:', list(first))
        print('SciFact title:', first.get('title'))

if __name__ == '__main__':
    inspect()
