"""Read-only dataset adapters shared by the CLI, UI and audit.

Master 2.0/2.1 and normalized corpora retain their source schemas. Raw dataset
adapters are explicit so benchmark labels never become retrieval prose.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'datasets/master/arxiv_master_v2.jsonl'
SCIENCEDIRECT_DEFAULT = ROOT / 'datasets/sciencedirect/master/sciencedirect_master.jsonl'
SCIENCEDIRECT_ENRICHED_DEFAULT = ROOT / 'datasets/sciencedirect/enriched/sciencedirect_enriched_v2.jsonl'
PURPOSES = {
    'arxiv': 'Scientific paper content; QA is optional',
    'qasper': 'Human scientific QA benchmark; preserve annotation splits',
    'scifact': 'Abstract corpus / claim verification benchmark, not full papers',
    'unarxive': 'Structured scientific text and citation contexts',
    'sciencedirect': 'ScienceDirect LLM Master Dataset; 100% verified DOIs with provenance and license governance',
    'sciencedirect_enriched': 'ScienceDirect Enriched Dataset v2; structured sections, paragraphs, citations, figures and tables',
}


def load_file(path):
    path = Path(path)
    if path.suffix == '.parquet':
        import pyarrow.parquet as pq
        from app.ingestion.normalize import normalize_qasper
        split = path.parent.name
        result = []
        for raw in pq.read_table(path).to_pylist():
            paper = normalize_qasper(raw, split)
            paper['raw_annotations'] = raw.get('qas')
            paper['figures_and_tables'] = raw.get('figures_and_tables') or []
            result.append(paper)
        return result
    text = path.read_text(encoding='utf-8-sig')
    if path.suffix == '.jsonl':
        try:
            records = [json.loads(line) for line in text.split('\n') if line.strip()]
        except json.JSONDecodeError:
            records = [json.loads(text)]  # unarXive preview uses pretty JSON with this suffix
    else:
        value = json.loads(text)
        records = value if isinstance(value, list) else [value]
    output = []
    for record in records:
        if not record.get('canonical_id'):
            from app.ingestion.normalize import normalize_scifact, normalize_unarxive, normalize_qasper
            if 'doc_id' in record:
                record = dict(normalize_scifact(record), raw_record=record)
            elif 'paper_id' in record and 'metadata' in record:
                record = dict(normalize_unarxive(record), raw_record=record)
            elif 'id' in record and 'qas' in record:
                record = dict(normalize_qasper(record, 'unknown'), raw_annotations=record['qas'])
            else:
                raise ValueError('Unsupported paper schema: choose a paper corpus, not claims or manifests')
        output.append(record)
    return output


def load_postgres(schema='public'):
    if schema == 'sciencedirect':
        from app.storage.sciencedirect_postgres import connect
        with connect() as db:
            db.execute('SET TRANSACTION READ ONLY')
            db.execute("SET LOCAL statement_timeout='30s'")
            rows = db.execute('SELECT master_json FROM sciencedirect.papers ORDER BY canonical_id').fetchall()
            return [r[0] for r in rows]

    from app.storage.postgres import connect
    with connect() as db:
        db.execute('SET TRANSACTION READ ONLY')
        db.execute("SET LOCAL statement_timeout='30s'")
        rows = db.execute('SELECT canonical_id,title,abstract,arxiv_id,doi,publication_year,metadata_json FROM papers ORDER BY canonical_id').fetchall()
        sources = {}
        for cid, source, sid, split in db.execute('SELECT canonical_id,source,source_id,dataset_split FROM paper_sources'):
            sources.setdefault(cid, []).append(dict(source=source, source_id=sid, split=split))
        sections = {}
        for cid, source, idx, heading, body in db.execute('SELECT canonical_id,source,section_idx,heading,body FROM sections ORDER BY canonical_id,source,section_idx'):
            sections.setdefault(cid, []).append(dict(heading=heading, text=body, source=source))
        enriched = {}
        if db.execute("SELECT to_regclass('paper_enrichments')").fetchone()[0]:
            enriched = dict(db.execute('SELECT canonical_id,document FROM paper_enrichments'))
        result = []
        for cid, title, abstract, aid, doi, year, metadata in rows:
            paper = enriched.get(cid) or dict(canonical_id=cid, title=title, abstract=abstract,
                arxiv_id=aid, doi=doi, year=year, metadata=metadata, sections=sections.get(cid, []),
                source=sources.get(cid, [{}])[0].get('source', 'unknown'))
            result.append(dict(paper, database_sources=sources.get(cid, [])))
        return result


def sections(paper):
    full = paper.get('full_text')
    return full.get('sections', []) if isinstance(full, dict) else full or paper.get('sections', [])


def paragraphs(section):
    values = section.get('paragraphs')
    if values is None:
        values = [t for t in section.get('text', '').split('\n\n') if t.strip()]
    return [p if isinstance(p, dict) else dict(text=p, page_start=section.get('page_start'),
            page_end=section.get('page_end')) for p in values]


def search(papers, query='', year=None, source='', topic='', full_text_status='', doi_status=''):
    result = []
    for p in papers:
        haystack = json.dumps([
            p.get('title'), p.get('canonical_id'), p.get('identifiers'),
            p.get('arxiv_id'), p.get('doi'), (p.get('publication') or {}).get('journal')
        ], ensure_ascii=False).lower()
        sources = {p.get('source')} | {s['source'] for s in p.get('database_sources', [])}
        topics = json.dumps([
            p.get('categories'), p.get('metadata'), p.get('topic_group'),
            p.get('provenance', {}).get('topic_group')
        ]).lower()
        ft_stat = (p.get('quality') or {}).get('full_text_status', '')
        doi_val_stat = (p.get('doi_validation') or {}).get('status', '')

        if (
            query.lower() in haystack
            and (year is None or p.get('year') == year)
            and (not source or source in sources)
            and (not topic or topic.lower() in topics)
            and (not full_text_status or ft_stat == full_text_status)
            and (not doi_status or doi_val_stat == doi_status)
        ):
            result.append(p)
    return result


def local_path(value):
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else ROOT / path

