"""Source preservation, Unicode chunking and evidence/split gates (offline)."""
import copy
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.dataset_inspector import load_file, search, load_postgres
from app.ingestion.enrichment import enrich_record, validate_enriched
from app.retrieval_corpus import normalize, tokenizer, windows, build_chunks, align_qas, training_exports


@pytest.fixture
def paper():
    text='A verified source reports α + β = 42 [7]. ' * 180
    return dict(canonical_id='arxiv:2305.10250',source_id='2305.10250',title='Source',year=2023,
        identifiers={'arxiv_id':'2305.10250','arxiv_version':'v3'},
        abstract='Independent abstract.',provenance={'enrichment_parser':'test','pdf_sha256':'source-hash'},
        full_text={'extraction_status':'partial','sections':[dict(section_id='s1',heading='Methods',kind='identified_heading',page_start=2,page_end=2,
            paragraphs=[dict(paragraph_id='p1',text=text,page_start=2,page_end=2)])]},
        figures_and_tables=[],qas=[])


def test_jsonl_unicode_line_separator_and_search(tmp_path):
    path=tmp_path/'papers.jsonl'
    row=dict(canonical_id='x',title='one\u2028two',identifiers={'doi':'10.1234/test'},year=2023,source='scifact')
    path.write_text(json.dumps(row,ensure_ascii=False)+'\n',encoding='utf-8')
    assert load_file(path)==[row]
    assert search([row],'10.1234/test',2023,'scifact')==[row]
    assert not search([row],year=2022)


def test_token_windows_preserve_unicode_equations_and_all_characters():
    enc=tokenizer()
    text=normalize(('科学 α² + β = 1.23e-4 [12] 👩🏽‍🔬. '*200))
    spans=list(windows(text,enc,400,60))
    assert spans[0][0]==0 and spans[-1][1]==len(text)
    assert all(len(enc.encode(value))<=400 and '\ufffd' not in value for _,_,value in spans)
    assert all(b[0] <= a[1] for a,b in zip(spans,spans[1:]))
    assert spans==list(windows(text,enc,400,60))


def test_source_offsets_determinism_and_atomic_tables(paper):
    enc=tokenizer()
    paper['figures_and_tables']=[dict(type='table',item_id='t',label='Table 1',caption='Observed data',
        headers=['value','unit'],rows=[['42','m/s']]*400,units='m/s',page=3,extraction_status='partial')]
    a=build_chunks(paper,enc)
    assert a==build_chunks(paper,enc)
    assert len({c['chunk_id'] for c in a})==len(a)
    table=[c for c in a if c['content_type']=='table']
    assert len(table)==1 and table[0]['oversized_atomic'] and 'm/s' in table[0]['text']
    for c in a:
        assert c['source_span'] is None
        if c['content_type']=='text':
            assert c['page_start']==2 and c['paragraph_ids']==['p1']
            assert c['text']==normalize(paper['full_text']['sections'][0]['paragraphs'][0]['text'])[c['normalized_start']:c['normalized_end']]
    changed=copy.deepcopy(paper)
    changed['provenance']['enrichment_parser']='new-parser'
    assert {c['chunk_id'] for c in a}.isdisjoint({c['chunk_id'] for c in build_chunks(changed,enc)})


def test_multichunk_evidence_and_train_only_human_verified(paper):
    text=' '.join('word'+str(i) for i in range(1200))
    paper['full_text']['sections'][0]['paragraphs'][0]['text']=text
    quote=' '.join(text.split()[90:600])
    qa=dict(qa_id='q',question='Which words?',answer='word90',original_evidence=[quote],
        evidence_refs=[dict(section_id='s1',paragraph_id='p1',quoted_evidence=quote)],split='train',
        source_dataset='fixture',source_type='human_annotated',validation_status='human_verified',training_eligible=True)
    paper['qas']=[qa]
    chunks=build_chunks(paper)
    aligned=align_qas(paper,chunks)
    assert aligned[0]['status']=='verified' and len(aligned[0]['evidence_chunk_ids'])>1
    assert len(training_exports(aligned)[0])==1
    for key,value in [('split','validation'),('source_type','synthetic'),('validation_status','candidate')]:
        changed=copy.deepcopy(paper)
        changed['qas'][0][key]=value
        assert training_exports(align_qas(changed,chunks))==([],[])
    qa['original_evidence'].append('missing evidence')
    assert align_qas(paper,chunks)[0]['status']=='insufficient'


