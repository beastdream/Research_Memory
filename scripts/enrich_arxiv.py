"""Enrich only existing imported arXiv papers, offline; never download PDFs."""
import argparse
import copy
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.ingestion.enrichment import (enrich_record, resolve_references, attach_qasper,
                                      validate_enriched, digest, REVISION)
from app.ingestion.arxiv_client import valid_pdf, sha256
from app.ingestion.arxiv_pipeline import atomic_json
from app.storage.postgres import connect


def read_jsonl(path):
    with Path(path).open(encoding='utf-8-sig') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.jsonl.tmp')
    with tmp.open('w', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False)+'\n')
    tmp.replace(path)


def snapshot(db):
    """Read-only selection and preservation fingerprints, never credentials."""
    selected = db.execute("""SELECT DISTINCT p.canonical_id,s.source_id FROM papers p
                              JOIN paper_sources s USING(canonical_id) WHERE s.source='arxiv'
                              ORDER BY p.canonical_id,s.source_id""").fetchall()
    fingerprints = {}
    for table, order in [('papers','canonical_id'), ('paper_sources','canonical_id,source,source_id,dataset_split'),
                         ('sections','canonical_id,source,section_idx'), ('paper_master_sources','canonical_id,source,source_id'),
                         ('paper_master_versions','canonical_id,source,source_id,pdf_sha256')]:
        # Streaming MD5 at the server avoids transferring full text just to audit preservation.
        fingerprints[table] = db.execute(f"SELECT count(*),md5(string_agg(md5(t::text),'' ORDER BY {order})) FROM {table} t").fetchone()
    return selected, fingerprints


