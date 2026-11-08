"""Audit selected/imported papers against PDFs and read-only PostgreSQL snapshots."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.dataset_inspector import DEFAULT, load_file, sections
from app.master_audit import audit_paper
from app.ingestion.enrichment import digest
from scripts.enrich_arxiv import write_jsonl


def database_check(papers):
    try:
        from app.storage.postgres import connect
        with connect() as db:
            db.execute('SET TRANSACTION READ ONLY')
            db.execute("SET LOCAL statement_timeout='30s'")
            selection = [dict(canonical_id=c, source_id=s) for c,s in db.execute(
                "SELECT DISTINCT canonical_id,source_id FROM paper_sources WHERE source='arxiv' ORDER BY canonical_id")]
            stored = dict(db.execute('SELECT canonical_id,document FROM paper_enrichments'))
            snapshots = dict(db.execute("SELECT canonical_id,master_json FROM paper_master_sources WHERE source='arxiv'"))
            raw_path=ROOT/'datasets/processed/arxiv_master.jsonl'
            raw={p['canonical_id']:p for p in load_file(raw_path)} if raw_path.is_file() else {}
            local = {p['canonical_id']:p for p in papers}
            consistency = [dict(canonical_id=c, present=c in stored,
                export_matches=digest(local[c])==digest(stored[c]) if c in local and c in stored else False)
                for c in sorted({s['canonical_id'] for s in selection})]
            return dict(status='verified_read_only', selection=selection,
                sources=dict(db.execute('SELECT source,count(*) FROM paper_sources GROUP BY source')),
                tables={t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in
                    ('papers','paper_sources','sections','paper_master_sources','paper_master_versions','paper_enrichments','paper_enrichment_history')},
                arxiv_sections=db.execute("SELECT count(DISTINCT canonical_id),count(*) FROM sections WHERE source='arxiv'").fetchone(),
                consistency=consistency, snapshot_count=len(snapshots),
                raw_jsonl_snapshot_matches=sum(c in raw and digest(p)==digest(raw[c]) for c,p in snapshots.items()),
                missing_raw_snapshots=sorted(set(local)-set(snapshots)))
    except Exception as error:
        return dict(status='blocked', reason=type(error).__name__, selection=[])


def run(path=DEFAULT, output=ROOT/'reports'):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    all_papers = load_file(path)
    database = database_check(all_papers)
    if database['status'] == 'verified_read_only':
        selected = {s['canonical_id'] for s in database['selection']}
        selection_source = 'live PostgreSQL paper_sources (read-only)'
    else:
        manifest = load_file_manifest(ROOT/'datasets/manifests/arxiv_enrichment_selected.jsonl')
        selected = {r['canonical_id'] for r in manifest}
        selection_source = 'offline selected manifest; import status not verified live'
    papers = [p for p in all_papers if p['canonical_id'] in selected]
    rows = []
    for n,paper in enumerate(papers,1):
        rows.append(audit_paper(paper))
        if n%10==0:
            print(f'Audited {n}/{len(papers)}', file=sys.stderr, flush=True)
    absent = selected-{p['canonical_id'] for p in papers}
    failures = [dict(canonical_id=r['canonical_id'], retrieval_ready=r['retrieval_ready'],
        issues=r['issues'], retrieval_blockers=r['retrieval_blockers']) for r in rows if r['issues'] or not r['retrieval_ready']]
    failures.extend(dict(canonical_id=c, issues=['selected_paper_missing_from_export']) for c in sorted(absent))
    summary = dict(selected_papers=len(selected), audited_papers=len(rows), unique_canonical_papers=len({p['canonical_id'] for p in papers}),
        duplicate_canonical_papers=len(papers)-len({p['canonical_id'] for p in papers}),
        missing_selected_papers=sorted(absent), imported_arxiv_papers=len(selected) if database['status']=='verified_read_only' else None,
        valid_pdf_papers=sum(r['has_pdf'] for r in rows), usable_full_text_papers=sum(r['full_text_usable'] for r in rows),
        structured_section_papers=sum(any(s.get('kind')=='identified_heading' for s in sections(p)) for p in papers),
        **{f'papers_with_{key}':sum(r[field]>0 for r in rows) for key,field in
           [('figures','figure_count'),('tables','table_count'),('references','reference_count'),('qas','qa_count'),('verified_qa_evidence','verified_qa_count')]},
        retrieval_ready_papers=sum(r['retrieval_ready'] for r in rows), qa_training_ready_papers=sum(r['qa_training_ready'] for r in rows),
        fully_complete_papers=sum(r['structurally_complete'] for r in rows),
        issue_counts=dict(Counter(i.split(':')[0] for r in rows for i in r['issues'])))
    report = dict(audit_date='2026-10-25', input=str(path), input_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(), selection_source=selection_source,
        branch='A: retain partial extraction, improve verified defects; B only for individually retrieval-ready papers',
        criteria='Schema + canonical identity + PDF checksum + headings + paragraph page ranges + per-page word coverage >= 0.80 on text-rich pages. Retrieval readiness is provisional usability, not scientific completeness. QA is independent.',
        summary=summary, postgres=database, papers=rows)
    (output/'master_dataset_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    write_jsonl(output/'master_dataset_failures.jsonl',failures)
    fields=[k for k in rows[0] if k!='page_checks'] if rows else ['canonical_id']
    with (output/'master_dataset_audit.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k:json.dumps(row[k],ensure_ascii=False) if isinstance(row[k],list) else row[k] for k in fields})
    text=['# Master Dataset audit — 25 October 2026','',report['branch'],'',report['criteria'],'',
          'Selection: '+selection_source,'','## Summary','']
    text.extend(f'- {k}: {v}' for k,v in summary.items())
    text.extend(['','## Verification boundaries','',
        '- Repository: all selected exported documents validated and compared page by page to local PDF text. Coverage checks cannot prove equations, reading order or table semantics.',
        '- PostgreSQL: '+database['status']+'. Current results are in the JSON report; historical documentation is not live evidence.',
        '- Export-only differences are recorded in postgres.consistency. No production writes occur in this audit.',
        '- Visual sample review is separate in manual_pdf_review.md. Unknown figure/table absence is never counted as a failure merely because an array is empty.',
        '- References count fragments, not verified bibliography entries. No generated QA or training is performed.','',
        '## Per-paper decision','', '| ID | Retrieval | QA train | Issues |','| --- | --- | --- | --- |'])
    text.extend(f"| {r['canonical_id']} | {r['retrieval_ready']} | {r['qa_training_ready']} | {'; '.join(r['issues'])} |" for r in rows)
    (output/'master_dataset_audit.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
    return report


def load_file_manifest(path):
    with Path(path).open(encoding='utf-8-sig') as stream:
        return [json.loads(line) for line in stream if line.strip()]


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=DEFAULT)
    parser.add_argument('--output',type=Path,default=ROOT/'reports')
    args=parser.parse_args()
    result=run(args.input,args.output)
    print(json.dumps(result['summary'],indent=2))
