"""Offline fixtures for the enrichment contract and non-destructive persistence."""
import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pymupdf
import pytest
from jsonschema import ValidationError

from app.ingestion.enrichment import (enrich_record, validate_enriched, resolve_references,
                                      qasper_annotations, digest, layout_adapter)
from app.storage.enrichment import merge_document, upsert_enrichment, MIGRATION


@pytest.fixture
def original():
    # Real source fixture is local and versioned; no network/PDF download required.
    path = Path(__file__).resolve().parents[1]/'docs/examples/memgpt_local_pdf_master_v2.json'
    paper = json.loads(path.read_text(encoding='utf-8'))
    paper['full_text'] = [dict(text='Abstract\nOriginal abstract\n\n1 Introduction\n\nObserved value is 42 [1].\n\n2 Method\n\nAn observed method.\n\nReferences\n\n[1] Author. 2020. arXiv:2005.11401.', page_start=1,page_end=1)]
    paper['sections'] = paper['full_text']
    return paper


def test_schema_stable_ids_and_preserved_source(original):
    before = copy.deepcopy(original)
    a = validate_enriched(enrich_record(original))
    b = validate_enriched(enrich_record(original))
    assert a == b and original==before
    assert a['full_text']['extraction_status']=='partial'
    assert a['provenance']['raw_record_sha256']==digest(original)
    assert any(s['heading']=='Abstract' and any('Original abstract' in p['text'] for p in s['paragraphs']) for s in a['full_text']['sections'])
    assert any('42 [1]' in p['text'] for s in a['full_text']['sections'] for p in s['paragraphs'])
    changed = copy.deepcopy(original)
    changed['identifiers']['arxiv_version']='v99'
    assert enrich_record(changed)['full_text']['sections'][0]['section_id']!=a['full_text']['sections'][0]['section_id']


def test_no_fake_complete_or_year(original):
    p = enrich_record(original)
    p['full_text']['extraction_status']='complete'
    with pytest.raises(ValueError,match='Completeness'):
        validate_enriched(p)
    p=enrich_record(original)
    p['year']=2040
    with pytest.raises(ValueError,match='year'):
        validate_enriched(p)
    original['publication']['journal_publication_year']=2025
    assert validate_enriched(enrich_record(original))['year']==2025


def test_schema_requires_paragraph_evidence_fields(original):
    p=enrich_record(original)
    del p['full_text']['sections'][0]['paragraphs'][0]['paragraph_id']
    with pytest.raises(ValidationError):
        validate_enriched(p)


def test_page_fallback_and_missing_text(original):
    original['full_text']=[dict(text='unstructured source text with α + β = 3',page_start=1)]
    p=validate_enriched(enrich_record(original))
    assert p['quality']['structure_status']=='page_fallback'
    assert p['full_text']['sections'][0]['paragraphs'][0]['text'].endswith('α + β = 3')
    original['full_text']=original['sections']=[]
    assert validate_enriched(enrich_record(original))['full_text']['extraction_status']=='failed'


def test_null_glyph_lossless_positions_for_jsonb(original):
    original['full_text']=[dict(text='Observed\x00glyph',page_start=1)]
    p=validate_enriched(enrich_record(original))
    assert '\\u0000' not in json.dumps(p)
    assert p['quality']['unmapped_glyph_count']==1
    note=p['provenance']['text_normalization'][0]
    text=p['full_text']['sections'][0]['paragraphs'][0]['text']
    assert text[note['offsets'][0]]=='\ufffd'
    assert text[:8]+'\x00'+text[9:]==original['full_text'][0]['text']


def test_reference_exact_resolution_and_ambiguous_fragment(original):
    p=enrich_record(original)
    other=copy.deepcopy(p)
    other['canonical_id']='arxiv:2005.11401'
    other['identifiers']['arxiv_id']='2005.11401'
    resolve_references([p,other])
    assert p['references'][0]['target_paper_id']=='arxiv:2005.11401'
    p['references'][0]['raw_reference']+=' arXiv:2310.08560'
    p['references'][0].update(target_paper_id=None,resolution_status='unresolved')
    resolve_references([p,other])
    assert p['references'][0]['target_paper_id'] is None


