"""Reviewable arXiv discovery, selected-only download, normalization and audit."""
from __future__ import annotations
import csv
import json
from collections import Counter
from pathlib import Path
from jsonschema import ValidationError

from app.ingestion.arxiv_client import ArxivClient, arxiv_identity, sha256, valid_pdf
from app.ingestion.arxiv_index import ExistingIndex
from app.ingestion.arxiv_parser import normalize_arxiv, PARSER_REVISION, metadata_digest, refresh_quality_flags
from app.ingestion.master_schema import validate_master

TOPICS = {
    'memory': (40, ['long term memory', 'adaptive memory', 'memory consolidation', 'conversational memory', 'forgetting', 'memory language agents', 'memory bank', 'memory networks', 'memory management', 'memory framework']),
    'retrieval': (35, ['retrieval augmented generation', 'dense retrieval', 'dense passage retrieval', 'reranking', 'retrieval language models']),
    'agents': (30, ['language agents', 'llm agents', 'long horizon agents', 'research agents', 'autonomous agents']),
    'graph': (25, ['graphrag', 'scientific knowledge graph', 'knowledge graphs', 'citation reasoning']),
    'evaluation': (20, ['scientific question answering', 'scientific qa', 'rag evaluation', 'agent evaluation', 'question answering benchmark']),
}
# Foundational papers are fetched from the API by ID, never synthesized metadata.
FOUNDATIONAL_IDS = ['1706.03762', '1410.3916', '1503.08895', '1605.06065', '1910.10683',
                     '2005.11401', '2004.04906', '1904.08375', '2002.08909', '2007.01282',
                     '2007.00808', '2104.07567', '2004.14974', '2210.03629', '2303.11366',
                     '2304.03442', '2303.17580', '2308.11432', '2309.02427', '2310.08560',
                     '2305.10250', '2304.13343', '2402.17753', '2407.01489', '2404.16130',
                     '2401.18059', '2404.10980', '2405.01535', '2402.14034', '2406.14562',
                     '1911.02782', '2105.03011', '2004.14974', '1806.03822', '1810.04805']
FIELDS = ['arxiv_id', 'title', 'year', 'categories', 'topic_group', 'relevance_score', 'pdf_url',
          'already_exists', 'selected', 'download_status', 'arxiv_version', 'pdf_path', 'pdf_sha256', 'reason']


def truth(value):
    return str(value).strip().lower() in ('true', '1', 'yes')


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def paths(root):
    root = Path(root)
    return root / 'datasets/manifests/arxiv_candidates.csv', root / 'datasets/raw/arxiv/metadata.json'


