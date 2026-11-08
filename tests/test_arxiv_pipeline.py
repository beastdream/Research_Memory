"""Offline tests: no network or production database access."""
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
import requests
import pymupdf
from jsonschema import ValidationError

from app.ingestion.arxiv_client import ArxivClient, arxiv_identity, normalize_doi, parse_atom, valid_pdf, sha256
from app.ingestion.arxiv_index import ExistingIndex
from app.ingestion.arxiv_parser import normalize_arxiv, full_text_quality
from app.ingestion.master_schema import validate_master
from app.ingestion.arxiv_pipeline import quotas, discover, download, normalize, write_manifest, paths, atomic_json, audit
from app.storage.postgres import preserve_merge, upsert_paper, SCHEMA_STATEMENTS

ATOM = '''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry><id>http://arxiv.org/abs/2310.00826v3</id><title>Memory Agents</title><summary>Long term memory for language agents.</summary>
<published>2023-10-01T00:00:00Z</published><updated>2024-01-01T00:00:00Z</updated>
<author><name>A. Researcher</name></author><category term="cs.AI"/><arxiv:primary_category term="cs.AI"/>
<arxiv:doi>https://doi.org/10.1234/EXAMPLE</arxiv:doi><link title="pdf" href="http://arxiv.org/pdf/2310.00826v3"/>
<link rel="alternate" href="https://arxiv.org/abs/2310.00826v3"/></entry></feed>'''


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / '2310.00826v3.pdf'
    doc = pymupdf.open()
    for _ in range(3):
        page = doc.new_page()
        # Meaningless fixture words are ONLY tests, never reported as real scientific data.
        page.insert_textbox((40, 40, 550, 800), ('This fixture sentence tests scientific text extraction. ' * 65), fontsize=10)
    doc.save(path)
    doc.close()
    return path


@pytest.mark.parametrize('raw,expected', [
    ('2310.00826v3', ('2310.00826', 'v3')),
    ('arxiv:2310.00826', ('2310.00826', None)),
    ('https://arxiv.org/pdf/hep-th/9901001v2.pdf', ('hep-th/9901001', 'v2')),
    ('math.GT/0309136v1', ('math.gt/0309136', 'v1')),
])
def test_identity(raw, expected):
    assert arxiv_identity(raw) == expected


@pytest.mark.parametrize('raw', ['../../x', '2310.00826v0', 'not an id', 'https://evil.org/2310.00826'])
def test_invalid_identity(raw):
    with pytest.raises(ValueError):
        arxiv_identity(raw)


def test_atom_metadata_and_missing_fields():
    row = parse_atom(ATOM)[0]
    assert row['arxiv_version'] == 'v3'
    assert row['doi'] == '10.1234/example'
    assert row['journal_reference'] is None and row['license'] is None
    assert row['authors'] == ['A. Researcher']
    assert row['pdf_url'].startswith('https://')


def test_doi():
    assert normalize_doi('DOI:10.1234/ABC') == '10.1234/abc'
    assert normalize_doi('not verified') == ''


def test_cross_source_index_and_conflict():
    index = ExistingIndex()
    index.add({'canonical_id': 'arxiv:2310.00826', 'arxiv_id': '2310.00826v2', 'has_full_text': True})
    assert index.match({'arxiv_id': '2310.00826v3'})['has_full_text']
    index.add({'canonical_id': 'doi:10.1234/x', 'doi': '10.1234/x'})
    assert index.match({'arxiv_id': '2310.00826', 'doi': '10.1234/x'})['conflict']
    assert index.match({'title': 'identical fuzzy title'}) is None


def test_identity_conflict_remains_after_repeated_source():
    index = ExistingIndex()
    for cid in ['old:a', 'old:b', 'old:b']:
        index.add({'canonical_id': cid, 'arxiv_id': '2310.00826'})
    assert index.match({'arxiv_id': '2310.00826'})['conflict']


def test_schema_and_quality(pdf):
    record = normalize_arxiv(parse_atom(ATOM)[0], pdf)
    validate_master(record)
    assert record['has_full_text']
    assert record['publication']['journal_publication_year'] is None
    assert record['year'] == 2023
    assert record['full_text'][0]['extraction_status'] == 'page_fallback'
    assert record['qas'] == []
    record['qas'] = [{'question': 'synthetic'}]
    with pytest.raises(ValidationError):
        validate_master(record)