def test_original_qasper_annotations_and_no_leakage(original):
    p=enrich_record(original)
    annotation=dict(unanswerable=False,yes_no=None,free_form_answer='42',extractive_spans=[],evidence=['Observed value is 42 [1].'])
    row=dict(id=p['source_id'],qas={'question':['Which value?'],'question_id':['q1'],
                                  'answers':[{'answer':[annotation]}]})
    p['qas']=qasper_annotations(row,'validation',p)
    qa=p['qas'][0]
    assert qa['original_annotation']==annotation
    assert qa['alignment_status']=='aligned_exact'
    assert qa['validation_status']=='human_annotated' and not qa['training_eligible']
    validate_enriched(p)
    qa['training_eligible']=True
    with pytest.raises(ValueError,match='leakage'):
        validate_enriched(p)
    qa['training_eligible']=False
    qa['evidence_refs'][0]['quoted_evidence']='a fabricated claim'
    with pytest.raises(ValueError,match='Evidence'):
        validate_enriched(p)


def test_unaligned_evidence_not_verified(original):
    p=enrich_record(original)
    row=dict(id=p['source_id'],qas=[dict(question='Q?',question_id='q',answers=[dict(answer=dict(evidence=['not found'],free_form_answer='original'))])])
    qa=qasper_annotations(row,'train',p)[0]
    assert qa['original_evidence']==['not found'] and qa['alignment_status']=='alignment_pending'
    assert qa['evidence_refs']==[]


def test_partial_objects_no_invented_headers(original):
    original['figures_and_tables']=[dict(type='table',item_id='old',label='Table 1',caption='caption',data=[['method','score'],['A','42']],text=None,image_path=None,page=1,extraction_status='partial')]
    p=validate_enriched(enrich_record(original))
    obj=p['figures_and_tables'][0]
    assert obj['rows'][1]==['A','42'] and obj['headers']==[] and obj['units'] is None
    assert obj['extraction_status']=='partial'


def test_merge_correction_downgrade_and_verified_annotations(original):
    old=enrich_record(original)
    new=copy.deepcopy(old)
    new['full_text']['sections'][0]['paragraphs'][0]['text']='Corrected source'
    assert merge_document(old,new)['full_text']==new['full_text']
    assert merge_document(old,old)==old
    new['full_text']=dict(sections=[],extraction_status='failed')
    merged=merge_document(old,new)
    assert merged['full_text']==old['full_text']


def test_parser_revision_does_not_duplicate_reference_or_figure(original):
    old=enrich_record(original)
    new=copy.deepcopy(old)
    for ref in new['references']:
        ref['reference_id']='new-parser-'+ref['reference_id']
    for obj in new['figures_and_tables']:
        obj['item_id']='new-parser-'+obj['item_id']
    merged=merge_document(old,new)
    assert len(merged['references'])==len(new['references'])
    assert len(merged['figures_and_tables'])==len(new['figures_and_tables'])
    assert merged['references'][0]['reference_id']==new['references'][0]['reference_id']


def test_additive_migration_and_actual_unchanged_import(original):
    assert not any(word in sql.upper() for sql in MIGRATION for word in ('DROP ','TRUNCATE ','DELETE '))
    p=enrich_record(original)
    db=Mock()
    def execute(sql,args=None):
        out=Mock()
        out.fetchone.return_value=(p,digest(p)) if 'SELECT document' in sql else (1,)
        return out
    db.execute.side_effect=execute
    assert upsert_enrichment(db,p)=='unchanged'
    assert not any('INSERT' in c.args[0] or 'UPDATE SET' in c.args[0] for c in db.execute.call_args_list)


def test_two_column_order_fixture(tmp_path):
    path=tmp_path/'columns.pdf'
    with pymupdf.open() as doc:
        page=doc.new_page()
        page.insert_textbox((40,80,280,700),'LEFT source text. '*80,fontsize=10)
        page.insert_textbox((320,80,555,700),'RIGHT source text. '*80,fontsize=10)
        doc.save(path)
    pages,_,repaired=layout_adapter(path,[dict(text='incorrect interleaving',page_start=1)])
    assert repaired==[1]
    assert pages[0]['text'].rfind('LEFT') < pages[0]['text'].find('RIGHT')


def test_synthetic_disabled_before_database_access():
    from scripts.enrich_arxiv import run
    with pytest.raises(ValueError,match='provider'):
        run(generate_qa=True)


