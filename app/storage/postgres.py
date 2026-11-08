"""PostgreSQL persistence for ResearchMemory Week 1, using psycopg 3.

PostgreSQL is the sole persistence backend for this project.
Requires DATABASE_URL in the project root .env or process environment.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import Connection
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[2]


# Separate statements avoid multi-statement prepared-query issues.
SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS papers (
        canonical_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        abstract TEXT NOT NULL DEFAULT '',
        doi TEXT NOT NULL DEFAULT '',
        arxiv_id TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        has_full_text BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS paper_sources (
        canonical_id TEXT NOT NULL REFERENCES papers(canonical_id) ON DELETE CASCADE,
        source TEXT NOT NULL,
        source_id TEXT NOT NULL,
        dataset_split TEXT NOT NULL,
        PRIMARY KEY (canonical_id, source, source_id, dataset_split)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sections (
        canonical_id TEXT NOT NULL REFERENCES papers(canonical_id) ON DELETE CASCADE,
        source TEXT NOT NULL,
        section_idx INTEGER NOT NULL,
        heading TEXT NOT NULL,
        body TEXT NOT NULL,
        PRIMARY KEY (canonical_id, source, section_idx)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        title TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        message_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_events (
        event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        memory_id TEXT,
        session_id TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
        operation TEXT NOT NULL,
        payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS papers_title_idx ON papers (title)",
    "CREATE INDEX IF NOT EXISTS paper_sources_source_idx ON paper_sources (source)",
    "CREATE INDEX IF NOT EXISTS messages_session_idx ON messages (session_id, created_at)",
    "CREATE INDEX IF NOT EXISTS memory_events_memory_idx ON memory_events (memory_id, created_at)",
)

# Additive v2 migration: source snapshots retain structures/provenance independently.
SCHEMA_STATEMENTS += (
    "ALTER TABLE papers ADD COLUMN IF NOT EXISTS publication JSONB NOT NULL DEFAULT '{}'::jsonb",
    "ALTER TABLE papers ADD COLUMN IF NOT EXISTS publication_year INTEGER",
    "ALTER TABLE papers ADD COLUMN IF NOT EXISTS identifiers JSONB NOT NULL DEFAULT '{}'::jsonb",
    """CREATE TABLE IF NOT EXISTS paper_master_sources (
        canonical_id TEXT NOT NULL REFERENCES papers(canonical_id),
        source TEXT NOT NULL, source_id TEXT NOT NULL,
        master_json JSONB NOT NULL,
        PRIMARY KEY (canonical_id, source, source_id)
    )""",
    """CREATE TABLE IF NOT EXISTS paper_master_versions (
        canonical_id TEXT NOT NULL REFERENCES papers(canonical_id),
        source TEXT NOT NULL, source_id TEXT NOT NULL,
        pdf_sha256 TEXT NOT NULL, master_json JSONB NOT NULL,
        PRIMARY KEY (canonical_id, source, source_id, pdf_sha256)
    )""",
    "CREATE INDEX IF NOT EXISTS papers_arxiv_id_idx ON papers(arxiv_id)",
    "CREATE INDEX IF NOT EXISTS papers_doi_idx ON papers(doi)",
)


def preserve_merge(old, new):
    """Retain populated old scalar metadata and union arrays without duplicates."""
    if isinstance(old, dict) and isinstance(new, dict):
        return {key: preserve_merge(old.get(key), new.get(key)) for key in old.keys() | new.keys()}
    if isinstance(old, list) and isinstance(new, list):
        result = list(old)
        for value in new:
            if value not in result:
                result.append(value)
        return result
    return old if old is not None and old != '' and old != [] and old != {} else new


def connect() -> Connection:
    # Explicit project .env, not cwd: safe when running scripts from VS Code.
    load_dotenv(ROOT / ".env", override=False)
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "Missing DATABASE_URL. Create D:\\ResearchMemory\\.env "
            "from .env.example and set your local PostgreSQL password."
        )
    if not url.startswith(("postgresql://", "postgres://")):
        raise ValueError("DATABASE_URL must begin with postgresql:// or postgres://")
    return psycopg.connect(url, connect_timeout=10)


def init_schema(db: Connection) -> None:
    for statement in SCHEMA_STATEMENTS:
        db.execute(statement)
    from app.storage.enrichment import migrate
    migrate(db)


def upsert_paper(db: Connection, paper: dict) -> None:
    if paper.get('source') == 'longmemeval' or (paper.get('source') == 'qasper' and paper.get('split') != 'train'):
        raise ValueError('Evaluation data cannot enter retrieval storage')
    if paper.get('schema_version') == '2.1':
        from app.storage.enrichment import upsert_enrichment
        return upsert_enrichment(db, paper)
    if paper.get('schema_version') == '2.0':
        from app.ingestion.master_schema import validate_master
        validate_master(paper)
    paper = dict(paper)
    from app.ingestion.arxiv_index import identities
    arxiv, doi = identities(paper)
    paper['arxiv_id'], paper['doi'] = arxiv, doi
    # Serialize identity resolution/imports so concurrent imports cannot create cross-ID duplicates.
    db.execute("SELECT pg_advisory_xact_lock(728391024)")
    checksum = ((paper.get('provenance') or {}).get('pdf_sha256')
                or (paper.get('metadata') or {}).get('pdf_sha256')
                or (paper.get('source_id') if paper.get('source') == 'uploaded_pdf' else None))
    matches = db.execute(
        """SELECT DISTINCT p.canonical_id FROM papers p
           LEFT JOIN paper_sources s ON s.canonical_id=p.canonical_id
           WHERE p.canonical_id=%s OR (%s<>'' AND p.arxiv_id=%s)
              OR (%s<>'' AND lower(p.doi)=%s)
              OR (%s<>'' AND s.source='uploaded_pdf' AND s.source_id=%s)""",
        (paper['canonical_id'], arxiv, arxiv, doi, doi, checksum or '', checksum or '')
    ).fetchall()
    if len(matches) > 1:
        raise ValueError('Multiple legacy identities match; manual review required; no rows merged')
    if matches:
        paper['canonical_id'] = matches[0][0]
    canonical_id = paper["canonical_id"]
    sections = paper.get('full_text') or paper.get("sections") or []
    previous = db.execute('SELECT metadata_json FROM papers WHERE canonical_id=%s FOR UPDATE', (canonical_id,)).fetchone()
    metadata = preserve_merge(previous[0] if previous else {}, paper.get('metadata') or {})
    db.execute(
        """
        INSERT INTO papers
        (canonical_id, title, abstract, doi, arxiv_id, url,
         metadata_json, has_full_text, created_at, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
        ON CONFLICT (canonical_id) DO UPDATE SET
          title = CASE WHEN length(papers.title) < length(EXCLUDED.title)
                       THEN EXCLUDED.title ELSE papers.title END,
          abstract = CASE WHEN length(papers.abstract) < length(EXCLUDED.abstract)
                          THEN EXCLUDED.abstract ELSE papers.abstract END,
          doi = CASE WHEN papers.doi = '' THEN EXCLUDED.doi ELSE papers.doi END,
          arxiv_id = CASE WHEN papers.arxiv_id = '' THEN EXCLUDED.arxiv_id ELSE papers.arxiv_id END,
          url = CASE WHEN papers.url = '' THEN EXCLUDED.url ELSE papers.url END,
          metadata_json = papers.metadata_json || EXCLUDED.metadata_json,
          has_full_text = papers.has_full_text OR EXCLUDED.has_full_text,
          updated_at = NOW()
        """,
        (
            canonical_id, paper.get("title") or "", paper.get("abstract") or "",
            paper.get("doi") or "", paper.get("arxiv_id") or "",
            paper.get("url") or "", Jsonb(metadata), bool(paper.get("has_full_text", bool(sections))),
        ),
    )
    # NULL journal year stays NULL; never substitute the preprint year.
    current = db.execute('SELECT publication, identifiers FROM papers WHERE canonical_id=%s', (canonical_id,)).fetchone()
    publication = preserve_merge(current[0], paper.get('publication') or {})
    identifiers = preserve_merge(current[1], paper.get('identifiers') or {})
    db.execute('UPDATE papers SET publication=%s, identifiers=%s, publication_year=COALESCE(publication_year,%s) WHERE canonical_id=%s',
               (Jsonb(publication), Jsonb(identifiers), publication.get('journal_publication_year'), canonical_id))
    if paper.get('schema_version') == '2.0':
        # Immutable extraction evidence for each PDF checksum preserves older versions.
        db.execute('''INSERT INTO paper_master_versions(canonical_id,source,source_id,pdf_sha256,master_json)
                      VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                   (canonical_id, paper['source'], paper['source_id'], checksum, Jsonb(paper)))
        old_master = db.execute('SELECT master_json FROM paper_master_sources WHERE canonical_id=%s AND source=%s AND source_id=%s',
                                (canonical_id, paper['source'], paper['source_id'])).fetchone()
        # Preserve the better text snapshot; retain new provenance/metadata separately through merge.
        master = paper
        if old_master:
            old = old_master[0]
            master = preserve_merge(old, paper)
            use_new = paper['has_full_text'] and (not old.get('has_full_text') or sum(len(s['text']) for s in paper['full_text']) >= sum(len(s['text']) for s in old.get('full_text', [])))
            chosen = paper if use_new else old
            for key in ('full_text', 'sections', 'has_full_text', 'extraction_quality', 'figures_and_tables',
                        'references', 'citation_markers', 'provenance', 'identifiers'):
                if key in chosen:
                    master[key] = chosen[key]
        db.execute('''INSERT INTO paper_master_sources(canonical_id,source,source_id,master_json) VALUES(%s,%s,%s,%s)
                      ON CONFLICT(canonical_id,source,source_id) DO UPDATE SET master_json=EXCLUDED.master_json''',
                   (canonical_id, paper['source'], paper['source_id'], Jsonb(master)))
    db.execute(
        """
        INSERT INTO paper_sources (canonical_id, source, source_id, dataset_split)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (canonical_id, source, source_id, dataset_split) DO NOTHING
        """,
        (canonical_id, paper["source"], paper["source_id"], paper.get("split") or "corpus"),
    )
    for idx, section in enumerate(sections):
        db.execute(
            """
            INSERT INTO sections (canonical_id, source, section_idx, heading, body)
            VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (canonical_id, source, section_idx) DO UPDATE SET
              heading = CASE WHEN length(EXCLUDED.body)>length(sections.body) THEN EXCLUDED.heading ELSE sections.heading END,
              body = CASE WHEN length(EXCLUDED.body)>length(sections.body) THEN EXCLUDED.body ELSE sections.body END
            """,
            (canonical_id, paper["source"], idx, section["heading"], section["text"]),
        )


def stats(db: Connection) -> dict:
    result = {}
    # Table names come from this trusted literal list, never from user input.
    for table in ("papers", "paper_sources", "sections", "sessions", "messages", "memory_events"):
        result[table] = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    result["by_source"] = db.execute(
        "SELECT source, count(*) FROM paper_sources GROUP BY source ORDER BY source"
    ).fetchall()
    result["with_full_text"] = db.execute(
        "SELECT count(*) FROM papers WHERE has_full_text = TRUE"
    ).fetchone()[0]
    return result