def test_abstract_not_full_text():
    assert not full_text_quality([{'text': 'Just a short abstract'}])[0]


def test_schema_rejects_false_full_text(pdf):
    record = normalize_arxiv(parse_atom(ATOM)[0], pdf)
    record['full_text'] = record['sections'] = [{'section_id': '1', 'heading': 'Abstract', 'text': 'abstract only', 'page_start': 1, 'page_end': 1, 'extraction_status': 'partial'}]
    with pytest.raises(ValueError):
        validate_master(record)


def response(content=b'', status=200, headers=None):
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.status_code = status
    result.headers = headers or {}
    result.content = content
    result.iter_content.return_value = iter([content])
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(response=result)
    return result


def client_for(responses, monkeypatch):
    session = Mock()
    session.get.side_effect = responses
    client = ArxivClient(session=session)
    client._pause = Mock()
    sleeper = Mock()
    monkeypatch.setattr('app.ingestion.arxiv_client.time.sleep', sleeper)
    return client, sleeper


def test_retry_after_429_and_503(monkeypatch):
    client, sleeper = client_for([response(status=429, headers={'Retry-After': '9'}), response(status=503), response(ATOM.encode())], monkeypatch)
    assert client.query()[0]['arxiv_id'] == '2310.00826'
    assert sleeper.call_args_list[0].args[0] == 9
    assert client.session.get.call_count == 3


def test_download_atomic_and_resume(pdf, tmp_path, monkeypatch):
    client, _ = client_for([response(pdf.read_bytes())], monkeypatch)
    dest = tmp_path / 'download.pdf'
    checksum = client.download('https://arxiv.org/pdf/2310.00826v3', dest, 1000000, [1000000])
    assert checksum == sha256(pdf) and valid_pdf(dest)
    assert client.download('https://arxiv.org/pdf/2310.00826v3', dest, 1, [1]) == checksum
    assert client.session.get.call_count == 1
    assert not dest.with_suffix('.pdf.part').exists()


def test_invalid_pdf_and_size_limit(tmp_path, monkeypatch):
    client, _ = client_for([response(b'HTML not PDF'), response(b'%PDF-' + b'x' * 100)], monkeypatch)
    dest = tmp_path / 'bad.pdf'
    with pytest.raises(ValueError):
        client.download('https://arxiv.org/pdf/2310.00826v3', dest, 1000000, [1000000])
    assert not dest.exists()
    with pytest.raises(ValueError):
        client.download('https://arxiv.org/pdf/2310.00826v3', dest, 10, [1000000])
    assert not dest.exists()


def test_network_retry_restarts_partial(pdf, tmp_path, monkeypatch):
    failed = response()
    failed.iter_content.side_effect = requests.ConnectionError('interrupted')
    client, _ = client_for([failed, response(pdf.read_bytes())], monkeypatch)
    dest = tmp_path / 'restart.pdf'
    client.download('https://arxiv.org/pdf/2310.00826v3', dest, 1000000, [1000000])
    assert sha256(dest) == sha256(pdf)


@pytest.mark.parametrize('target', [100, 101, 150, 199, 200])
def test_proportional_quotas(target):
    assert sum(quotas(target).values()) == target


def test_dry_run_does_not_network(tmp_path):
    client = Mock()
    assert discover(tmp_path, dry_run=True, client=client)['candidate_goal'] == 300
    client.query.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_selected_only_and_normalize_resume(tmp_path, pdf):
    csvpath, metapath = paths(tmp_path)
    meta = parse_atom(ATOM)[0]
    row = dict.fromkeys(__import__('app.ingestion.arxiv_pipeline', fromlist=['FIELDS']).FIELDS, '')
    row.update(arxiv_id=meta['arxiv_id'], selected='true', download_status='pending', topic_group='memory')
    second = dict(row, arxiv_id='2401.00001', selected='false')
    write_manifest(csvpath, [row, second])
    atomic_json(metapath, {meta['arxiv_id']: meta})
    fake = Mock()
    def copy(url, dest, *args):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(pdf.read_bytes())
        return sha256(dest)
    fake.download.side_effect = copy
    download(tmp_path, dry_run=True)
    download(tmp_path, reviewed=True, database=False, client=fake)
    assert fake.download.call_count == 1
    result = normalize(tmp_path)
    assert result['valid_records'] == 1
    assert normalize(tmp_path, resume=True)['valid_records'] == 1
    report = audit(tmp_path, database=False)
    assert report['full_text_success'] == 1 and report['shortfall'] == 149


