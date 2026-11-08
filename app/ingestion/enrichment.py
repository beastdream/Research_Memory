"""Offline enrichment of selected papers. Original extraction is never modified.

The line-based structural adapter is deliberately conservative: all of its output
is partial until reviewed. It reuses stored text instead of extracting PDFs again.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from app.ingestion.arxiv_client import arxiv_identity, normalize_doi

REVISION = 'pymupdf-layout-enrichment-1.4'
STATUSES = ['pending', 'complete', 'partial', 'failed', 'not_applicable']
HEADING = re.compile(
    r'^(?:(?:\d+(?:\.\d+)*\.?|[A-Z])\s+)?'
    r'(?:Introduction|Related Work|Background|Methods?|Methodology|Approach|'
    r'Experiments?(?: and Results)?|Experimental (?:Setup|Results)|Results(?: and Discussion)?|'
    r'Discussion|Conclusions?(?: and Future Work)?|Limitations|Acknowledg(?:e)?ments?|'
    r'References|Bibliography|Appendix)$', re.I)
NUMBERED = re.compile(r'^(?:[1-9]\d?(?:\.\d+)*\.?|[IVX]+\.|[A-Z]\.)\s+[A-Z][^!?]{2,100}$')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def stable_id(paper, kind, value):
    namespace = [paper['canonical_id'], paper['identifiers'].get('arxiv_version'), REVISION]
    return kind + '-' + digest([namespace, value])[:24]


def improve_object_captions(items, pdf_path, paper=None):
    """Retain full PDF text blocks and their bbox, never infer chart/table values.

    A block may still contain neighboring prose, hence it remains a candidate.
    No bitmap is associated with a figure without verified geometric evidence.
    """
    import pymupdf
    checksum = sha256_local(pdf_path)
    with pymupdf.open(pdf_path) as doc:
        blocks_by_page={number:page.get_text('blocks') for number,page in enumerate(doc,1)}
        if paper:
            known={(o.get('page'),re.sub(r'\s+', ' ', o.get('label') or '').lower().replace('fig.', 'figure')) for o in items}
            for number,blocks in blocks_by_page.items():
                for block in blocks:
                    if block[6]!=0:
                        continue
                    for match in re.finditer(r'(?im)^\s*((?:Figure|Fig\.|Table)\s+\d+[a-z]?)\s*[:.]\s*([^\n]+)',block[4]):
                        label=re.sub(r'\s+', ' ',match[1])
                        key=(number,label.lower().replace('fig.', 'figure'))
                        if key in known:
                            continue
                        known.add(key)
                        kind='table' if label.lower().startswith('table') else 'figure'
                        oid=stable_id(paper,kind,['pdf_block_caption',number,list(block[:4]),match[0]])
                        found=[s['section_id'] for s in paper['full_text']['sections'] if s['page_start']<=number<=s['page_end']]
                        obj=dict(type=kind,item_id=oid,label=label,caption=match[2].strip(),page=number,
                            data=None,text=None,image_path=None,section_id=found[0] if len(found)==1 else None,
                            source_bbox=None,caption_bbox=list(block[:4]),extraction_status='partial',detection_status='pdf_block_caption_candidate')
                        if kind=='table':
                            obj.update(table_id=oid,headers=[],rows=[],units=None,raw_text=None,header_status='unverified')
                        else:
                            obj.update(figure_id=oid,description=None)
                        items.append(obj)
        for obj in items:
            number, label = obj.get('page'), obj.get('label')
            if not number or not label or not 1 <= number <= len(doc):
                continue
            page = doc[number-1]
            label_pattern = re.escape(label).replace(r'\ ', r'\s+')
            candidates = [b for b in blocks_by_page[number] if b[6]==0 and
                          re.match(r'^\s*'+label_pattern+r'\s*[:.]', b[4], re.I)]
            if len(candidates) != 1:
                continue
            block = candidates[0]
            raw = block[4].strip()
            caption = re.sub(r'^\s*'+label_pattern+r'\s*[:.]\s*', '', raw, count=1, flags=re.I)
            # A complete block is evidence; it is not a guarantee of a complete caption.
            if len(caption) > len(obj.get('caption') or ''):
                obj['original_caption'] = obj.get('caption')
                obj['caption'] = caption
            obj['caption_bbox'] = list(block[:4])
            obj['caption_raw_text'] = raw
            obj['caption_status'] = 'pdf_block_candidate'
            obj['caption_pdf_sha256'] = checksum
    return items


def sha256_local(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def recover_caption_tables(items, pdf_path):
    """Fallback for ruled tables lacking vertical rules. Keep raw cells partial.

    Geometric association and header semantics remain unverified. Multi-line
    cells are retained literally, never expanded into guessed scientific rows.
    """
    import pymupdf
    with pymupdf.open(pdf_path) as doc:
        for obj in items:
            if obj['type']!='table' or obj.get('rows') or not obj.get('caption_bbox'):
                continue
            page=doc[obj['page']-1]
            box=obj['caption_bbox']
            clip=pymupdf.Rect(0,max(0,box[1]-180),page.rect.width,min(page.rect.height,box[3]+220))
            try:
                found=page.find_tables(clip=clip,vertical_strategy='text',horizontal_strategy='lines').tables
                candidates=[]
                for table in found:
                    x0,y0,x1,y1=table.bbox
                    distance=max(box[1]-y1,y0-box[3],0)
                    if distance<80 and x1-x0>100 and x0<box[2] and x1>box[0]:
                        candidates.append((distance,table))
                candidates.sort(key=lambda value:value[0])
                if candidates and (len(candidates)==1 or candidates[0][0]<candidates[1][0]):
                    table=candidates[0][1]
                    cells=table.extract()
                    if cells and len(cells)>1:
                        obj.update(rows=cells,data=cells,source_bbox=list(table.bbox),headers=[],units=None,
                            header_status='unverified',table_parser='pymupdf-text-columns-ruled-rows',
                            detection_status='geometric_table_candidate',extraction_status='partial')
            except (ValueError,RuntimeError,AttributeError):
                # Retain the caption and any existing cells if fallback fails.
                continue


def layout_adapter(path, stored_pages):
    """Use font evidence for headings and repair order only on detected two-column pages."""
    import pymupdf
    from collections import Counter
    pages, heading_lines, repaired = [], set(), []
    with pymupdf.open(path) as doc:
        for number, page in enumerate(doc, 1):
            blocks = [b for b in page.get_text('dict', flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)['blocks'] if b['type']==0]
            sizes = Counter()
            for b in blocks:
                for line in b['lines']:
                    for span in line['spans']:
                        sizes[round(span['size'], 1)] += len(span['text'])
            body = sizes.most_common(1)[0][0] if sizes else 10
            for b in blocks:
                for line in b['lines']:
                    text = ''.join(s['text'] for s in line['spans']).strip()
                    norm = ' '.join(text.split())
                    if any(s['size']>body+.4 or s['flags'] & 16 for s in line['spans']):
                        if norm.lower()=='method' and not any(s['size']>body+.4 for s in line['spans']):
                            continue
                        heading_lines.add((number, norm))
            midpoint = page.rect.width/2
            left = [b for b in blocks if b['bbox'][0]<midpoint and b['bbox'][2]<=midpoint+12]
            right = [b for b in blocks if b['bbox'][0]>=midpoint-12]
            chars = lambda bs: sum(len(s['text']) for b in bs for l in b['lines'] for s in l['spans'])
            two_columns = chars(left)>450 and chars(right)>450
            stored = next((p for p in stored_pages if p.get('page_start')==number), None)
            if not two_columns and stored:
                pages.append(copy.deepcopy(stored))
                continue
            # Full-width blocks separate column bands; within each band read left then right.
            wide = [b for b in blocks if b not in left and b not in right]
            ordered, remaining = [], list(blocks)
            for anchor in sorted(wide, key=lambda b:b['bbox'][1]):
                preceding = [b for b in remaining if b is not anchor and b['bbox'][3]<=anchor['bbox'][1]+2]
                ordered.extend(sorted(preceding, key=lambda b:(b['bbox'][0]>=midpoint-12,b['bbox'][1])))
                ordered.append(anchor)
                remaining = [b for b in remaining if b is not anchor and b not in preceding]
            ordered.extend(sorted(remaining, key=lambda b:(b['bbox'][0]>=midpoint-12,b['bbox'][1])))
            text = '\n\n'.join('\n'.join(''.join(s['text'] for s in l['spans']) for l in b['lines']) for b in ordered)
            pages.append(dict(text=text, page_start=number, page_end=number))
            if two_columns:
                repaired.append(number)
    return pages, heading_lines, repaired


def structure(paper, pages, heading_lines=None):
    sections, current, buffer = [], None, []
    skipped_abstract = False
    in_abstract = False
    body_started = False

    def flush():
        nonlocal buffer
        if buffer and current is not None:
            text = '\n'.join(line for _, line in buffer).strip()
            if text:
                start, end = buffer[0][0], buffer[-1][0]
                pid = stable_id(paper, 'para', [current['section_id'], len(current['paragraphs']), text, start, end])
                current['paragraphs'].append(dict(paragraph_id=pid, text=text,
                                                 page_start=start, page_end=end))
        buffer = []

    def section(heading, page, kind):
        nonlocal current
        flush()
        sid = stable_id(paper, 'sec', [len(sections), heading, page])
        current = dict(section_id=sid, heading=heading, paragraphs=[],
                       page_start=page, page_end=page, extraction_status='partial', kind=kind)
        sections.append(current)

    for page_idx, page in enumerate(pages, 1):
        number = page.get('page_start') or page_idx
        if current is None:
            section('Front matter / unclassified text', number, 'unclassified')
        lines = page.get('text', '').split('\n')
        combined = []
        i = 0
        while i < len(lines):
            raw = lines[i]
            if (heading_lines is not None and i+1<len(lines) and
                    re.fullmatch(r'(?:[1-9]\d?(?:\.\d+)*\.?|[A-Z])', raw.strip()) and
                    (number, ' '.join(lines[i+1].split())) in heading_lines):
                raw += ' ' + lines[i+1].strip()
                i += 1
            combined.append(raw)
            i += 1
        for raw in combined:
            line = raw.strip()
            if line.lower() == 'abstract' and paper.get('abstract'):
                # Preserve PDF wording in its own section. Previously the parser
                # discarded everything up to the next recognized heading, which
                # could silently remove an introduction on unfamiliar templates.
                section('Abstract', number, 'abstract')
                skipped_abstract = True
                continue
            named = bool(HEADING.fullmatch(line)) and not line.endswith('.')
            numbered = bool(NUMBERED.fullmatch(line)) and len(line)<85 and not line.endswith((':', ','))
            heading = named or (numbered and (body_started or heading_lines is not None))
            # Numeric headings must be short, text-like, and contain no table columns.
            heading = heading and not re.search(r'[=<>]|\d\s*[%±]', raw.strip())
            if heading_lines is not None:
                # Small caps headings may be unbolded; numbered title case lines
                # remain candidates, but sentence-like list entries are rejected.
                small_caps = line.upper()==line and len(line)<70 and not re.search(r'\d', re.sub(r'^\d+(?:\.\d+)*\.?\s+', '', line))
                normalized = ' '.join(line.split())
                without_number = re.sub(r'^(?:\d+(?:\.\d+)*\.?|[A-Z])\s+', '', normalized)
                heading = heading and ((number, normalized) in heading_lines or
                                       (number, without_number) in heading_lines or small_caps)
                if re.search(r'\b(?:We |we |Specific to |Query on |Query focused )', line):
                    heading = False
            if heading:
                body_started = True
                in_abstract = False
                section(line, number, 'identified_heading')
            elif not in_abstract:
                if not line:
                    flush()
                else:
                    buffer.append((number, raw.rstrip()))
                    current['page_end'] = number
        flush()
        # Do not let a missing introduction heading discard the rest of the paper.
        if in_abstract:
            in_abstract = False
            section('Unclassified text', number + 1, 'unclassified')
    flush()
    return [s for s in sections if s['paragraphs'] or s['kind']=='identified_heading'], skipped_abstract


def enrich_record(original, pdf_path=None):
    paper = copy.deepcopy(original)
    pages = copy.deepcopy(original.get('full_text') or original.get('sections') or [])
    if isinstance(pages, dict):
        return paper
    hints, repaired = None, []
    if pdf_path:
        pages, hints, repaired = layout_adapter(pdf_path, pages)
    normalizations = []
    for page in pages:
        text = page.get('text', '')
        offsets = [i for i,c in enumerate(text) if c=='\x00']
        if offsets:
            normalizations.append(dict(page=page.get('page_start'), offsets=offsets,
                original_codepoint='U+0000', replacement='U+FFFD',
                reason='PDF unmapped glyph; NUL cannot be stored in PostgreSQL JSONB'))
            page['text'] = text.replace('\x00', '\ufffd')
    sections, skipped = structure(paper, pages, hints)
    paper['schema_version'] = '2.1'
    paper['full_text'] = dict(sections=sections, extraction_status='partial' if sections else 'failed')
    # Keep the legacy flat alias for existing readers; raw page text stays in the original JSONL/DB.
    paper['sections'] = [dict(heading=s['heading'], text='\n\n'.join(p['text'] for p in s['paragraphs']))
                         for s in sections]
    paper['identifiers'].setdefault('openalex_id', None)
    paper['research_metadata'] = dict(methods=[], datasets=[], metrics=[], claimed_contributions=[])
    paper['quality'] = dict(full_text_status=paper['full_text']['extraction_status'],
                            structure_status='heuristic' if any(s['kind']=='identified_heading' for s in sections) else 'page_fallback',
                            qa_status='not_annotated', figures_status='pending', tables_status='pending',
                            references_status='pending', research_metadata_status='pending', equations_status='pending',
                            limitations=['Reading order, section boundaries, equations and object completeness require review.',
                                         'No OCR or visual chart interpretation performed.'])
    paper['provenance'].update(enrichment_parser=REVISION, raw_extraction_path='datasets/processed/arxiv_master.jsonl',
                               raw_record_sha256=digest(original), abstract_separated=skipped,
                               reused_text=True, layout_reordered_pages=repaired,
                               text_normalization=normalizations,
                               year_basis='arxiv_first_submitted_date')
    paper['quality']['unmapped_glyph_count'] = sum(len(n['offsets']) for n in normalizations)
    if normalizations:
        paper['quality']['limitations'].append('Unmapped PDF NUL glyphs represented by U+FFFD; page offsets retained for reconstruction.')
    publication = paper['publication']
    if publication.get('journal_publication_year'):
        paper['year'] = publication['journal_publication_year']
        paper['provenance']['year_basis'] = 'journal_publication_year'
    def section_at(page):
        found = [s for s in sections if page and s['page_start'] <= page <= s['page_end']]
        return found[0]['section_id'] if len(found) == 1 else None

    items, seen = [], set()
    for obj in original.get('figures_and_tables', []):
        obj = copy.deepcopy(obj)
        # Legacy regex also captured prose such as "Figure 1 illustrates ...".
        # Keep the original detection in raw provenance, not as an identified object.
        if re.match(r'^(?:shows?|illustrates?|reports?|summarizes?|presents?|compares?|contains?|gives?|lists?|provides?|yields?|is|are|and|in)\b',
                    obj.get('caption') or '', re.I):
            continue
        key = [obj['type'], obj.get('page'), obj.get('label'), obj.get('caption'), obj.get('data')]
        if digest(key) in seen:
            continue
        seen.add(digest(key))
        oid = stable_id(paper, obj['type'], key)
        obj.update(item_id=oid, section_id=section_at(obj.get('page')), source_bbox=obj.get('source_bbox'),
                   extraction_status='partial', detection_status='unverified_caption_or_cells')
        if obj['type'] == 'table':
            cells = obj.get('data') or []
            # Without header semantics verification, preserve cells as rows and leave headers empty.
            obj.update(table_id=oid, headers=[], rows=cells, units=None, raw_text=obj.get('text'),
                       header_status='unverified')
        else:
            obj.update(figure_id=oid, description=None)
        items.append(obj)
    if pdf_path:
        improve_object_captions(items, pdf_path, paper)
    paper['figures_and_tables'] = items
    # Split bibliography conservatively; uncertain author-year groups remain raw, partial references.
    bibliography = [s for s in sections if re.search(r'\b(?:references|bibliography)\b', s['heading'], re.I)]
    raw_refs = []
    for s in bibliography:
        text = '\n\n'.join(p['text'] for p in s['paragraphs'])
        parts = re.split(r'\n(?=\s*\[\d+\]\s)|\n\n(?=[A-Z])', text)
        raw_refs.extend(p.strip() for p in parts if len(p.strip()) > 25)
    # Retain original fragments only when a bibliography cannot be recognized.
    if not raw_refs:
        raw_refs = [r['raw_reference'] for r in original.get('references', [])]
    references = []
    for raw in dict.fromkeys(raw_refs):
        doi = re.search(r'\b10\.\d{4,9}/[^\s<>]+', raw)
        aid = re.search(r'(?:arXiv:\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+/\d{7}(?:v\d+)?)', raw, re.I)
        year = re.search(r'\b(?:19|20)\d{2}\b', raw)
        references.append(dict(reference_id=stable_id(paper, 'ref', raw), raw_reference=raw,
                               title=None, authors=[], year=int(year[0]) if year else None,
                               doi=normalize_doi(doi[0].rstrip('.,;)')) if doi else None,
                               arxiv_id=arxiv_identity(aid[1])[0] if aid else None,
                               target_paper_id=None, resolution_status='unresolved',
                               citation_contexts=[], extraction_status='partial'))
    paper['references'] = references
    for ref in references:
        label = re.match(r'^\[(\d+)\]', ref['raw_reference'])
        if not label:
            continue
        marker = '['+label[1]+']'
        for s in sections:
            if re.search(r'\b(?:references|bibliography)\b', s['heading'], re.I):
                continue
            for paragraph in s['paragraphs']:
                for occurrence in re.finditer(re.escape(marker), paragraph['text']):
                    context = paragraph['text'][max(0,occurrence.start()-160):occurrence.end()+160]
                    ref['citation_contexts'].append(dict(section_id=s['section_id'],
                        paragraph_id=paragraph['paragraph_id'], page=paragraph['page_start'],
                        marker=marker, quoted_context=context, status='numeric_marker_candidate'))
    paper['equations'] = []
    for kind in ('figures', 'tables'):
        paper['quality'][kind+'_status'] = 'partial' if any(i['type']==kind[:-1] for i in items) else 'pending'
    paper['quality']['references_status'] = 'partial' if references else 'pending'
    return paper


def resolve_references(papers):
    index = {}
    for paper in papers:
        for kind in ('arxiv_id', 'doi'):
            value = paper['identifiers'].get(kind)
            if value:
                index.setdefault((kind, value), set()).add(paper['canonical_id'])
    for paper in papers:
        for ref in paper['references']:
            matches = set()
            for kind in ('arxiv_id', 'doi'):
                matches.update(index.get((kind, ref.get(kind)), set()))
            # A fragment containing several references is not a resolved citation.
            multiple = len(re.findall(r'(?:arXiv:|arxiv\.org/(?:abs|pdf)/|\b10\.\d{4,9}/)', ref['raw_reference'], re.I)) > 1
            if len(matches) == 1 and not multiple:
                ref.update(target_paper_id=next(iter(matches)), resolution_status='resolved')


def qasper_annotations(row, split, paper):
    """Preserve every original annotation. Exact evidence alignment is not verification."""
    qas = row.get('qas') or {}
    if isinstance(qas, dict):
        entries = [{k: values[i] for k, values in qas.items()} for i in range(len(qas.get('question', [])))]
    else:
        entries = qas
    paragraphs = [dict(p, section_id=s['section_id']) for s in paper['full_text']['sections'] for p in s['paragraphs']]
    output = []
    for entry in entries:
        answers = entry.get('answers') or []
        if isinstance(answers, dict):
            answers = answers.get('answer') or []
        for idx, annotation in enumerate(answers):
            original = annotation.get('answer', annotation)
            if not isinstance(original, dict):
                continue
            refs = []
            evidence = original.get('evidence') or []
            for quote in evidence:
                candidates = [p for p in paragraphs if quote and quote in p['text']]
                if len(candidates)==1:
                    p=candidates[0]
                    refs.append(dict(section_id=p['section_id'], paragraph_id=p['paragraph_id'],
                                     table_id=None, figure_id=None, quoted_evidence=quote))
            answer_type = ('unanswerable' if original.get('unanswerable') else 'yes_no' if original.get('yes_no') is not None
                           else 'extractive' if original.get('extractive_spans') else 'free_form')
            answer = (original.get('yes_no') if answer_type=='yes_no' else original.get('extractive_spans')
                      if answer_type=='extractive' else original.get('free_form_answer', ''))
            output.append(dict(qa_id='qasper-'+digest([row['id'], entry.get('question_id'), idx, annotation]),
                               paper_id=paper['canonical_id'], question=entry['question'], answer=answer,
                               answer_type=answer_type, original_annotation=copy.deepcopy(annotation),
                               original_question=copy.deepcopy(entry), original_evidence=evidence,
                               source_dataset='QASPER', source_type='human_annotated', split=split,
                               training_eligible=split=='train', validation_status='human_annotated',
                               alignment_status='aligned_exact' if evidence and len(refs)==len(evidence) else 'alignment_pending',
                               evidence_refs=refs))
    return output


def attach_qasper(root, papers):
    import pyarrow.parquet as pq
    by_id = {p['identifiers']['arxiv_id']: p for p in papers}
    summary = {}
    for split in ('train', 'validation', 'test'):
        count, matched = 0, 0
        for path in sorted((Path(root)/'datasets/raw/qasper/qasper'/split).glob('*.parquet')):
            ids = pq.read_table(path, columns=['id']).column('id').to_pylist()
            selected = [i for i, value in enumerate(ids) if arxiv_identity(value)[0] in by_id]
            count += len(ids)
            if selected:
                for row in pq.read_table(path).take(selected).to_pylist():
                    paper = by_id[arxiv_identity(row['id'])[0]]
                    paper['qas'].extend(qasper_annotations(row, split, paper))
                    paper['quality']['qa_status'] = 'human_annotated'
                    matched += 1
        summary[split] = dict(available_papers=count, matched_papers=matched)
    return summary


def validate_enriched(paper):
    from jsonschema import Draft202012Validator, FormatChecker
    schema = json.loads((Path(__file__).resolve().parents[2]/'schemas/master_dataset_v2_enriched.schema.json').read_text(encoding='utf-8'))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(paper)
    if paper['canonical_id'] != 'arxiv:'+paper['identifiers']['arxiv_id']:
        raise ValueError('Canonical identity mismatch')
    if arxiv_identity(paper['source_id'])[0] != paper['identifiers']['arxiv_id']:
        raise ValueError('Source identity mismatch')
    seen, paragraphs = set(), {}
    for s in paper['full_text']['sections']:
        if s['section_id'] in seen:
            raise ValueError('Duplicate section ID')
        seen.add(s['section_id'])
        for p in s['paragraphs']:
            if p['paragraph_id'] in paragraphs or (p['page_start'] and p['page_end'] and p['page_start']>p['page_end']):
                raise ValueError('Invalid paragraph identity or page range')
            paragraphs[p['paragraph_id']] = (s['section_id'], p['text'])
            for key in ('page_start', 'page_end'):
                if p.get(key) and not 1 <= p[key] <= paper['extraction_quality']['page_count']:
                    raise ValueError('Paragraph page outside source PDF')
    pub = paper['publication']
    expected = pub.get('journal_publication_year') or (int(pub['arxiv_first_submitted_date'][:4]) if pub.get('arxiv_first_submitted_date') else None)
    if paper['year'] != expected:
        raise ValueError('Publication year lacks supporting date')
    if paper['full_text']['extraction_status']=='complete':
        review = paper['quality'].get('completeness_review') or {}
        checks = ('section_boundaries_verified', 'reading_order_verified',
                  'abstract_separation_verified', 'scientific_content_preserved')
        all_pages = list(range(1,paper['extraction_quality']['page_count']+1))
        if (not paragraphs or not paper['quality'].get('reviewed_complete') or
                review.get('pdf_sha256')!=paper['provenance']['pdf_sha256'] or
                review.get('reviewed_pages')!=all_pages or not review.get('reviewer') or
                not all(review.get(check) is True for check in checks)):
            raise ValueError('Completeness requires source-bound structural and scientific quality review')
    for obj in paper['figures_and_tables']:
        if obj.get('section_id') and obj['section_id'] not in seen:
            raise ValueError('Object points to a missing section')
    for ref in paper['references']:
        for context in ref['citation_contexts']:
            value = paragraphs.get(context['paragraph_id'])
            if not value or value[0]!=context['section_id'] or context['quoted_context'] not in value[1]:
                raise ValueError('Citation context is not in the cited source')
    qa_ids = set()
    for qa in paper['qas']:
        if qa['qa_id'] in qa_ids or qa['paper_id']!=paper['canonical_id']:
            raise ValueError('Invalid QA identity')
        qa_ids.add(qa['qa_id'])
        if qa.get('training_eligible') and qa.get('split')!='train':
            raise ValueError('Evaluation leakage')
        for e in qa['evidence_refs']:
            value = paragraphs.get(e['paragraph_id'])
            if not value or value[0]!=e['section_id'] or not e['quoted_evidence'] or e['quoted_evidence'] not in value[1]:
                raise ValueError('Evidence is not in the cited source')
    return paper
