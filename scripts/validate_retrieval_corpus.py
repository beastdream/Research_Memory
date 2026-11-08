"""Offline validation of the generated corpus, source links and deterministic IDs."""
import json
from pathlib import Path
import sqlite3
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.dataset_inspector import DEFAULT,load_file
from app.retrieval_corpus import tokenizer,build_chunks,align_qas
from app.ingestion.enrichment import digest


def run():
    folder=ROOT/'datasets/retrieval_corpus'
    with (folder/'chunks.jsonl').open(encoding='utf-8') as stream:
        chunks=[json.loads(line) for line in stream if line.strip()]
    with (folder/'qa_evidence_alignment.jsonl').open(encoding='utf-8') as stream:
        alignment=[json.loads(line) for line in stream if line.strip()]
    report=json.loads((ROOT/'reports/master_dataset_audit.json').read_text(encoding='utf-8'))
    ready={p['canonical_id'] for p in report['papers'] if p['retrieval_ready']}
    papers=load_file(DEFAULT)
    enc=tokenizer()
    rebuilt,realigned=[],[]
    for paper in papers:
        if paper['canonical_id'] in ready:
            current=build_chunks(paper,enc)
            rebuilt.extend(current)
            realigned.extend(align_qas(paper,current))
    ids={c['chunk_id'] for c in chunks}
    assert len(ids)==len(chunks),'duplicate chunks'
    assert ready=={c['paper_id'] for c in chunks},'paper gate mismatch'
    assert rebuilt==chunks,'non-deterministic or stale corpus'
    assert realigned==alignment,'stale evidence alignment'
    for chunk in chunks:
        assert chunk['token_count']==len(enc.encode(chunk['text'],disallowed_special=()))
        assert chunk['token_count']<=400 or chunk['oversized_atomic']
    for row in alignment:
        assert set(row['evidence_chunk_ids'])<=ids
        if row['training_eligible']:
            assert row['split']=='train' and row['status']=='verified' and row['source_type']=='human_annotated'
    with sqlite3.connect((folder/'corpus.sqlite').resolve().as_uri()+'?mode=ro',uri=True) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('SELECT count(*) FROM chunks').fetchone()[0]==len(chunks)
        for chunk in chunks:
            stored=json.loads(db.execute('SELECT document FROM chunks WHERE chunk_id=?',(chunk['chunk_id'],)).fetchone()[0])
            assert stored==chunk
    result=dict(status='passed',papers=len(ready),chunks=len(chunks),deterministic_rebuild=True,
        source_links_equal_rebuild=True,all_token_counts_verified=True,sqlite_integrity='ok',
        alignment_records=len(alignment),corpus_digest=digest(chunks),
        leakage_policy='Only source content; exact human-verified train labels may be exported. No selected QA exists.',
        scientific_completeness_verified=False)
    (ROOT/'reports/corpus_validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    run()
