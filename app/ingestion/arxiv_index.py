"""Evidence-only identity index. Never merges titles or changes source datasets."""
import json
from pathlib import Path
from app.ingestion.arxiv_client import arxiv_identity, normalize_doi, sha256


def identities(row):
    ids = row.get('identifiers') or {}
    arxiv = row.get('arxiv_id') or ids.get('arxiv_id')
    canonical = row.get('canonical_id', '')
    if not arxiv and canonical.startswith('arxiv:'):
        arxiv = canonical
    try:
        arxiv = arxiv_identity(arxiv)[0]
    except ValueError:
        arxiv = ''
    return arxiv, normalize_doi(row.get('doi') or ids.get('doi'))


class ExistingIndex:
    def __init__(self):
        self.arxiv = {}
        self.dois = {}
        self.hashes = {}
        self.pdfs = {}
        self.excluded = set()
        self.db_status = 'not_checked'

    def add(self, row):
        arxiv, doi = identities(row)
        info = {'canonical_id': row.get('canonical_id'), 'has_full_text': bool(row.get('has_full_text'))}
        for key, store in ((arxiv, self.arxiv), (doi, self.dois)):
            if key:
                previous = store.get(key)
                if previous and (previous.get('conflict') or previous['canonical_id'] != info['canonical_id']):
                    # Ambiguous legacy records require review, never destructive merging.
                    store[key] = dict(info, conflict=True)
                else:
                    store[key] = dict(info, has_full_text=info['has_full_text'] or bool(previous and previous['has_full_text']))
        checksum = (row.get('provenance') or {}).get('pdf_sha256') or (row.get('metadata') or {}).get('pdf_sha256')
        if checksum:
            self.hashes[checksum] = info

    def match(self, row):
        arxiv, doi = identities(row)
        a, d = self.arxiv.get(arxiv), self.dois.get(doi)
        if a and d and a['canonical_id'] != d['canonical_id']:
            return {'conflict': True, 'has_full_text': False}
        return a or d

    @classmethod
    def load(cls, root, database=True):
        root = Path(root)
        index = cls()
        # Read only retrieval allowlisted files; evaluation labels are never opened.
        from scripts.import_db import INPUTS
        for name in INPUTS:
            path = root / 'datasets/processed' / name
            if path.exists():
                for line in path.read_text(encoding='utf-8-sig').split('\n'):
                    if line.strip():
                        index.add(json.loads(line))
        # Read ONLY IDs from held-out QASPER parquet, never QA/answer columns.
        import pyarrow.parquet as pq
        for split in ('validation', 'test'):
            for path in (root / 'datasets/raw/qasper/qasper' / split).glob('*.parquet'):
                for ident in pq.read_table(path, columns=['id']).column('id').to_pylist():
                    try:
                        index.excluded.add(arxiv_identity(ident)[0])
                    except ValueError:
                        pass
        for path in (root / 'storage/pdfs').rglob('*.pdf'):
            checksum = sha256(path)
            index.hashes.setdefault(checksum, {'path': str(path)})
            try:
                base, _ = arxiv_identity(path.stem.replace('__', '/'))
                index.pdfs[base] = path
                # Match legacy uploaded records by checksum even without arXiv metadata.
                legacy = root / 'datasets/processed/uploaded_pdfs.jsonl'
                if legacy.exists():
                    for line in legacy.read_text(encoding='utf-8-sig').split('\n'):
                        if not line.strip():
                            continue
                        row = json.loads(line)
                        if row.get('source_id') == checksum:
                            index.add(dict(row, arxiv_id=base))
            except ValueError:
                pass
        if database:
            from app.storage.postgres import connect
            try:
                with connect() as db:
                    db.execute('SET TRANSACTION READ ONLY')
                    for cid, arxiv, doi, full, metadata in db.execute('SELECT canonical_id,arxiv_id,doi,has_full_text,metadata_json FROM papers'):
                        index.add(dict(canonical_id=cid, arxiv_id=arxiv, doi=doi, has_full_text=full, metadata=metadata))
                    index.db_status = 'checked'
            except Exception as exc:
                index.db_status = 'unavailable:' + type(exc).__name__
        return index
