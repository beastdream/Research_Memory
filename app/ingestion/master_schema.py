"""Master Dataset v2 validation, including cross-field full-text invariants."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
from app.ingestion.arxiv_client import arxiv_identity, normalize_doi
from app.ingestion.arxiv_parser import full_text_quality

SCHEMA_PATH = Path(__file__).resolve().parents[2] / 'schemas/master_dataset_v2.schema.json'
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def validate_master(record):
    if record.get('schema_version') == '2.1':
        from app.ingestion.enrichment import validate_enriched
        return validate_enriched(record)
    VALIDATOR.validate(record)
    base, _ = arxiv_identity(record['identifiers']['arxiv_id'])
    if record['canonical_id'] != 'arxiv:' + base:
        raise ValueError('Master arXiv canonical identity mismatch')
    if record['source_id'] != base:
        raise ValueError('Master source identity mismatch')
    doi = record['identifiers']['doi']
    if doi and normalize_doi(doi) != doi:
        raise ValueError('DOI must be normalized')
    submitted = record['publication']['arxiv_first_submitted_date']
    if submitted and record['year'] != int(submitted[:4]):
        raise ValueError('year must represent arXiv first submission; journal year is separate')
    for section in record['full_text']:
        if section['page_start'] and section['page_end'] and section['page_start'] > section['page_end']:
            raise ValueError('Invalid section page range')
        if any(section[key] and section[key] > record['extraction_quality']['page_count'] for key in ('page_start', 'page_end')):
            raise ValueError('Section page is outside PDF page count')
    if record['has_full_text'] != (record['extraction_quality']['status'] == 'full_text'):
        raise ValueError('Extraction status disagrees with full-text flag')
    if record.get('sections') != record['full_text']:
        raise ValueError('Compatibility sections must match full_text')
    if record['has_full_text'] and not full_text_quality(record['full_text'], record['abstract'])[0]:
        raise ValueError('Full-text quality criteria not met')
    if record['source'] == 'arxiv' and record['qas']:
        raise ValueError('arXiv ingestion cannot generate ground-truth QAs')
    return record