def test_review_required(tmp_path):
    csvpath, _ = paths(tmp_path)
    write_manifest(csvpath, [])
    with pytest.raises(ValueError, match='Review candidate'):
        download(tmp_path)


def test_metadata_merge_non_destructive_idempotent():
    old = {'authors': ['A'], 'license': 'known', 'nested': {'a': 1}, 'full': True}
    new = {'authors': ['B'], 'license': None, 'nested': {'b': 2}}
    merged = preserve_merge(old, new)
    assert merged['license'] == 'known' and merged['authors'] == ['A', 'B']
    assert preserve_merge(merged, new) == merged
    assert old['authors'] == ['A']


def test_evaluation_rejected_before_any_write():
    db = Mock()
    for source, split in [('qasper', 'validation'), ('qasper', 'test'), ('longmemeval', 'evaluation')]:
        with pytest.raises(ValueError):
            upsert_paper(db, {'source': source, 'split': split})
    db.execute.assert_not_called()


def test_schema_is_additive():
    assert not any('DROP ' in s.upper() for s in SCHEMA_STATEMENTS)
    assert any('paper_master_sources' in s for s in SCHEMA_STATEMENTS)


def test_filename_identity_keeps_sha_provenance(pdf):
    from app.ingestion.normalize import normalize_pdf
    record = normalize_pdf(pdf)
    assert record['canonical_id'] == 'arxiv:2310.00826'
    assert record['metadata']['arxiv_version'] == 'v3'
    assert record['source_id'] == sha256(pdf)


def test_resume_does_not_bypass_review_after_edit(tmp_path):
    csvpath, _ = paths(tmp_path)
    write_manifest(csvpath, [])
    download(tmp_path, dry_run=True)
    csvpath.write_text(csvpath.read_text() + '\n', encoding='utf-8')
    with pytest.raises(ValueError, match='Review candidate'):
        download(tmp_path, reviewed=True, resume=True)


def test_no_overwrite_existing_good_pdf(pdf, monkeypatch):
    before = pdf.read_bytes()
    client, _ = client_for([], monkeypatch)
    client.download('https://arxiv.org/pdf/2310.00826v3', pdf, 1000000, [1000000])
    assert pdf.read_bytes() == before
    client.session.get.assert_not_called()


def test_upsert_resolves_verified_doi_to_existing_record():
    from app.ingestion.normalize import make_paper
    class DB:
        def __init__(self): self.calls = []
        def execute(self, sql, args=None):
            self.calls.append((sql, args))
            result = Mock()
            result.fetchall.return_value = [('openalex:existing',)]
            result.fetchone.return_value = ({}, {}) if 'SELECT publication' in sql else ({'license': 'retained'},)
            return result
    db = DB()
    paper = make_paper('arxiv', '2310.00826', 'Title', '', [], arxiv_id='2310.00826', doi='10.1234/test')
    upsert_paper(db, paper)
    writes = [(s, a) for s, a in db.calls if 'INSERT INTO papers' in s]
    assert writes[0][1][0] == 'openalex:existing'
    assert writes[0][1][6].obj['license'] == 'retained'
    assert 'papers.has_full_text OR EXCLUDED.has_full_text' in writes[0][0]


def test_upsert_identity_conflict_does_not_write():
    from app.ingestion.normalize import make_paper
    db = Mock()
    db.execute.return_value.fetchall.return_value = [('old:a',), ('old:b',)]
    with pytest.raises(ValueError, match='manual review'):
        upsert_paper(db, make_paper('arxiv', '2310.00826', 'Title', '', [], arxiv_id='2310.00826'))
    assert not any('INSERT' in call.args[0] or 'UPDATE' in call.args[0] for call in db.execute.call_args_list)


