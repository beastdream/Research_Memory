"""Source-bound audit. Readiness is independent of scientific completeness."""
from collections import Counter
import hashlib
import re
import unicodedata

from app.dataset_inspector import local_path, sections, paragraphs


def words(text):
    return Counter(re.findall(r'\w+', unicodedata.normalize('NFKC', text).lower()))


def audit_paper(paper):
    from app.ingestion.master_schema import validate_master
    import pymupdf
    issues = []
    schema_valid = True
    try:
        validate_master(paper)
        if paper['canonical_id'] != 'arxiv:'+paper['identifiers']['arxiv_id']:
            raise ValueError('identity')
    except Exception as error:
        schema_valid = False
        issues.append('schema_or_invariant:'+type(error).__name__)
    ss = sections(paper)
    full = paper.get('full_text')
    extraction_status = full.get('extraction_status', 'unknown') if isinstance(full, dict) else paper.get('extraction_quality', {}).get('status', 'unknown')
    pp = [p for s in ss for p in paragraphs(s)]
    path = local_path(paper.get('provenance', {}).get('pdf_path'))
    pdf_valid, checksum_matches = False, False
    page_count, page_checks, pdf_labels = 0, [], []
    by_page = {}
    for p in pp:
        start, end = p.get('page_start'), p.get('page_end')
        if start and end and start == end:
            by_page.setdefault(start, []).append(p['text'])
    try:
        if path and path.is_file():
            checksum_matches = hashlib.sha256(path.read_bytes()).hexdigest() == paper.get('provenance', {}).get('pdf_sha256')
            with pymupdf.open(path) as doc:
                pdf_valid, page_count = doc.is_pdf and len(doc) > 0, len(doc)
                for number, page in enumerate(doc, 1):
                    source_text = page.get_text()
                    original = words(source_text)
                    for match in re.finditer(r'(?im)^\s*((?:Figure|Fig\.|Table)\s+\d+[a-z]?)\s*[:.]', source_text):
                        label=re.sub(r'\s+', ' ', match[1]).lower().replace('fig.', 'figure')
                        pdf_labels.append((number,label))
                    extracted = words('\n'.join(by_page.get(number, [])))
                    if number == 1:
                        extracted += words(paper.get('abstract', ''))
                    coverage = sum((original & extracted).values()) / max(1, sum(original.values()))
                    page_checks.append(dict(page=number, source_word_count=sum(original.values()),
                        word_coverage=round(coverage, 4), mapped=number in by_page))
    except (RuntimeError, ValueError, OSError):
        issues.append('pdf_open_failed')
    if not pdf_valid:
        issues.append('valid_pdf_unavailable')
    if not checksum_matches:
        issues.append('pdf_checksum_unverified')
    missing = [v['page'] for v in page_checks if v['source_word_count'] >= 30 and not v['mapped']]
    low = [v['page'] for v in page_checks if v['source_word_count'] >= 80 and v['word_coverage'] < .80]
    if missing:
        issues.append('unmapped_text_pages:'+','.join(map(str, missing)))
    if low:
        issues.append('low_page_word_coverage:'+','.join(map(str, low)))
    invalid_ranges = sum(not p.get('page_start') or not p.get('page_end') or
        not 1 <= p['page_start'] <= p['page_end'] <= page_count for p in pp)
    if invalid_ranges:
        issues.append('invalid_or_missing_paragraph_page_ranges')
    fingerprints = Counter(' '.join(p['text'].split()) for p in pp if len(p['text']) > 100)
    duplicates = sum(n-1 for n in fingerprints.values() if n>1)
    if duplicates:
        issues.append('duplicate_long_paragraphs:'+str(duplicates))
    glyphs = sum(p['text'].count('\ufffd')+p['text'].count('\x00') for p in pp)
    if glyphs:
        issues.append('unmapped_scientific_glyphs:'+str(glyphs))
    objects = paper.get('figures_and_tables', [])
    figures = [o for o in objects if o['type']=='figure']
    tables = [o for o in objects if o['type']=='table']
    recorded_labels={(o.get('page'),re.sub(r'\s+', ' ', o.get('label') or '').lower().replace('fig.', 'figure')) for o in objects}
    missed_labels=sorted(set(pdf_labels)-recorded_labels)
    if missed_labels:
        issues.append('pdf_caption_candidates_missing_from_objects:'+str(len(missed_labels)))
    if any(o.get('extraction_status') != 'complete' for o in objects):
        issues.append('object_structure_partial')
    if any(o.get('image_path') and not local_path(o['image_path']).is_file() for o in figures):
        issues.append('figure_image_missing')
    if any(not o.get('rows') for o in tables):
        issues.append('table_caption_without_cells')
    refs = paper.get('references', [])
    if any(r.get('extraction_status') != 'complete' for r in refs):
        issues.append('bibliography_partial_fragments')
    if not refs:
        issues.append('bibliography_not_detected_absence_unverified')
    structure = any(s.get('kind') == 'identified_heading' for s in ss)
    if not structure:
        issues.append('section_headings_unverified')
    metadata_ready = bool(paper.get('title') and paper.get('abstract') and paper.get('authors') and
        paper.get('year') and paper.get('publication', {}).get('arxiv_first_submitted_date') and
        paper.get('provenance', {}).get('metadata_source') and schema_valid)
    provenance_ready = bool(pdf_valid and checksum_matches and paper.get('provenance', {}).get('enrichment_parser'))
    # Word coverage alone does not establish usability: require schema, exact source
    # identity/checksum, structural headings and valid page links as separate gates.
    usable = bool(pp and provenance_ready and not missing and not low and not invalid_ranges and structure and schema_valid)
    if glyphs / max(1, sum(len(p['text']) for p in pp)) > .002:
        usable = False
        issues.append('excessive_unmapped_glyphs')
    qa = paper.get('qas', [])
    verified = [q for q in qa if q.get('validation_status') == 'human_verified' and q.get('evidence_refs')]
    training = [q for q in verified if q.get('split') == 'train' and q.get('training_eligible') and q.get('question') and q.get('answer')]
    return dict(canonical_id=paper['canonical_id'], arxiv_id=paper.get('identifiers', {}).get('arxiv_id'),
        title=paper.get('title'), year=paper.get('year'), has_pdf=pdf_valid,
        has_full_text=bool(pp), full_text_status=extraction_status,
        section_count=len(ss), paragraph_count=len(pp), figure_count=len(figures), table_count=len(tables),
        reference_count=len(refs), qa_count=len(qa), verified_qa_count=len(verified),
        page_mapping_status='verified_ranges' if pp and not invalid_ranges and not missing else 'partial',
        provenance_status='pdf_checksum_verified' if provenance_ready else 'unverified', schema_valid=schema_valid,
        MASTER_METADATA_READY=metadata_ready, MASTER_CONTENT_READY=usable, RETRIEVAL_READY=usable,
        QA_TRAINING_READY=bool(training), retrieval_ready=usable, qa_training_ready=bool(training),
        full_text_usable=usable, structurally_complete=extraction_status == 'complete',
        page_count=page_count, page_checks=page_checks, pdf_caption_candidates=len(set(pdf_labels)),
        missing_caption_candidates=[dict(page=n,label=l) for n,l in missed_labels],
        object_absence_status='not_established_by_text_parser', duplicate_long_paragraph_count=duplicates,
        unmapped_glyph_count=glyphs, issues=issues,
        retrieval_blockers=[i for i in issues if i.startswith(('schema_', 'valid_pdf', 'pdf_checksum',
            'unmapped_text_pages', 'low_page', 'invalid_or_missing', 'section_headings', 'excessive_'))])
