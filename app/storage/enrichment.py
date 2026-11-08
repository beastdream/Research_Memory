"""Additive, transactional storage for enriched masters; existing tables stay intact."""
import copy
from psycopg.types.json import Jsonb
from app.ingestion.enrichment import digest, validate_enriched

MIGRATION = (
    """CREATE TABLE IF NOT EXISTS paper_enrichments (
        canonical_id TEXT PRIMARY KEY REFERENCES papers(canonical_id),
        document JSONB NOT NULL,
        content_sha256 TEXT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS paper_enrichment_history (
        canonical_id TEXT NOT NULL REFERENCES papers(canonical_id),
        content_sha256 TEXT NOT NULL,
        document JSONB NOT NULL,
        archived_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY(canonical_id, content_sha256)
    )""",
    """CREATE OR REPLACE VIEW enriched_sections AS
        SELECT canonical_id, s->>'section_id' AS section_id, s->>'heading' AS heading,
               s->'paragraphs' AS paragraphs
        FROM paper_enrichments CROSS JOIN LATERAL
        jsonb_array_elements(document->'full_text'->'sections') s""",
    """CREATE OR REPLACE VIEW enriched_references AS
        SELECT canonical_id, r->>'reference_id' AS reference_id,
               r->>'target_paper_id' AS target_paper_id, r AS reference_json
        FROM paper_enrichments CROSS JOIN LATERAL jsonb_array_elements(document->'references') r""",
    """CREATE OR REPLACE VIEW enriched_objects AS
        SELECT canonical_id, o->>'item_id' AS item_id, o->>'type' AS object_type, o AS object_json
        FROM paper_enrichments CROSS JOIN LATERAL jsonb_array_elements(document->'figures_and_tables') o""",
    """CREATE OR REPLACE VIEW enriched_qas AS
        SELECT canonical_id, q->>'qa_id' AS qa_id, q->>'split' AS dataset_split, q AS qa_json
        FROM paper_enrichments CROSS JOIN LATERAL jsonb_array_elements(document->'qas') q""",
)


def migrate(db):
    for statement in MIGRATION:
        db.execute(statement)


