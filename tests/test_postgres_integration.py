"""Opt-in PostgreSQL integration tests. NEVER connect to the production database.

Set TEST_DATABASE_URL pointing to a separately-created 'researchmemory_test' database.
All writes are rolled back. If the variable is unset, tests are skipped.
"""
import os
from uuid import uuid4

import pytest

psycopg = pytest.importorskip('psycopg')
from app.ingestion.normalize import make_paper, normalize_scifact
from app.storage.postgres import init_schema, upsert_paper


@pytest.fixture
def db():
    url = os.getenv('TEST_DATABASE_URL', '').strip()
    if not url:
        pytest.skip('Set TEST_DATABASE_URL for an isolated PostgreSQL integration test')
    with psycopg.connect(url, connect_timeout=5) as conn:
        current = conn.execute('SELECT current_database()').fetchone()[0]
        if current != 'researchmemory_test':
            pytest.fail(
                f'Refusing to run integration test on {current!r}. '
                'Expected researchmemory_test only.'
            )
        try:
            init_schema(conn)
            yield conn
        finally:
            conn.rollback()  # The fixture NEVER commits.


def test_postgres_upsert_sources_sections_and_metadata(db):
    ident = f'2401.{uuid4().int % 100000:05d}'
    p1 = make_paper('qasper', ident, 'Long Paper', 'An abstract',
                    [{'heading': 'Method', 'text': 'A method'}], arxiv_id=ident,
                    split='train', metadata={'first_source': 'qasper'})
    p2 = make_paper('unarxive', ident, 'Long Paper', 'An abstract',
                    [{'heading': 'Results', 'text': 'Result text'}], arxiv_id=ident,
                    metadata={'second_source': 'unarxive'})
    upsert_paper(db, p1)
    upsert_paper(db, p1)
    upsert_paper(db, p2)
    row = db.execute(
        'SELECT metadata_json, has_full_text FROM papers WHERE canonical_id = %s',
        (p1['canonical_id'],)
    ).fetchone()
    assert row[0]['first_source'] == 'qasper'
    assert row[0]['second_source'] == 'unarxive'
    assert row[1] is True
    assert db.execute('SELECT count(*) FROM paper_sources WHERE canonical_id = %s',
                      (p1['canonical_id'],)).fetchone()[0] == 2
    assert db.execute('SELECT count(*) FROM sections WHERE canonical_id = %s',
                      (p1['canonical_id'],)).fetchone()[0] == 2


def test_scifact_abstract_flag_in_postgres(db):
    paper = normalize_scifact({
        'doc_id': str(uuid4()), 'title': 'One Abstract',
        'abstract': ['A finding', 'Another finding']
    })
    upsert_paper(db, paper)
    assert db.execute('SELECT has_full_text FROM papers WHERE canonical_id = %s',
                      (paper['canonical_id'],)).fetchone()[0] is False


def test_v2_reimport_preserves_master_when_metadata_only_arrives(db):
    import json
    from pathlib import Path
    record = json.loads((Path(__file__).resolve().parents[1] / 'docs/examples/memgpt_local_pdf_master_v2.json').read_text(encoding='utf-8'))
    ident = f'2401.{uuid4().int % 100000:05d}'
    record.update(canonical_id='arxiv:' + ident, source_id=ident, arxiv_id=ident)
    record['identifiers']['arxiv_id'] = ident
    upsert_paper(db, record)
    upsert_paper(db, record)
    metadata = make_paper('openalex', str(uuid4()), 'Short title', '', [], arxiv_id=ident)
    upsert_paper(db, metadata)
    stored = db.execute('SELECT has_full_text FROM papers WHERE canonical_id=%s', (record['canonical_id'],)).fetchone()
    assert stored[0] is True
    assert db.execute('SELECT count(*) FROM paper_master_sources WHERE canonical_id=%s', (record['canonical_id'],)).fetchone()[0] == 1
    snapshot = db.execute('SELECT master_json FROM paper_master_sources WHERE canonical_id=%s', (record['canonical_id'],)).fetchone()[0]
    assert snapshot['full_text'] == record['full_text']
    assert snapshot['provenance']['pdf_sha256'] == record['provenance']['pdf_sha256']


def test_enriched_upsert_content_idempotency_and_history(db):
    import copy
    import json
    from pathlib import Path
    from app.ingestion.enrichment import enrich_record
    from app.storage.enrichment import upsert_enrichment
    record=json.loads((Path(__file__).resolve().parents[1]/'docs/examples/memgpt_local_pdf_master_v2.json').read_text(encoding='utf-8'))
    ident=f'2401.{uuid4().int % 100000:05d}'
    record.update(canonical_id='arxiv:'+ident,source_id=ident,arxiv_id=ident)
    record['identifiers']['arxiv_id']=ident
    upsert_paper(db,record)
    paper=enrich_record(record)
    assert upsert_enrichment(db,paper)=='inserted'
    timestamp=db.execute('SELECT updated_at FROM paper_enrichments WHERE canonical_id=%s',(paper['canonical_id'],)).fetchone()[0]
    assert upsert_enrichment(db,paper)=='unchanged'
    assert db.execute('SELECT updated_at FROM paper_enrichments WHERE canonical_id=%s',(paper['canonical_id'],)).fetchone()[0]==timestamp
    changed=copy.deepcopy(paper)
    changed['quality']['review_note']='A new quality observation'
    assert upsert_enrichment(db,changed)=='updated'
    assert db.execute('SELECT count(*) FROM paper_enrichment_history WHERE canonical_id=%s',(paper['canonical_id'],)).fetchone()[0]==1
