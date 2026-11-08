"""Deterministic section-aware corpus; benchmark answers never become content."""
import hashlib
from bisect import bisect_left
import json
import os
from pathlib import Path
import re
import unicodedata

from app.dataset_inspector import ROOT, sections, paragraphs
from app.ingestion.enrichment import digest

VERSION = 'section-window-1.0-cl100k_base-nfc'


def tokenizer():
    os.environ.setdefault('TIKTOKEN_CACHE_DIR', str(ROOT/'storage/tokenizers'))
    import tiktoken
    return tiktoken.get_encoding('cl100k_base')


def normalize(text):
    # Keep equations, numeric values and citation characters. No speculative
    # dehyphenation, ligature folding, footer deletion or punctuation stripping.
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', text)).strip()


def windows(text, enc, size=400, overlap=60, boundaries=()):
    if size < 2 or not 0 <= overlap < size:
        raise ValueError('Require 0 <= overlap < size and size >= 2')
    tokens = enc.encode(text, disallowed_special=())
    if not tokens:
        return
    decoded, offsets = enc.decode_with_offsets(tokens)
    if decoded != text:
        raise ValueError('Tokenizer failed lossless round-trip')
    offsets = offsets + [len(text)]
    start = 0
    while start < len(tokens):
        end = min(start+size, len(tokens))
        # Offsets can repeat for multiple tokens of a single Unicode character.
        # Slice source characters, then verify the actual re-encoded length.
        while end > start and len(enc.encode(text[offsets[start]:offsets[end]], disallowed_special=())) > size:
            end -= 1
        # Prefer a whole-paragraph end in the latter half of this window. Long
        # paragraphs still split with exact normalized offsets and token overlap.
        candidates=[b for b in boundaries if offsets[start]+(offsets[end]-offsets[start])*.5 < b <= offsets[end]]
        if end < len(tokens) and candidates:
            end=bisect_left(offsets,max(candidates),lo=start+1,hi=end+1)
            while len(enc.encode(text[offsets[start]:offsets[end]],disallowed_special=())) > size:
                end-=1
        if end <= start or offsets[end] <= offsets[start]:
            raise ValueError('Token window cannot advance')
        yield offsets[start], offsets[end], text[offsets[start]:offsets[end]]
        if end == len(tokens):
            break
        following = max(start+1, end-overlap)
        while following < len(tokens) and offsets[following] <= offsets[start]:
            following += 1
        start = following


def build_chunks(paper, enc=None, size=400, overlap=60):
    enc = enc or tokenizer()
    base = dict(paper_id=paper['canonical_id'], source_id=paper['source_id'],
        title=paper['title'], year=paper.get('year'),
        parser_version=paper['provenance'].get('enrichment_parser'), chunker_version=VERSION,
        pdf_sha256=paper['provenance'].get('pdf_sha256'), source_span=None,
        scientific_extraction_status=paper['full_text']['extraction_status'])
    output = []

    def emit(text, content_type, sid, heading, paras=None, atomic=False):
        text = normalize(text)
        if not text:
            return
        spans = [(0,len(text),text)] if atomic else windows(text,enc,size,overlap,
            [p['normalized_end'] for p in (paras or [])])
        for start,end,value in spans:
            mapped = [p for p in (paras or []) if p['normalized_end']>start and p['normalized_start']<end]
            pages = [v for p in mapped for v in (p.get('page_start'),p.get('page_end')) if v]
            count=len(enc.encode(value,disallowed_special=()))
            chunk=dict(base, section_id=sid, section_title=heading, content_type=content_type,
                page_start=min(pages) if pages else None, page_end=max(pages) if pages else None,
                text=value, token_count=count, content_hash=hashlib.sha256(value.encode('utf-8')).hexdigest(),
                normalized_start=start, normalized_end=end, paragraph_ids=[p['paragraph_id'] for p in mapped],
                paragraph_spans=mapped, oversized_atomic=atomic and count>size)
            chunk['chunk_id']='chunk-'+digest([paper['canonical_id'],base['source_id'],sid,
                base['parser_version'], VERSION, size, overlap, start,end,chunk['content_hash']])[:32]
            output.append(chunk)

    emit(paper.get('abstract',''),'abstract',None,'Abstract')
    for section in sections(paper):
        heading = section.get('heading','')
        if (section.get('kind') in ('abstract','retained_annotation_evidence') or
            re.search(r'\b(?:references|bibliography|abstract)\b',heading,re.I)):
            continue
        parts, mappings, cursor = [], [], 0
        for para in paragraphs(section):
            value=normalize(para['text'])
            if not value:
                continue
            mappings.append(dict(paragraph_id=para['paragraph_id'], normalized_start=cursor,
                normalized_end=cursor+len(value), page_start=para.get('page_start'), page_end=para.get('page_end')))
            parts.append(value)
            cursor += len(value)+1
        emit(' '.join(parts),'text',section['section_id'],heading,mappings)
    for obj in paper.get('figures_and_tables',[]):
        if obj['type']=='table':
            value=json.dumps({k:obj.get(k) for k in ('label','caption','headers','rows','units','raw_text','extraction_status')},ensure_ascii=False)
            emit(value,'table',obj['item_id'],obj.get('label') or 'Table',atomic=True)
        elif obj.get('caption'):
            emit(obj['caption'],'figure_caption',obj['item_id'],obj.get('label') or 'Figure',atomic=True)
        for chunk in output:
            if chunk['section_id']==obj['item_id']:
                chunk.update(page_start=obj.get('page'),page_end=obj.get('page'),object_status=obj.get('extraction_status'))
    return output