def read_manifest(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def write_manifest(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.csv.tmp')
    with tmp.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def quotas(target):
    if not 100 <= target <= 200:
        raise ValueError('target must be between 100 and 200')
    result = {key: weight * target // 150 for key, (weight, _) in TOPICS.items()}
    for key in sorted(TOPICS, key=lambda k: -(TOPICS[k][0] * target % 150))[:target - sum(result.values())]:
        result[key] += 1
    return result


def relevance(row):
    title = row['title'].lower().replace('-', ' ')
    abstract = row['abstract'].lower().replace('-', ' ')
    text = title + ' ' + abstract
    context = any(term in text for term in ('language model', 'llm', 'retrieval', 'knowledge graph',
                  'question answering', 'scientific', 'memory network', 'neural turing',
                  'differentiable neural computer', 'cognitive architecture', 'conversational', 'dialogue'))
    if not context:
        return 'memory', 0
    scores = {}
    for topic, (_, terms) in TOPICS.items():
        scores[topic] = sum(6 * (term in title) + 2 * (term in abstract) for term in terms)
    topic = max(scores, key=scores.get)
    bonus = 1 if any(c in ('cs.AI', 'cs.CL', 'cs.IR', 'cs.LG') for c in row.get('categories', [])) else 0
    # Generic image/translation reranking and mobile-agent control do not meet this corpus scope.
    if topic == 'agents' and not any(t in text for t in ('language model', 'llm', 'language agent', 'research agent', 'cognitive architecture')):
        return topic, 0
    return topic, scores[topic] + bonus


def discover(root, target=150, dry_run=False, resume=False, limit=None, client=None, database=True):
    allocation = quotas(target)
    csvpath, metapath = paths(root)
    if dry_run:
        return {'target': target, 'quotas': allocation, 'candidate_goal': 300,
                'action': 'official Atom queries only; no PDF download; review selected column before download'}
    if csvpath.exists():
        if resume and metapath.exists():
            return {'candidates': len(read_manifest(csvpath)), 'resumed': True}
        raise ValueError('Manifest already exists; use --resume to preserve manual selections')
    index = ExistingIndex.load(root, database=database)
    client = client or ArxivClient()
    candidates = {}
    for row in client.query(id_list=FOUNDATIONAL_IDS, max_results=len(FOUNDATIONAL_IDS)):
        candidates[row['arxiv_id']] = row
    for row in client.query('all:memory AND (all:"language model" OR all:LLM OR all:"language agents")', max_results=75):
        candidates[row['arxiv_id']] = row
    # Relevance and oldest-first searches provide historical coverage without a recent-only filter.
    for order in ('relevance', 'submittedDate'):
        for topic, (_, terms) in TOPICS.items():
            query = '(' + ' OR '.join(f'all:"{term}"' for term in terms) + ') AND (cat:cs.AI OR cat:cs.CL OR cat:cs.IR OR cat:cs.LG)'
            for row in client.query(query, start=0, max_results=35, sortBy=order,
                                    sortOrder='ascending' if order == 'submittedDate' else 'descending'):
                key = row['arxiv_id']
                if key not in candidates or int((row.get('arxiv_version') or 'v0')[1:]) > int((candidates[key].get('arxiv_version') or 'v0')[1:]):
                    candidates[key] = row
    # Dedup may reduce 350 API entries; fill via paginated relevance searches, up to 350 unique.
    for start in (35, 70, 105):
        if len(candidates) >= 300:
            break
        for topic, (_, terms) in TOPICS.items():
            query = '(' + ' OR '.join(f'all:"{t}"' for t in terms) + ') AND (cat:cs.AI OR cat:cs.CL OR cat:cs.IR OR cat:cs.LG)'
            for row in client.query(query, start=start, max_results=25):
                candidates.setdefault(row['arxiv_id'], row)
                if len(candidates) >= 350:
                    break
            if len(candidates) >= 350:
                break
    rows = []
    ranked = sorted(candidates.values(), key=lambda r: relevance(r)[1] + (10 if r['arxiv_id'] in FOUNDATIONAL_IDS else 0), reverse=True)[:350]
    candidates = {r['arxiv_id']: r for r in ranked}
    for meta in ranked:
        topic, score = relevance(meta)
        meta['topic_group'] = topic
        match = index.match(meta)
        reason = 'evaluation_excluded' if meta['arxiv_id'] in index.excluded else ('identity_conflict' if match and match.get('conflict') else '')
        row = dict(arxiv_id=meta['arxiv_id'], title=meta['title'], year=(meta.get('published') or '')[:4],
                   categories=';'.join(meta['categories']), topic_group=topic, relevance_score=score + (10 if meta['arxiv_id'] in FOUNDATIONAL_IDS and score else 0),
                   pdf_url=meta.get('pdf_url') or '', already_exists=bool(match), selected=False,
                   download_status='skipped_duplicate' if match and match.get('has_full_text') else 'pending',
                   arxiv_version=meta.get('arxiv_version') or '', pdf_path='', pdf_sha256='', reason=reason)
        rows.append(row)
    # Greedy topic quota with a penalty for repeatedly selecting the same publication year.
    years = Counter()
    for topic, count in allocation.items():
        eligible = [r for r in rows if r['topic_group'] == topic and not r['reason'] and r['download_status'] == 'pending' and r['pdf_url'] and r['relevance_score'] > 1]
        for _ in range(min(count, len(eligible))):
            best = max(eligible, key=lambda r: float(r['relevance_score']) / (1 + .15 * years[r['year']]))
            best['selected'] = True
            years[best['year']] += 1
            eligible.remove(best)
    rows.sort(key=lambda r: (not r['selected'], -float(r['relevance_score']), r['arxiv_id']))
    # --limit intentionally only limits selection, discovery retains reserve metadata.
    if limit is not None:
        for row in [r for r in rows if r['selected']][limit:]:
            row['selected'] = False
    atomic_json(metapath, candidates)
    write_manifest(csvpath, rows)
    return {'candidates': len(rows), 'selected': sum(r['selected'] for r in rows), 'database': index.db_status,
            'topic_quotas': allocation, 'manifest': str(csvpath)}


def download(root, dry_run=False, resume=False, limit=None, max_pdf_mb=40, max_total_download_mb=2000,
             reviewed=False, client=None, database=True):
    csvpath, metapath = paths(root)
    rows = read_manifest(csvpath)
    chosen = [r for r in rows if truth(r['selected'])]
    if limit is not None:
        chosen = chosen[:limit]
    if dry_run:
        result = {'selected_for_run': len(chosen), 'ids': [r['arxiv_id'] for r in chosen],
                  'manifest_sha256': sha256(csvpath)}
        atomic_json(csvpath.with_name('arxiv_download_dry_run.json'), result)
        return result
    receipt = csvpath.with_name('arxiv_download_dry_run.json')
    if not reviewed or not receipt.exists() or json.loads(receipt.read_text())['manifest_sha256'] != sha256(csvpath):
        raise ValueError('Review candidate CSV, run download --dry-run, then pass --reviewed. Re-run dry-run after edits.')
    approved = json.loads(receipt.read_text())
    if not set(r['arxiv_id'] for r in chosen).issubset(approved['ids']):
        raise ValueError('Download scope exceeds dry-run; repeat dry-run with the same limit')
    metadata = json.loads(metapath.read_text(encoding='utf-8'))
    index = ExistingIndex.load(root, database=database)
    # Offline identities remain usable when PostgreSQL is unavailable. Database imports
    # always re-resolve identities transactionally; no DB record is created here.
    budget = [int(max_total_download_mb * 1024 ** 2)]
    client = client or ArxivClient()
    for row in chosen:
        if row['download_status'] == 'downloaded' and row.get('pdf_path'):
            cached_path = Path(row['pdf_path'])
            if valid_pdf(cached_path) and sha256(cached_path) == row['pdf_sha256']:
                continue
        meta = metadata[row['arxiv_id']]
        match = index.match(meta)
        if row['arxiv_id'] in index.excluded or (match and match.get('conflict')):
            row['download_status'], row['reason'] = 'skipped_duplicate', 'evaluation_excluded_or_identity_conflict'
        elif match and match.get('has_full_text'):
            row['download_status'], row['reason'] = 'skipped_duplicate', 'existing_full_text'
        else:
            try:
                base, version = arxiv_identity(meta['arxiv_id'] + (meta.get('arxiv_version') or ''))
                if not version:
                    raise ValueError('PDF version missing from API metadata')
                dest = Path(root) / 'storage/pdfs/arxiv' / (base.replace('/', '__') + version + '.pdf')
                # Reuse verified local PDF only when the filename identifies this version.
                local = index.pdfs.get(base)
                if local and local.stem.replace('__', '/') == base + version and valid_pdf(local):
                    dest = local
                    checksum = sha256(dest)
                else:
                    checksum = client.download(meta['pdf_url'], dest, int(max_pdf_mb * 1024 ** 2), budget)
                duplicate = index.hashes.get(checksum)
                row.update(download_status='downloaded', pdf_path=str(dest), pdf_sha256=checksum, reason='')
                if duplicate and duplicate.get('has_full_text'):
                    row.update(download_status='skipped_duplicate', reason='sha256_existing_full_text')
                index.hashes[checksum] = {'canonical_id': 'arxiv:' + base, 'has_full_text': True}
            except (ValueError, RuntimeError, OSError) as exc:
                row.update(download_status='failed', reason=type(exc).__name__ + ': ' + str(exc))
        write_manifest(csvpath, rows)  # Persist after every paper, even on failure.
        approved['manifest_sha256'] = sha256(csvpath)
        atomic_json(receipt, approved)
    return dict(Counter(r['download_status'] for r in rows))


def normalize(root, dry_run=False, resume=False, limit=None):
    csvpath, metapath = paths(root)
    metadata = json.loads(metapath.read_text(encoding='utf-8'))
    rows = [r for r in read_manifest(csvpath) if truth(r['selected']) and r['download_status'] == 'downloaded']
    if limit is not None:
        rows = rows[:limit]
    if dry_run:
        return {'pdfs_to_extract': len(rows)}
    dest = Path(root) / 'datasets/processed/arxiv_master.jsonl'
    existing = {}
    if dest.exists():
        if not resume:
            raise ValueError('Master output exists; use --resume to preserve records')
        for line in dest.read_text(encoding='utf-8').split('\n'):
            if not line.strip():
                continue
            record = validate_master(refresh_quality_flags(json.loads(line)))
            if record['canonical_id'] in existing:
                raise ValueError('Duplicate canonical identity in existing Master output; review before normalization')
            existing[record['canonical_id']] = record
    failures = []
    for row in rows:
        try:
            if sha256(row['pdf_path']) != row['pdf_sha256']:
                raise ValueError('PDF checksum changed')
            cached = existing.get('arxiv:' + row['arxiv_id'])
            if (cached and cached['has_full_text']
                    and cached['provenance'].get('pdf_sha256') == row['pdf_sha256']
                    and cached['provenance'].get('metadata_sha256') == metadata_digest(metadata[row['arxiv_id']])
                    and cached['extraction_quality'].get('parser_revision') == PARSER_REVISION):
                continue
            record = validate_master(normalize_arxiv(metadata[row['arxiv_id']], row['pdf_path']))
            old = existing.get(record['canonical_id'])
            if not old or (not old['has_full_text'] or record['has_full_text']):
                existing[record['canonical_id']] = record
        except (ValueError, RuntimeError, OSError, ValidationError) as exc:
            failures.append({'arxiv_id': row['arxiv_id'], 'reason': type(exc).__name__})
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.jsonl.tmp')
    with tmp.open('w', encoding='utf-8') as stream:
        for record in existing.values():
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
    tmp.replace(dest)
    atomic_json(Path(root) / 'datasets/manifests/arxiv_normalize_failures.json', failures)
    return {'valid_records': len(existing), 'has_full_text': sum(r['has_full_text'] for r in existing.values()), 'failures': failures}


def audit(root, target=150, database=True):
    quotas(target)
    csvpath, _ = paths(root)
    rows = read_manifest(csvpath) if csvpath.exists() else []
    dest = Path(root) / 'datasets/processed/arxiv_master.jsonl'
    records, invalid, seen = [], [], set()
    if dest.exists():
        for number, line in enumerate(dest.read_text(encoding='utf-8').split('\n'), 1):
            if not line.strip():
                continue
            try:
                record = validate_master(json.loads(line))
                if record['canonical_id'] in seen:
                    raise ValueError('Duplicate canonical identity')
                seen.add(record['canonical_id'])
                records.append(record)
            except (ValueError, KeyError, TypeError) as exc:
                invalid.append({'line': number, 'reason': type(exc).__name__})
            except Exception as exc:
                invalid.append({'line': number, 'reason': type(exc).__name__})
    full = sum(r['has_full_text'] for r in records)
    result = dict(target=target, metadata_candidates=len(rows), selected=sum(truth(r['selected']) for r in rows),
                  existing_duplicates=sum(truth(r['already_exists']) for r in rows),
                  pdf_downloaded=sum(r['download_status'] == 'downloaded' for r in rows),
                  pdf_failed=sum(r['download_status'] == 'failed' for r in rows),
                  full_text_success=full, abstract_only=sum(bool(r['abstract']) and not r['full_text'] for r in records),
                  insufficient_text=sum(not r['has_full_text'] for r in records),
                  with_figures=sum(any(i['type'] == 'figure' for i in r['figures_and_tables']) for r in records),
                  with_tables=sum(any(i['type'] == 'table' for i in r['figures_and_tables']) for r in records),
                  with_references=sum(bool(r['references']) for r in records), with_qas=sum(bool(r['qas']) for r in records),
                  by_year=dict(Counter(str(r['year']) for r in records)),
                  by_topic=dict(Counter(r['provenance'].get('topic_group') or 'unknown' for r in records)),
                  shortfall=max(0, target-full), invalid_records=invalid, imported_postgres=None,
                  reserve_candidates=[r['arxiv_id'] for r in rows if not truth(r['selected']) and r['download_status'] == 'pending' and not r['reason']],
                  failure_reasons=dict(Counter(r['reason'] for r in rows if r['reason'])),
                  shortfall_reason='Only validated extracted full text counts; inspect download failures and topic coverage.' if full < target else None)
    result['topic_quotas'] = quotas(target)
    selected_topics = Counter(r['topic_group'] for r in rows if truth(r['selected']))
    result['selected_by_topic'] = dict(selected_topics)
    result['selection_shortfall_by_topic'] = {k: max(0, n-selected_topics[k]) for k, n in quotas(target).items()}
    result['pdf_integrity_failures'] = []
    for row in rows:
        if row['download_status'] == 'downloaded':
            path = Path(row['pdf_path'])
            if not valid_pdf(path) or sha256(path) != row['pdf_sha256']:
                result['pdf_integrity_failures'].append(row['arxiv_id'])
    if database:
        from app.storage.postgres import connect
        try:
            with connect() as db:
                db.execute('SET TRANSACTION READ ONLY')
                result['imported_postgres'] = db.execute("SELECT count(DISTINCT canonical_id) FROM paper_sources WHERE source='arxiv'").fetchone()[0]
        except Exception as exc:
            result['database_status'] = 'unavailable:' + type(exc).__name__
    atomic_json(Path(root) / 'datasets/manifests/arxiv_audit.json', result)
    return result