def test_no_qa_answers_or_bibliography_in_corpus(paper):
    paper['qas']=[dict(answer='SECRET_GOLD_ANSWER')]
    paper['full_text']['sections'].append(dict(section_id='refs',heading='References',kind='identified_heading',
        paragraphs=[dict(paragraph_id='r',text='BIBLIOGRAPHY_ONLY',page_start=3,page_end=3)]))
    text=' '.join(c['text'] for c in build_chunks(paper))
    assert 'SECRET_GOLD_ANSWER' not in text and 'BIBLIOGRAPHY_ONLY' not in text


def test_postgres_inspector_sets_readonly_before_queries(monkeypatch):
    db=MagicMock()
    db.__enter__.return_value=db
    def execute(sql):
        result=MagicMock()
        result.__iter__.return_value=iter([])
        result.fetchall.return_value=[]
        result.fetchone.return_value=(None,)
        return result
    db.execute.side_effect=execute
    monkeypatch.setattr('app.storage.postgres.connect',lambda:db)
    assert load_postgres()==[]
    assert db.execute.call_args_list[0].args[0]=='SET TRANSACTION READ ONLY'
    assert all(not any(word in c.args[0].upper() for word in ('INSERT ', 'UPDATE ', 'DELETE ', 'CREATE ')) for c in db.execute.call_args_list)


def test_enrichment_preserves_body_after_unrecognized_abstract_heading():
    original=json.loads((Path(__file__).resolve().parents[1]/'docs/examples/memgpt_local_pdf_master_v2.json').read_text(encoding='utf-8'))
    original['full_text']=[dict(text='Abstract\nAbstract wording\n\nUnusual body heading\nImportant result = 42.',page_start=1,page_end=1)]
    p=enrich_record(original)
    assert any('Important result = 42.' in v['text'] for s in p['full_text']['sections'] for v in s['paragraphs'])
    p['canonical_id']='arxiv:9999.99999'
    with pytest.raises(ValueError,match='Canonical identity'):
        validate_enriched(p)


def test_pdf_block_recovers_missing_figure_caption_without_inventing_image(tmp_path,paper):
    import pymupdf
    from app.ingestion.enrichment import improve_object_captions
    path=tmp_path/'caption.pdf'
    with pymupdf.open() as doc:
        page=doc.new_page()
        page.insert_textbox((50,80,500,150),'Figure 3: Source caption first line.\nSecond line includes observed value 42.',fontsize=10)
        doc.save(path)
    items=[]
    improve_object_captions(items,path,paper)
    assert len(items)==1 and items[0]['label']=='Figure 3'
    assert 'Second line includes observed value 42.' in items[0]['caption']
    assert items[0]['image_path'] is None and items[0]['extraction_status']=='partial'
    assert items[0]['caption_bbox'] and items[0]['caption_pdf_sha256']


def test_corpus_index_can_be_replaced_idempotently_on_windows(tmp_path,paper):
    from scripts.build_retrieval_corpus import run
    source=tmp_path/'master.jsonl'
    source.write_text(json.dumps(paper)+'\n',encoding='utf-8')
    audit=tmp_path/'audit.json'
    audit.write_text(json.dumps(dict(input=str(source),papers=[dict(canonical_id=paper['canonical_id'],retrieval_ready=True)])),encoding='utf-8')
    output=tmp_path/'corpus'
    first=run(source,audit,output)
    second=run(source,audit,output)
    assert first==second
    assert (output/'corpus.sqlite').is_file()
    assert not (output/'corpus.build.sqlite').exists()