def align_qas(paper, chunks):
    """Exact source references + normalized interval coverage, never similarity gold."""
    by_pid={p['paragraph_id']:(s['section_id'],p['text']) for s in sections(paper) for p in paragraphs(s)}
    records=[]
    for qa in paper.get('qas',[]):
        evidence=qa.get('evidence_refs',[])
        ids, matched= set(), 0
        for ref in evidence:
            source=by_pid.get(ref.get('paragraph_id'))
            quote=ref.get('quoted_evidence')
            if not source or source[0]!=ref.get('section_id') or not quote or quote not in source[1]:
                continue
            normalized_source=normalize(source[1])
            normalized_quote=normalize(quote)
            # Ambiguous repeated quotes need further review, not an arbitrary span.
            if normalized_source.count(normalized_quote)!=1:
                continue
            offset=normalized_source.index(normalized_quote)
            candidates=[]
            for chunk in chunks:
                if chunk['section_id']!=source[0]:
                    continue
                for span in chunk['paragraph_spans']:
                    if span['paragraph_id']==ref['paragraph_id']:
                        lo=span['normalized_start']+offset
                        hi=lo+len(normalized_quote)
                        left,right=max(lo,chunk['normalized_start']),min(hi,chunk['normalized_end'])
                        if left<right:
                            candidates.append((left,right,chunk['chunk_id']))
            if candidates:
                covered=lo
                for left,right,cid in sorted(candidates):
                    if left<=covered:
                        covered=max(covered,right)
                if covered>=hi:
                    matched+=1
                    ids.update(cid for _,_,cid in candidates)
        expected=qa.get('original_evidence') or [r.get('quoted_evidence') for r in evidence]
        exact=bool(evidence) and matched==len(evidence) and len(evidence)==len(expected)
        status='verified' if exact and qa.get('validation_status')=='human_verified' else 'candidate' if exact else 'insufficient' if ids else 'missing'
        records.append(dict(qa_id=qa['qa_id'],paper_id=paper['canonical_id'],question=qa.get('question'),answer=qa.get('answer'),
            evidence_chunk_ids=sorted(ids),status=status,split=qa.get('split'),source_dataset=qa.get('source_dataset'),
            source_type=qa.get('source_type'),validation_status=qa.get('validation_status'),
            training_eligible=bool(status=='verified' and qa.get('split')=='train' and qa.get('training_eligible') and
                                   qa.get('source_type')=='human_annotated' and qa.get('question') and
                                   qa.get('answer') is not None and qa.get('answer') != '' and qa.get('answer') != [])))
    return records


def training_exports(alignments):
    eligible=[a for a in alignments if a['training_eligible'] and a['status']=='verified' and a['split']=='train' and a['source_type']=='human_annotated']
    retriever=[dict(query=a['question'],positive_chunk_ids=a['evidence_chunk_ids'],negative_chunk_ids=[],source_dataset=a['source_dataset']) for a in eligible]
    llm=[dict(question=a['question'],answer=a['answer'],evidence_chunk_ids=a['evidence_chunk_ids'],citation_ids=a['evidence_chunk_ids'],
              source_dataset=a['source_dataset'],split='train') for a in eligible]
    return retriever,llm