def run(root=ROOT, limit=None, import_db=False, generate_qa=False, qa_provider=None, qa_model=None, qa_endpoint=None):
    if generate_qa and not (qa_provider and qa_model and qa_endpoint):
        raise ValueError('Synthetic QA requires an explicitly configured provider/model adapter; no API was called')
    root = Path(root)
    out = root/'datasets/master'
    if limit:
        out = out/'pilot'
    out.mkdir(parents=True, exist_ok=True)
    cached = {p['canonical_id']:p for p in read_jsonl(out/'arxiv_master_v2.jsonl')} if (out/'arxiv_master_v2.jsonl').exists() else {}
    with connect() as db:
        db.execute('SET TRANSACTION READ ONLY')
        selected, before = snapshot(db)
        source_sections = db.execute("SELECT count(distinct canonical_id),count(*) FROM sections WHERE source='arxiv'").fetchone()
        # Prefer the exact imported snapshot over a possibly stale JSONL.
        imported = {cid: doc for cid, doc in db.execute("SELECT canonical_id,master_json FROM paper_master_sources WHERE source='arxiv'")}
    local = {r['canonical_id']: r for r in read_jsonl(root/'datasets/processed/arxiv_master.jsonl')}
    if len({cid for cid,_ in selected}) != len(selected):
        raise ValueError('Multiple arXiv source mappings per canonical paper require review')
    if limit:
        # Deliberately varied eras/templates, when available.
        sample = ['1410.3916','2005.11401','2305.10250','2404.16130','2609.38473']
        selected = sorted(selected, key=lambda item: (item[1] not in sample, sample.index(item[1]) if item[1] in sample else item[1]))[:limit]
    elif not (out/'pilot/quality_review.json').exists():
        raise ValueError('Run the five-paper pilot and record its visual quality review before full enrichment')
    else:
        review = json.loads((out/'pilot/quality_review.json').read_text(encoding='utf-8'))
        if not review.get('accepted_for_partial_enrichment') or len(review.get('papers',[]))<5:
            raise ValueError('Pilot review does not meet five-paper acceptance gate')
        pilot = read_jsonl(out/'pilot/arxiv_master_v2.jsonl')
        if review.get('pilot_digest') != digest(pilot) or review.get('parser_revision')!=REVISION:
            raise ValueError('Pilot parser/output changed; repeat quality review')
    manifest, papers, failures = [], [], []
    reused = 0
    for processed, (cid, source_id) in enumerate(selected, 1):
        original = imported.get(cid) or local.get(cid)
        if not original:
            failures.append(dict(canonical_id=cid, reason='missing_imported_snapshot'))
            continue
        original = dict(original, canonical_id=cid)
        pdf = Path(original['provenance']['pdf_path'])
        if not pdf.is_absolute():
            pdf = root/pdf
        valid = valid_pdf(pdf)
        checksum_ok = valid and sha256(pdf)==original['provenance']['pdf_sha256']
        manifest.append(dict(canonical_id=cid, source_id=source_id, selection_source='postgresql.paper_sources',
                             pdf_path=str(pdf), matched_pdf=pdf.exists(), valid_pdf=valid, checksum_matches=checksum_ok))
        try:
            prior = cached.get(cid)
            if (checksum_ok and prior and prior.get('schema_version')=='2.1' and
                    prior['provenance'].get('enrichment_parser')==REVISION and
                    prior['provenance'].get('raw_record_sha256')==digest(original)):
                paper = copy.deepcopy(prior)
                reused += 1
            else:
                paper = enrich_record(original, pdf if checksum_ok else None)
            paper['provenance']['raw_extraction_source'] = 'postgresql.paper_master_sources' if cid in imported else 'local_jsonl'
            paper['quality']['pdf_valid'] = valid
            paper['quality']['pdf_checksum_matches'] = checksum_ok
            if not checksum_ok:
                paper['quality']['limitations'].append('Local PDF unavailable or checksum changed; stored extraction reused.')
                failures.append(dict(canonical_id=cid, reason='pdf_unavailable_or_checksum_mismatch'))
            papers.append(paper)
        except (ValueError, KeyError, TypeError) as error:
            failures.append(dict(canonical_id=cid, reason=type(error).__name__))
        if processed % 10 == 0:
            print(f'Enriched {processed}/{len(selected)} stored papers', file=sys.stderr, flush=True)
    resolve_references(papers)
    qasper = attach_qasper(root, papers)
    if generate_qa:
        import os
        from app.ingestion.synthetic_qa import generate_candidates
        for paper in papers:
            candidates = generate_candidates(paper, provider=qa_provider, model=qa_model,
                                              endpoint=qa_endpoint, api_key=os.environ.get('QA_API_KEY'))
            paper['qas'].extend(candidates)
            if candidates:
                paper['quality']['qa_status']='synthetic_candidate'
    for paper in papers:
        paper['qas'] = list({q['qa_id']:q for q in paper['qas']}.values())
        validate_enriched(paper)
    write_jsonl(root/'datasets/manifests'/('arxiv_enrichment_pilot.jsonl' if limit else 'arxiv_enrichment_selected.jsonl'), manifest)
    write_jsonl(out/'arxiv_master_v2.jsonl', papers)
    write_jsonl(out/'arxiv_enrichment_failures.jsonl', failures)
    write_jsonl(out/'qa_candidates.jsonl', [qa for p in papers for qa in p['qas'] if qa['source_type']=='synthetic'])
    write_jsonl(out/'qa_verified.jsonl', [qa for p in papers for qa in p['qas'] if qa['validation_status']=='human_verified'])
    write_jsonl(out/'qa_human_annotated.jsonl', [qa for p in papers for qa in p['qas'] if qa['source_type']=='human_annotated'])
    counts = Counter()
    after = before
    if import_db:
        from app.storage.enrichment import migrate, upsert_enrichment
        with connect() as db:
            migrate(db)
            for paper in papers:
                # Isolated savepoint permits retry/reporting while preserving successful records.
                try:
                    with db.transaction():
                        counts[upsert_enrichment(db, paper)] += 1
                except Exception as error:
                    counts['failed'] += 1
                    failures.append(dict(canonical_id=paper['canonical_id'], reason='import_'+type(error).__name__))
            _, after = snapshot(db)
            if after != before:
                raise ValueError('Legacy preservation audit failed; transaction rolled back')
            # The stored master may preserve richer evidence/verified annotations.
            # Export that authoritative merged document, not a poorer incoming copy.
            committed = {cid:doc for cid,doc in db.execute('SELECT canonical_id,document FROM paper_enrichments WHERE canonical_id=ANY(%s)',
                                                         ([p['canonical_id'] for p in papers],))}
            papers = [committed.get(p['canonical_id'],p) for p in papers]
        write_jsonl(out/'arxiv_enrichment_failures.jsonl', failures)
        write_jsonl(out/'arxiv_master_v2.jsonl', papers)
        write_jsonl(out/'qa_candidates.jsonl', [q for p in papers for q in p['qas'] if q['source_type']=='synthetic'])
        write_jsonl(out/'qa_verified.jsonl', [q for p in papers for q in p['qas'] if q['validation_status']=='human_verified'])
        write_jsonl(out/'qa_human_annotated.jsonl', [q for p in papers for q in p['qas'] if q['source_type']=='human_annotated'])
    report = dict(selected_arxiv_papers=len(selected), exported_papers=len(papers),
                  matched_pdf_count=sum(m['matched_pdf'] for m in manifest),
                  missing_pdf_count=sum(not m['matched_pdf'] for m in manifest),
                  valid_pdf_count=sum(m['valid_pdf'] for m in manifest),
                  full_text_complete_count=sum(p['full_text']['extraction_status']=='complete' for p in papers),
                  full_text_partial_count=sum(p['full_text']['extraction_status']=='partial' for p in papers),
                  identified_section_papers=sum(p['quality']['structure_status']=='heuristic' for p in papers),
                  papers_with_tables=sum(any(i['type']=='table' for i in p['figures_and_tables']) for p in papers),
                  papers_with_figures=sum(any(i['type']=='figure' for i in p['figures_and_tables']) for p in papers),
                  papers_with_table_cells=sum(any(i['type']=='table' and i['rows'] for i in p['figures_and_tables']) for p in papers),
                  papers_with_references=sum(bool(p['references']) for p in papers),
                  reference_count=sum(len(p['references']) for p in papers),
                  resolved_reference_count=sum(r['resolution_status']=='resolved' for p in papers for r in p['references']),
                  papers_with_human_qa=sum(any(q['source_type']=='human_annotated' for q in p['qas']) for p in papers),
                  papers_with_synthetic_qa=sum(any(q['source_type']=='synthetic' for q in p['qas']) for p in papers),
                  papers_with_verified_qa=sum(any(q['validation_status']=='human_verified' for q in p['qas']) for p in papers),
                  qa_counts=dict(Counter(q['source_type'] for p in papers for q in p['qas'])),
                  duplicate_canonical_id_count=len(papers)-len({p['canonical_id'] for p in papers}),
                  missing_year_count=sum(p['year'] is None for p in papers),
                  postgres={key:counts[key] for key in ('inserted','updated','unchanged','failed')},
                  extraction_failures_by_reason=dict(Counter(f['reason'] for f in failures)),
                  qasper=qasper, legacy_before=before, legacy_after=after, legacy_unchanged=before==after,
                  existing_arxiv_sections=dict(papers=source_sections[0], sections=source_sections[1]),
                  parser_revision=REVISION, reused_enriched_records=reused, synthetic_qa_enabled=generate_qa,
                  status='partial', papers=[dict(canonical_id=p['canonical_id'], full_text_status=p['full_text']['extraction_status'],
                      sections=len(p['full_text']['sections']), objects=len(p['figures_and_tables']),
                      references=len(p['references']), qas=len(p['qas'])) for p in papers])
    atomic_json(out/'arxiv_enrichment_audit.json', report)
    return {key: value for key,value in report.items() if key not in ('papers','legacy_before','legacy_after')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--import-db', action='store_true')
    parser.add_argument('--generate-qa', action='store_true', help='Requires configured provider adapter; disabled by default')
    parser.add_argument('--qa-provider', choices=['grounded-json-http'])
    parser.add_argument('--qa-model')
    parser.add_argument('--qa-endpoint')
    args = parser.parse_args()
    if args.limit is not None and args.limit<1:
        parser.error('limit must be positive')
    try:
        result = run(limit=args.limit, import_db=args.import_db, generate_qa=args.generate_qa,
                     qa_provider=args.qa_provider, qa_model=args.qa_model, qa_endpoint=args.qa_endpoint)
        print(json.dumps(result, indent=2))
        if result['postgres']['failed'] or result['exported_papers']!=result['selected_arxiv_papers']:
            raise SystemExit(1)
    except Exception as error:
        print('Enrichment failed: '+type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None


if __name__=='__main__':
    main()
