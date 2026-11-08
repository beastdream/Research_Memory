"""Convert scholarly corpora to a common paper schema, preserving provenance.
QASPER validation/test gold answers are never imported into retrieval storage.
"""
from __future__ import annotations
from collections.abc import Iterable
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ARXIV_RE = re.compile(r'^(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$', re.I)


def clean(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return ' '.join(str(value).split())


def make_paper(source: str, source_id: str, title: str, abstract: str,
               sections: list[dict], *, arxiv_id: str = '', doi: str = '',
               split: str = 'corpus', url: str = '', metadata: dict | None = None) -> dict:
    source_id = clean(source_id)
    arxiv_id = clean(arxiv_id).replace('arxiv:', '').split('v')[0] if arxiv_id else ''
    doi = clean(doi).lower().removeprefix('https://doi.org/').removeprefix('doi:')
    if arxiv_id and ARXIV_RE.match(arxiv_id):
        canonical_id = 'arxiv:' + arxiv_id
    elif doi:
        canonical_id = 'doi:' + doi
    else:
        canonical_id = source + ':' + (source_id or hashlib.sha256(title.encode('utf-8')).hexdigest()[:16])
    valid_sections = []
    for section in sections:
        name = clean(section.get('heading') or section.get('name')) or 'Untitled'
        content = section.get('text') or ''
        if isinstance(content, list):
            content = '\n\n'.join(clean(x) for x in content if clean(x))
        else:
            content = str(content).strip()
        if content:
            valid_sections.append({'heading': name, 'text': content})
    return {
        'canonical_id': canonical_id, 'source': source,
        'source_id': source_id, 'split': split,
        'title': clean(title), 'abstract': str(abstract or '').strip(),
        'arxiv_id': arxiv_id, 'doi': doi, 'url': url,
        'sections': valid_sections, 'has_full_text': bool(valid_sections),
        'metadata': metadata or {},
    }


def normalize_qasper(row: dict, split: str = 'train') -> dict:
    full_text = row.get('full_text') or {}
    sections: list[dict] = []
    if isinstance(full_text, dict):
        names = full_text.get('section_name') or []
        paragraphs = full_text.get('paragraphs') or []
        for idx, paras in enumerate(paragraphs):
            heading = names[idx] if idx < len(names) else f'Section {idx+1}'
            if isinstance(paras, str):
                paras = [paras]
            sections.append({'heading': heading, 'text': '\n\n'.join(str(p) for p in (paras or []) if p)})
    elif isinstance(full_text, list):
        for sec in full_text:
            if isinstance(sec, dict):
                sections.append({'heading': sec.get('section_name'), 'text': sec.get('paragraphs', [])})
    source_id = str(row.get('id') or '')
    arxiv_id = source_id if ARXIV_RE.fullmatch(source_id) else ''
    return make_paper(
        'qasper', source_id, row.get('title') or '', row.get('abstract') or '',
        sections, split=split, arxiv_id=arxiv_id,
        url=f'https://arxiv.org/abs/{arxiv_id}' if arxiv_id else '',
        metadata={'dataset': 'QASPER', 'has_question_annotations': bool(row.get('qas'))}
    )


def normalize_unarxive(row: dict) -> dict:
    metadata = row.get('metadata') or {}
    sections = []
    for name, section in (row.get('sections') or {}).items():
        text = section.get('text', '') if isinstance(section, dict) else str(section)
        sections.append({'heading': name, 'text': text})
    arxiv_id = str(row.get('paper_id') or metadata.get('id') or '')
    abstract = row.get('abstract')
    if isinstance(abstract, dict):
        abstract = abstract.get('text', '')
    return make_paper(
        'unarxive', arxiv_id, metadata.get('title') or '', abstract or metadata.get('abstract') or '',
        sections, arxiv_id=arxiv_id,
        doi=metadata.get('doi') or '', url=f'https://arxiv.org/abs/{arxiv_id}',
        metadata={'authors': metadata.get('authors'), 'category': metadata.get('categories'),
                  'paper_license': metadata.get('license')}
    )


def normalize_scifact(row: dict) -> dict:
    raw_abstract = row.get('abstract') or []
    if isinstance(raw_abstract, list):
        abstract = '\n'.join(str(p) for p in raw_abstract if p)
    else:
        abstract = str(raw_abstract)
    paper = make_paper(
        'scifact', str(row.get('doc_id')), row.get('title') or '', abstract,
        [{'heading': 'Abstract', 'text': abstract}], split='corpus',
        metadata={'dataset': 'SciFact', 'doc_id': row.get('doc_id')}
    )
    # The SciFact corpus contains abstracts, not whole article texts.
    paper['has_full_text'] = False
    return paper


def normalize_pdf(path: Path) -> dict:
    import fitz
    from app.ingestion.arxiv_client import arxiv_identity
    from app.ingestion.arxiv_parser import full_text_quality
    try:
        base, version = arxiv_identity(path.stem.replace('__', '/'))
    except ValueError:
        base, version = '', None
    with fitz.open(path) as doc:
        metadata = doc.metadata or {}
        pages = [{'heading': f'Page {idx+1}', 'text': page.get_text('text')}
                 for idx, page in enumerate(doc)]
        title = metadata.get('title') or path.stem
        paper = make_paper(
            'uploaded_pdf', hashlib.sha256(path.read_bytes()).hexdigest(),
            title, '', pages, split='personal', arxiv_id=base,
            metadata={'filename': path.name, 'page_count': len(doc), 'arxiv_version': version,
                      'identity_evidence': 'arxiv_filename' if base else 'sha256_only',
                      'pdf_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        )
        paper['has_full_text'] = full_text_quality(paper['sections'])[0]
        return paper


def write_jsonl(rows: Iterable[dict], dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with dest.open('w', encoding='utf-8') as output:
        for row in rows:
            if not row['title'] or not row['canonical_id']:
                continue
            output.write(json.dumps(row, ensure_ascii=False) + '\n')
            count += 1
    return count
