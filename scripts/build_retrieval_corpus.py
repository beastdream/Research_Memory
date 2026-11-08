"""Export individually gated papers to JSONL and a read-only-queryable SQLite index."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.dataset_inspector import DEFAULT,load_file
from app.ingestion.enrichment import digest
from app.retrieval_corpus import tokenizer,build_chunks,align_qas,training_exports,VERSION
from scripts.enrich_arxiv import write_jsonl


def run(path=DEFAULT,audit=ROOT/'reports/master_dataset_audit.json',output=ROOT/'datasets/retrieval_corpus'):
    report=json.loads(Path(audit).read_text(encoding='utf-8'))
    if Path(report['input']).resolve()!=Path(path).resolve():
        raise ValueError('Audit does not describe this input')
    ready={r['canonical_id'] for r in report['papers'] if r['retrieval_ready']}
    papers=load_file(path)
    if report.get('input_sha256'):
        import hashlib
        if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=report['input_sha256']:
            raise ValueError('Input changed after audit')
    output=Path(output)
    output.mkdir(parents=True,exist_ok=True)
    enc=tokenizer()
    chunks,alignment=[],[]
    for paper in papers:
        if paper['canonical_id'] not in ready:
            continue
        current=build_chunks(paper,enc)
        chunks.extend(current)
        alignment.extend(align_qas(paper,current))
    if len({c['chunk_id'] for c in chunks})!=len(chunks):
        raise ValueError('Duplicate chunk IDs')
    write_jsonl(output/'chunks.jsonl',chunks)
    write_jsonl(output/'qa_evidence_alignment.jsonl',alignment)
    retriever,llm=training_exports(alignment)
    write_jsonl(output/'retriever_train.jsonl',retriever)
    write_jsonl(output/'llm_train.jsonl',llm)
    # Build a new derived index, then atomically replace it. Never touch source DB.
    temp=output/'corpus.build.sqlite'
    if temp.exists():
        temp.unlink()
    with sqlite3.connect(temp) as db:
        db.execute('CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY,paper_id TEXT,year INTEGER,section TEXT,content_type TEXT,source_id TEXT,document TEXT)')
        db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?,?)',[(c['chunk_id'],c['paper_id'],c['year'],c['section_id'],c['content_type'],c['source_id'],json.dumps(c,ensure_ascii=False)) for c in chunks])
        for field in ('paper_id','year','section','content_type','source_id'):
            db.execute(f'CREATE INDEX chunks_{field} ON chunks({field})')
    db.close()  # sqlite's transaction context does not close handles on Windows.
    temp.replace(output/'corpus.sqlite')
    lengths=sorted(c['token_count'] for c in chunks)
    manifest=dict(chunker_version=VERSION,tokenizer=enc.name,size=400,overlap=60,papers=len(ready),chunks=len(chunks),
        content_types=dict(Counter(c['content_type'] for c in chunks)),alignment_records=len(alignment),
        retriever_training_records=len(retriever),llm_training_records=len(llm),
        corpus_digest=digest(chunks),alignment_digest=digest(alignment),
        token_distribution={k:lengths[min(len(lengths)-1,int((len(lengths)-1)*v))] if lengths else 0 for k,v in [('min',0),('p50',.5),('p95',.95),('max',1)]},
        oversized_atomic_objects=sum(c['oversized_atomic'] for c in chunks),
        limitations=['Provisional retrieval corpus from partial scientific extraction.','Tables and captions remain partial; atomic objects may exceed 400 tokens.','Normalized offsets are not PDF character spans. Source_span stays null.','No QA answers are added to corpus text.'])
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=DEFAULT)
    parser.add_argument('--audit',type=Path,default=ROOT/'reports/master_dataset_audit.json')
    parser.add_argument('--output',type=Path,default=ROOT/'datasets/retrieval_corpus')
    parser.add_argument('--chunk-id',help='Read one chunk from the existing index without rebuilding')
    parser.add_argument('--paper-id',help='Query existing corpus by canonical paper ID')
    parser.add_argument('--year',type=int)
    parser.add_argument('--section')
    parser.add_argument('--content-type')
    parser.add_argument('--source-id')
    parser.add_argument('--limit',type=int,default=20)
    args=parser.parse_args()
    filters={key:value for key,value in {'chunk_id':args.chunk_id,'paper_id':args.paper_id,
        'year':args.year,'section':args.section,'content_type':args.content_type,'source_id':args.source_id}.items() if value is not None}
    if filters:
        with sqlite3.connect((args.output/'corpus.sqlite').resolve().as_uri()+'?mode=ro',uri=True) as db:
            sql='SELECT document FROM chunks WHERE '+' AND '.join(k+'=?' for k in filters)+' ORDER BY chunk_id LIMIT ?'
            for row in db.execute(sql,(*filters.values(),max(1,args.limit))):
                print(row[0])
    else:
        print(json.dumps(run(args.input,args.audit,args.output),indent=2))