def merge_document(old, new):
    """Replace changed extraction as a unit, retain annotations and richer prior evidence.

    Never union section arrays: stale paragraph identities would break citations.
    Incoming references/objects carry stable IDs and are merged by identity.
    """
    result = copy.deepcopy(new)
    from app.storage.postgres import preserve_merge
    for key in ('title', 'abstract', 'year', 'authors', 'categories', 'metadata'):
        if key in old:
            result[key] = preserve_merge(result.get(key), old[key])
    rank = {'failed': 0, 'pending': 1, 'partial': 2, 'complete': 3, 'not_applicable': 3}
    old_status = old['full_text']['extraction_status']
    new_status = new['full_text']['extraction_status']
    # Equal-status, same-parser new extraction can correct content. A shorter
    # fallback from another parser cannot silently replace a richer extraction.
    downgrade = rank[old_status] > rank[new_status]
    if downgrade or (old['full_text']['sections'] and not new['full_text']['sections']):
        for key in ('full_text', 'sections', 'quality', 'provenance', 'extraction_quality', 'has_full_text'):
            result[key] = copy.deepcopy(old[key])
    for key, identity in [('qas', 'qa_id'), ('references', 'reference_id'), ('figures_and_tables', 'item_id')]:
        def identity_key(value):
            if key=='references':
                return digest(value['raw_reference'])
            if key=='figures_and_tables':
                return digest([value['type'],value.get('page'),value.get('label'),value.get('caption')])
            return value[identity]
        old_values = {identity_key(v): copy.deepcopy(v) for v in old.get(key, [])}
        # Parser revisions change extraction IDs. Deduplicate references/objects by
        # their source evidence instead of unioning all historical extraction IDs.
        merged = dict(old_values) if key=='qas' or not result.get(key) else {
            k:v for k,v in old_values.items() if v.get('extraction_status')=='complete' or v.get('resolution_status')=='resolved'}
        for value in result.get(key, []):
            item_key = identity_key(value)
            previous = old_values.get(item_key)
            if previous and (previous.get('validation_status')=='human_verified' or
                             previous.get('extraction_status')=='complete' and value.get('extraction_status')!='complete'):
                merged[item_key] = previous
                continue
            if previous:
                value = {k: (previous.get(k) if v is None or v=='' or v==[] else v) for k,v in value.items()}
                for field in previous.keys()-value.keys():
                    value[field] = copy.deepcopy(previous[field])
                if key=='references' and previous.get('resolution_status')=='resolved' and value.get('resolution_status')!='resolved':
                    for field in ('resolution_status','target_paper_id'):
                        value[field] = previous[field]
            merged[item_key] = value
        result[key] = list(merged.values())
    # Human evidence keeps its source paragraphs available when extraction changes.
    old_sections = {s['section_id']: s for s in old['full_text']['sections']}
    new_sections = {s['section_id']: s for s in result['full_text']['sections']}
    for qa in result['qas']:
        for evidence in qa['evidence_refs']:
            sid = evidence['section_id']
            if sid not in new_sections and sid in old_sections:
                retained = copy.deepcopy(old_sections[sid])
                retained['kind'] = 'retained_annotation_evidence'
                result['full_text']['sections'].append(retained)
                new_sections[sid] = retained
            elif sid in old_sections and sid in new_sections:
                current_ids = {p['paragraph_id'] for p in new_sections[sid]['paragraphs']}
                for paragraph in old_sections[sid]['paragraphs']:
                    if paragraph['paragraph_id']==evidence['paragraph_id'] and paragraph['paragraph_id'] not in current_ids:
                        new_sections[sid]['paragraphs'].append(copy.deepcopy(paragraph))
    # Resolved reference fragments can survive a parser revision even when their
    # new segmentation differs. Preserve the exact cited historical paragraphs,
    # rather than emitting dangling context IDs or dropping the source evidence.
    for reference in result['references']:
        for context in reference.get('citation_contexts', []):
            sid, pid = context['section_id'], context['paragraph_id']
            previous = old_sections.get(sid)
            if not previous:
                continue
            if sid not in new_sections:
                retained = copy.deepcopy(previous)
                retained['paragraphs'] = [p for p in retained['paragraphs'] if p['paragraph_id']==pid]
                retained['kind'] = 'retained_annotation_evidence'
                result['full_text']['sections'].append(retained)
                new_sections[sid] = retained
            elif not any(p['paragraph_id']==pid for p in new_sections[sid]['paragraphs']):
                new_sections[sid]['paragraphs'].extend(copy.deepcopy(p) for p in previous['paragraphs'] if p['paragraph_id']==pid)
    for obj in result['figures_and_tables']:
        sid = obj.get('section_id')
        if sid and sid not in new_sections:
            obj['previous_section_id'] = sid
            candidates = [s['section_id'] for s in result['full_text']['sections']
                          if obj.get('page') and s.get('page_start') and s.get('page_end') and
                          s['page_start'] <= obj['page'] <= s['page_end'] and s.get('kind')!='retained_annotation_evidence']
            obj['section_id'] = candidates[0] if len(candidates)==1 else None
    result['sections'] = [dict(heading=s['heading'], text='\n\n'.join(p['text'] for p in s['paragraphs']))
                          for s in result['full_text']['sections']]
    # Missing metadata must not erase known publication facts or annotations.
    for key in ('publication', 'identifiers', 'research_metadata'):
        result[key] = preserve_merge(old.get(key, {}), result.get(key, {}))
    if result['publication'].get('journal_publication_year'):
        result['year'] = result['publication']['journal_publication_year']
        result['provenance']['year_basis'] = 'journal_publication_year'
    return result


def upsert_enrichment(db, paper):
    validate_enriched(paper)
    cid = paper['canonical_id']
    db.execute('SELECT pg_advisory_xact_lock(728391024)')
    # This workflow enriches existing canonical papers; it never imports candidates.
    mapping = db.execute("SELECT 1 FROM paper_sources WHERE canonical_id=%s AND source='arxiv' AND source_id=%s",
                         (cid, paper['source_id'])).fetchone()
    if not mapping:
        raise ValueError('Paper is outside imported arXiv selection')
    old = db.execute('SELECT document,content_sha256 FROM paper_enrichments WHERE canonical_id=%s FOR UPDATE', (cid,)).fetchone()
    document = merge_document(old[0], paper) if old else paper
    validate_enriched(document)
    checksum = digest(document)
    if old and checksum == old[1]:
        return 'unchanged'
    if old:
        db.execute('''INSERT INTO paper_enrichment_history(canonical_id,content_sha256,document)
                      VALUES(%s,%s,%s) ON CONFLICT DO NOTHING''', (cid, old[1], Jsonb(old[0])))
    db.execute('''INSERT INTO paper_enrichments(canonical_id,document,content_sha256) VALUES(%s,%s,%s)
                  ON CONFLICT(canonical_id) DO UPDATE SET document=EXCLUDED.document,
                  content_sha256=EXCLUDED.content_sha256,updated_at=NOW()''', (cid, Jsonb(document), checksum))
    return 'updated' if old else 'inserted'