def test_heldout_index_reads_ids_only(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq
    folder = tmp_path / 'datasets/raw/qasper/qasper/validation'
    folder.mkdir(parents=True)
    pq.write_table(pa.table({'id': ['2310.00826'], 'qas': ['secret evaluation label']}), folder / 'test.parquet')
    index = ExistingIndex.load(tmp_path, database=False)
    assert '2310.00826' in index.excluded
    assert not index.arxiv


def test_legacy_uploaded_checksum_is_used_for_resolution(pdf):
    from app.ingestion.normalize import normalize_pdf
    db = Mock()
    db.execute.return_value.fetchall.return_value = [('uploaded_pdf:legacyhash',)]
    db.execute.return_value.fetchone.return_value = ({}, {})
    upsert_paper(db, normalize_pdf(pdf))
    query = next(call for call in db.execute.call_args_list if 'SELECT DISTINCT' in call.args[0])
    assert query.args[1][-1] == sha256(pdf)
    insert = next(call for call in db.execute.call_args_list if 'INSERT INTO papers' in call.args[0])
    assert insert.args[1][0] == 'uploaded_pdf:legacyhash'


def test_structured_table_cells_are_actual_pdf_content(tmp_path):
    from app.ingestion.arxiv_parser import extract_pdf
    path = tmp_path / 'table.pdf'
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 55), 'Table 1: Observed fixture values')
    for x in (50, 150, 250):
        page.draw_line((x, 80), (x, 180))
    for y in (80, 130, 180):
        page.draw_line((50, y), (250, y))
    for x, y, value in [(60, 110, 'Name'), (160, 110, 'Value'), (60, 160, 'Observed'), (160, 160, '42')]:
        page.insert_text((x, y), value)
    doc.save(path)
    doc.close()
    result = extract_pdf(path)
    tables = [item for item in result['figures_and_tables'] if item.get('data')]
    assert tables and tables[0]['data'] == [['Name', 'Value'], ['Observed', '42']]
    assert tables[0]['extraction_status'] == 'partial'


def test_v2_snapshot_keeps_text_and_checksum_from_same_version(pdf):
    import copy
    incoming = normalize_arxiv(parse_atom(ATOM)[0], pdf)
    old = copy.deepcopy(incoming)
    old['provenance']['pdf_sha256'] = 'a' * 64
    old['full_text'] = old['sections'] = []
    old['has_full_text'] = False
    class DB:
        def __init__(self): self.calls = []
        def execute(self, sql, args=None):
            self.calls.append((sql, args))
            result = Mock()
            result.fetchall.return_value = []
            if 'SELECT master_json' in sql: result.fetchone.return_value = (old,)
            elif 'SELECT publication' in sql: result.fetchone.return_value = ({}, {})
            else: result.fetchone.return_value = None
            return result
    db = DB()
    upsert_paper(db, incoming)
    immutable = next(args for sql, args in db.calls if 'INSERT INTO paper_master_versions' in sql)
    assert immutable[3] == incoming['provenance']['pdf_sha256']
    selected = next(args for sql, args in db.calls if 'INSERT INTO paper_master_sources' in sql)[3].obj
    assert selected['provenance'] == incoming['provenance']
    assert selected['full_text'] == incoming['full_text']


def test_unicode_pdf_line_separators_do_not_split_jsonl_records(tmp_path, pdf):
    record = normalize_arxiv(parse_atom(ATOM)[0], pdf)
    record['full_text'][0]['text'] += '\u2028Unicode PDF text\x85still in the same record'
    dest = tmp_path / 'datasets/processed/arxiv_master.jsonl'
    dest.parent.mkdir(parents=True)
    dest.write_text(json.dumps(record, ensure_ascii=False) + '\n', encoding='utf-8')
    index = ExistingIndex.load(tmp_path, database=False)
    assert index.match(record)['has_full_text']
    report = audit(tmp_path, database=False)
    assert report['full_text_success'] == 1 and not report['invalid_records']


def test_audit_does_not_count_duplicate_identity_twice(tmp_path, pdf):
    record = normalize_arxiv(parse_atom(ATOM)[0], pdf)
    dest = tmp_path / 'datasets/processed/arxiv_master.jsonl'
    dest.parent.mkdir(parents=True)
    dest.write_text((json.dumps(record) + '\n') * 2, encoding='utf-8')
    report = audit(tmp_path, database=False)
    assert report['full_text_success'] == 1 and len(report['invalid_records']) == 1
