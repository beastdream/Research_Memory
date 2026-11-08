"""CPU PDF fallback: page-preserving text with explicitly partial structure."""
import re
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import pymupdf
from app.ingestion.arxiv_client import arxiv_identity, normalize_doi, sha256, valid_pdf

PARSER_REVISION = 'pymupdf-fallback-v2.1'


def refresh_quality_flags(record):
    """Derive detection flags from retained evidence without extracting the PDF again."""
    items = record['figures_and_tables']
    record['extraction_quality'].update(
        figures_status='partial' if any(i['type'] == 'figure' for i in items) else 'not_detected',
        tables_status='partial' if any(i['type'] == 'table' and i.get('data') for i in items) else ('captions_only' if any(i['type'] == 'table' for i in items) else 'not_detected'))
    return record


def metadata_digest(metadata):
    return hashlib.sha256(json.dumps(metadata, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def full_text_quality(sections, abstract=''):
    text = '\n'.join(s['text'] for s in sections)
    words = re.findall(r'\b\w+\b', text)
    # Conservative digital-text gate; no claim of semantic completeness or OCR.
    ratio = sum(c.isprintable() or c.isspace() for c in text) / max(1, len(text))
    nonempty = sum(len(re.findall(r'\b\w+\b', s['text'])) >= 80 for s in sections)
    enough = len(words) >= 800 and len(text) >= max(4000, len(abstract) * 3) and nonempty >= 2 and ratio >= .98
    return bool(enough), dict(word_count=len(words), printable_ratio=round(ratio, 4), text_pages=nonempty,
                             criteria='>=800 words; >=4000 chars and 3x abstract; >=2 pages of 80 words; >=98% printable')


def extract_pdf(path):
    path = Path(path)
    if not valid_pdf(path):
        raise ValueError('Unreadable PDF')
    sections, items, references, markers = [], [], [], []
    with pymupdf.open(path) as doc:
        metadata = doc.metadata or {}
        for number, page in enumerate(doc, 1):
            text = page.get_text('text', sort=True).strip()
            if text:
                sections.append(dict(section_id=f'page-{number}', heading=f'Page {number}', text=text,
                                     paragraphs=[p for p in re.split(r'\n\s*\n', text) if p.strip()],
                                     page_start=number, page_end=number, extraction_status='page_fallback'))
            # Captions are heuristics; no invented image or table content.
            for match in re.finditer(r'(?im)^((?:Figure|Fig\.|Table)\s+\d+[a-z]?)[.:]?\s+(.{5,})', text):
                label, caption = match.groups()
                items.append(dict(item_id=f'caption-{number}-{len(items)}', type='table' if label.lower().startswith('table') else 'figure',
                                  label=label, caption=caption.strip(), data=None, text=None, image_path=None,
                                  page=number, extraction_status='partial'))
            if re.search(r'(?im)^Table\s+\d+', text) and hasattr(page, 'find_tables'):
                try:
                    for table in page.find_tables().tables:
                        cells = table.extract()
                        if cells and any(any(cell for cell in row) for row in cells):
                            items.append(dict(item_id=f'table-cells-{number}-{len(items)}', type='table',
                                              label=None, caption=None, data=cells, text=None, image_path=None,
                                              page=number, extraction_status='partial'))
                except (ValueError, RuntimeError, AttributeError):
                    # The caption stays partial when structured extraction is unavailable.
                    pass
            for match in re.finditer(r'\[(\d+(?:\s*[,–-]\s*\d+)*)\]', text):
                markers.append(dict(marker=match[0], page=number, offset=match.start(), resolution_status='unresolved'))
        joined = '\n'.join(s['text'] for s in sections)
        refheading = re.search(r'(?im)^[ \t]*(?:\d+\.?[ \t]+)?(?:References|Bibliography)\b', joined)
        if refheading:
            tail = joined[refheading.end():]
            appendix = re.search(r'(?im)^\s*(?:Appendix|[A-Z]\s+Supplementary)\b', tail)
            if appendix:
                tail = tail[:appendix.start()]
            for raw in re.split(r'\n\s*\n|\n(?=\[\d+\]|[A-Z][\w-]+,)', tail):
                if len(raw.strip()) > 25:
                    year = re.search(r'\b(19\d{2}|20\d{2})\b', raw)
                    doi_match = re.search(r'\b10\.\d{4,9}/[^\s<>]+', raw)
                    arxiv_match = re.search(r'(?:arXiv:\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5}(?:v\d+)?)', raw, re.I)
                    references.append(dict(raw_reference=raw.strip(), title=None, year=int(year[0]) if year else None,
                                           doi=normalize_doi(doi_match[0].rstrip('.,;)')) if doi_match else None,
                                           arxiv_id=arxiv_match[1] if arxiv_match else None, resolution_status='partial'))
        return dict(sections=sections, figures_and_tables=items, references=references, citation_markers=markers,
                    pdf_metadata=metadata, page_count=len(doc))


def normalize_arxiv(metadata, path):
    base, version = arxiv_identity(metadata['arxiv_id'] + (metadata.get('arxiv_version') or ''))
    extracted = extract_pdf(path)
    abstract = metadata.get('abstract') or ''
    full, quality = full_text_quality(extracted['sections'], abstract)
    published = metadata.get('published')
    items = extracted['figures_and_tables']
    quality.update(parser='pymupdf', parser_revision=PARSER_REVISION, structure_status='page_fallback',
                   figures_status='partial' if any(i['type'] == 'figure' for i in items) else 'not_detected',
                   tables_status='partial' if any(i['type'] == 'table' and i.get('data') for i in items) else ('captions_only' if any(i['type'] == 'table' for i in items) else 'not_detected'),
                   references_status='partial' if extracted['references'] else 'not_detected',
                   page_count=extracted['page_count'], status='full_text' if full else 'insufficient_text')
    return dict(
        schema_version='2.0', canonical_id='arxiv:' + base, source='arxiv', source_id=base, split='corpus',
        title=metadata.get('title') or extracted['pdf_metadata'].get('title') or '', abstract=abstract,
        year=int(published[:4]) if published else None,
        identifiers=dict(arxiv_id=base, arxiv_version=version, doi=metadata.get('doi')),
        publication=dict(arxiv_first_submitted_date=published, arxiv_updated_date=metadata.get('updated'),
                         journal_publication_date=None, journal_publication_year=None, venue=None,
                         journal_reference=metadata.get('journal_reference'), publication_status='preprint'),
        authors=metadata.get('authors') or [], categories=metadata.get('categories') or [],
        full_text=extracted['sections'], qas=[], figures_and_tables=extracted['figures_and_tables'],
        references=extracted['references'], citation_markers=extracted['citation_markers'], has_full_text=full,
        provenance=dict(metadata_source=metadata.get('metadata_source', 'arxiv_atom_api'), abstract_url=metadata.get('abstract_url'),
                        metadata_sha256=metadata_digest(metadata),
                        pdf_url=metadata.get('pdf_url'), pdf_path=str(path), pdf_sha256=sha256(path),
                        arxiv_version=version, license=metadata.get('license'), license_status='known' if metadata.get('license') else 'unknown',
                        processed_at_utc=datetime.now(timezone.utc).isoformat(), topic_group=metadata.get('topic_group')),
        extraction_quality=quality,
        # Compatibility aliases for the current import and search code.
        arxiv_id=base, doi=metadata.get('doi') or '', url=metadata.get('abstract_url') or '',
        sections=extracted['sections'], metadata={'topic_group': metadata.get('topic_group'),
                                                'primary_category': metadata.get('primary_category')},
    )
