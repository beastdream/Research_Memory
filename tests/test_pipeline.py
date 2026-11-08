"""Fast unit tests for normalization, independent of PostgreSQL server."""
from app.ingestion.normalize import (
    normalize_qasper, normalize_unarxive, normalize_scifact, make_paper
)


def test_qasper_normalization():
    row = {'id': '2310.00826', 'title': 'Test Paper', 'abstract': 'Example',
           'full_text': {'section_name': ['Introduction', 'Methods'],
                         'paragraphs': [['paragraph A', 'paragraph B'], ['method C']]}}
    paper = normalize_qasper(row)
    assert paper['canonical_id'] == 'arxiv:2310.00826'
    assert len(paper['sections']) == 2
    assert 'paragraph B' in paper['sections'][0]['text']
    assert paper['has_full_text'] is True


def test_unarxive_normalization():
    row = {'paper_id': '2310.00826', 'metadata': {'title': 'Test Paper'},
           'abstract': {'text': 'Abstract'},
           'sections': {'Introduction': {'text': 'A paragraph'}}}
    paper = normalize_unarxive(row)
    assert paper['canonical_id'] == 'arxiv:2310.00826'
    assert paper['sections'][0]['heading'] == 'Introduction'


def test_scifact_abstract_not_full_text():
    row = {'doc_id': 3, 'title': 'SciFact Sample',
           'abstract': ['First sentence.', 'Second sentence.']}
    paper = normalize_scifact(row)
    assert paper['canonical_id'] == 'scifact:3'
    assert 'Second' in paper['abstract']
    assert paper['sections'][0]['heading'] == 'Abstract'
    assert paper['has_full_text'] is False


def test_canonical_dedup_across_sources():
    q = make_paper('qasper', '2310.00826', 'Test', 'Abstract',
                   [{'heading': 'Intro', 'text': 'Text'}],
                   arxiv_id='2310.00826', split='train')
    u = make_paper('unarxive', '2310.00826', 'Test', 'Abstract',
                   [{'heading': 'Intro', 'text': 'Other text'}],
                   arxiv_id='2310.00826')
    assert q['canonical_id'] == u['canonical_id']
    assert q['source'] != u['source']


def test_metadata_only_not_full_text():
    paper = make_paper('openalex', 'W100', 'A Title', '', [], split='metadata_only')
    assert paper['has_full_text'] is False