def test_optin_synthetic_discards_hallucination_and_deduplicates(original, monkeypatch):
    from app.ingestion.synthetic_qa import generate_candidates
    p=enrich_record(original)
    para=next(v for s in p['full_text']['sections'] for v in s['paragraphs'] if '42' in v['text'])
    valid=dict(question='Which value?',answer='42',paragraph_id=para['paragraph_id'],quoted_evidence=para['text'])
    fake=dict(valid,question='Another?',answer='99')
    reply=Mock()
    reply.json.return_value={'qas':[valid,valid,fake]}
    post=Mock(return_value=reply)
    monkeypatch.setattr('app.ingestion.synthetic_qa.requests.post',post)
    candidates=generate_candidates(p,provider='grounded-json-http',model='test-model',endpoint='http://localhost/qa')
    assert len(candidates)==1 and candidates[0]['validation_status']=='candidate'
    assert candidates[0]['split'] is None and not candidates[0]['training_eligible']
    p['qas']=candidates
    validate_enriched(p)


def test_annotation_survives_reprocessing_with_changed_paragraph(original):
    old=enrich_record(original)
    annotation=dict(unanswerable=False,yes_no=None,free_form_answer='42',evidence=['Observed value is 42 [1].'])
    row=dict(id=old['source_id'],qas=[dict(question='Which value?',question_id='q',answers=[annotation])])
    old['qas']=qasper_annotations(row,'train',old)
    old['qas'][0]['validation_status']='human_verified'
    new=enrich_record(original)
    for s in new['full_text']['sections']:
        for p in s['paragraphs']:
            if '42' in p['text']:
                p['paragraph_id']='corrected-paragraph'
                p['text']='Corrected extraction'
    merged=validate_enriched(merge_document(old,new))
    assert merged['qas'][0]['validation_status']=='human_verified'
    assert merged['qas'][0]['evidence_refs'][0]['quoted_evidence']=='Observed value is 42 [1].'


def test_resolved_reference_context_survives_parser_revision(original):
    old=enrich_record(original)
    old['references'][0].update(resolution_status='resolved',target_paper_id='arxiv:2005.11401')
    assert old['references'][0]['citation_contexts']
    new=copy.deepcopy(enrich_record(original))
    new['references']=[]
    for section in new['full_text']['sections']:
        section['section_id']='new-'+section['section_id']
        for para in section['paragraphs']:
            para['paragraph_id']='new-'+para['paragraph_id']
    merged=validate_enriched(merge_document(old,new))
    assert merged['references'][0]['target_paper_id']=='arxiv:2005.11401'
    context=merged['references'][0]['citation_contexts'][0]
    assert any(p['paragraph_id']==context['paragraph_id'] for s in merged['full_text']['sections'] for p in s['paragraphs'])


def test_retained_object_does_not_keep_dangling_section(original):
    old=enrich_record(original)
    sid=old['full_text']['sections'][0]['section_id']
    old['figures_and_tables']=[dict(type='figure',item_id='f',figure_id='f',label='Figure 1',
        caption='Original source',page=1,section_id=sid,extraction_status='partial')]
    new=copy.deepcopy(enrich_record(original))
    new['figures_and_tables']=[]
    new['references']=[]
    for section in new['full_text']['sections']:
        section['section_id']='new-'+section['section_id']
    merged=merge_document(old,new)
    assert merged['figures_and_tables'][0]['section_id'] is None
    assert merged['figures_and_tables'][0]['previous_section_id']==sid


def test_pdf_typography_rejects_body_mentions_and_joins_heading_numbers(tmp_path, original):
    path=tmp_path/'headings.pdf'
    with pymupdf.open() as doc:
        page=doc.new_page()
        page.insert_text((40,80),'1',fontsize=14,fontname='hebo')
        page.insert_text((65,80),'Introduction',fontsize=14,fontname='hebo')
        page.insert_textbox((40,105,500,300),'Body text with a source. '*25,fontsize=10)
        doc.save(path)
    original['full_text']=[dict(text='1  Introduction\n\nBody text with a source.',page_start=1)]
    p=validate_enriched(enrich_record(original,path))
    assert any(s['heading']=='1  Introduction' for s in p['full_text']['sections'])
