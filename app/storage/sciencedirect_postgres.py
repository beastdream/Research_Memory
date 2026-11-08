"""PostgreSQL storage backend for ScienceDirect Master Dataset in isolated schema.

Creates and manages:
- sciencedirect schema
- sciencedirect.papers
- sciencedirect.sections
- sciencedirect.references
- sciencedirect.figures_and_tables
- sciencedirect.qas
- sciencedirect.ingestion_runs

Ensures:
- Strict isolation from public arXiv tables.
- 100% verified DOI check constraints.
- Idempotent upserts.
- No dropping or truncating of existing tables.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg
from dotenv import load_dotenv
from psycopg import Connection
from psycopg.types.json import Jsonb

from app.ingestion.sciencedirect_client import normalize_doi
from app.ingestion.sciencedirect_schema import validate_sciencedirect_master

ROOT = Path(__file__).resolve().parents[2]

SCHEMA_STATEMENTS = (
    "CREATE SCHEMA IF NOT EXISTS sciencedirect;",
    """
    CREATE TABLE IF NOT EXISTS sciencedirect.papers (
        canonical_id TEXT PRIMARY KEY,
        doi TEXT UNIQUE NOT NULL,
        doi_url TEXT NOT NULL,
        title TEXT NOT NULL,
        abstract TEXT NOT NULL DEFAULT '',
        year INTEGER NOT NULL,
        topic_group TEXT NOT NULL,
        journal TEXT NOT NULL DEFAULT '',
        publisher TEXT NOT NULL DEFAULT '',
        publication_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        authors_json JSONB NOT NULL DEFAULT '[]'::jsonb,
        keywords_json JSONB NOT NULL DEFAULT '[]'::jsonb,
        doi_validation JSONB NOT NULL DEFAULT '{}'::jsonb,
        rights_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        provenance_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        quality_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        master_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        has_full_text BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT check_canonical_doi CHECK (canonical_id LIKE 'doi:%'),
        CONSTRAINT check_doi_verified CHECK ((doi_validation->>'status') = 'verified')
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS sciencedirect.sections (
        canonical_id TEXT NOT NULL REFERENCES sciencedirect.papers(canonical_id) ON DELETE CASCADE,
        section_idx INTEGER NOT NULL,
        section_id TEXT NOT NULL,
        heading TEXT NOT NULL,
        body TEXT NOT NULL,
        paragraphs_json JSONB NOT NULL DEFAULT '[]'::jsonb,
        extraction_status TEXT NOT NULL DEFAULT 'pending',
        PRIMARY KEY (canonical_id, section_idx)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS sciencedirect.references (
        canonical_id TEXT NOT NULL REFERENCES sciencedirect.papers(canonical_id) ON DELETE CASCADE,
        ref_idx INTEGER NOT NULL,
        raw_reference TEXT NOT NULL,
        doi TEXT,
        title TEXT,
        authors_json JSONB DEFAULT '[]'::jsonb,
        year INTEGER,
        resolution_status TEXT DEFAULT 'unresolved',
        PRIMARY KEY (canonical_id, ref_idx)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS sciencedirect.figures_and_tables (
        canonical_id TEXT NOT NULL REFERENCES sciencedirect.papers(canonical_id) ON DELETE CASCADE,
        item_idx INTEGER NOT NULL,
        item_id TEXT NOT NULL,
        item_type TEXT NOT NULL,
        label TEXT,
        caption TEXT,
        data_json JSONB DEFAULT '{}'::jsonb,
        extraction_status TEXT DEFAULT 'pending',
        PRIMARY KEY (canonical_id, item_idx)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS sciencedirect.qas (
        canonical_id TEXT NOT NULL REFERENCES sciencedirect.papers(canonical_id) ON DELETE CASCADE,
        qa_id TEXT NOT NULL,
        question TEXT NOT NULL,
        answer TEXT NOT NULL,
        evidence_json JSONB DEFAULT '[]'::jsonb,
        PRIMARY KEY (canonical_id, qa_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS sciencedirect.ingestion_runs (
        run_id TEXT PRIMARY KEY,
        target INTEGER NOT NULL,
        candidates_count INTEGER NOT NULL,
        verified_count INTEGER NOT NULL,
        inserted_count INTEGER NOT NULL,
        updated_count INTEGER NOT NULL,
        unchanged_count INTEGER NOT NULL,
        details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    "CREATE INDEX IF NOT EXISTS sciencedirect_papers_doi_idx ON sciencedirect.papers (doi);",
    "CREATE INDEX IF NOT EXISTS sciencedirect_papers_year_idx ON sciencedirect.papers (year);",
    "CREATE INDEX IF NOT EXISTS sciencedirect_papers_journal_idx ON sciencedirect.papers (journal);",
    "CREATE INDEX IF NOT EXISTS sciencedirect_papers_topic_idx ON sciencedirect.papers (topic_group);",
    "ALTER TABLE sciencedirect.papers ADD COLUMN IF NOT EXISTS pdf_sha256 TEXT;",
    "ALTER TABLE sciencedirect.papers ADD COLUMN IF NOT EXISTS pdf_path TEXT;",
    """
    CREATE TABLE IF NOT EXISTS sciencedirect.paper_enrichments (
        canonical_id TEXT PRIMARY KEY REFERENCES sciencedirect.papers(canonical_id) ON DELETE CASCADE,
        schema_version TEXT NOT NULL DEFAULT '2.1',
        document JSONB NOT NULL DEFAULT '{}'::jsonb,
        enriched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """,
    "CREATE INDEX IF NOT EXISTS sciencedirect_enrichments_version_idx ON sciencedirect.paper_enrichments (schema_version);",
)


def connect(autocommit: bool = True) -> Connection:
    """Connect to PostgreSQL using DATABASE_URL from .env or process environment."""
    load_dotenv(ROOT / ".env", override=False)
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("Missing DATABASE_URL in environment or .env file.")
    if not url.startswith(("postgresql://", "postgres://")):
        raise ValueError("DATABASE_URL must start with postgresql:// or postgres://")
    return psycopg.connect(url, connect_timeout=15, autocommit=autocommit)


def init_schema(db: Connection) -> None:
    """Initialize the dedicated sciencedirect schema and tables."""
    for stmt in SCHEMA_STATEMENTS:
        db.execute(stmt)


def upsert_sciencedirect_paper(db: Connection, record: Dict[str, Any]) -> str:
    """Idempotently upsert a ScienceDirect master record into sciencedirect schema.

    Returns one of: 'inserted', 'updated', 'unchanged'
    """
    validate_sciencedirect_master(record)
    canonical_id = record["canonical_id"]
    doi = normalize_doi(record["doi"])
    title = record["title"]
    abstract = record.get("abstract", "")
    year = int(record["year"])
    topic_group = record["topic_group"]
    pub = record.get("publication") or {}
    journal = pub.get("journal", "")
    publisher = pub.get("publisher", "")
    full_text = record.get("full_text") or {}
    sections = full_text.get("sections") or []
    has_full_text = bool(sections)

    # Check existing record
    existing = db.execute(
        "SELECT master_json, has_full_text FROM sciencedirect.papers WHERE canonical_id = %s",
        (canonical_id,),
    ).fetchone()

    status = "inserted"
    if existing:
        old_master, old_has_ft = existing
        # If content and metadata are equal, unchanged
        if old_master == record:
            return "unchanged"
        status = "updated"

    # Insert or update papers
    db.execute(
        """
        INSERT INTO sciencedirect.papers
        (canonical_id, doi, doi_url, title, abstract, year, topic_group, journal, publisher,
         publication_json, authors_json, keywords_json, doi_validation, rights_json,
         provenance_json, quality_json, master_json, has_full_text, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        ON CONFLICT (canonical_id) DO UPDATE SET
            title = EXCLUDED.title,
            abstract = EXCLUDED.abstract,
            year = EXCLUDED.year,
            topic_group = EXCLUDED.topic_group,
            journal = EXCLUDED.journal,
            publisher = EXCLUDED.publisher,
            publication_json = EXCLUDED.publication_json,
            authors_json = EXCLUDED.authors_json,
            keywords_json = EXCLUDED.keywords_json,
            doi_validation = EXCLUDED.doi_validation,
            rights_json = EXCLUDED.rights_json,
            provenance_json = EXCLUDED.provenance_json,
            quality_json = EXCLUDED.quality_json,
            master_json = EXCLUDED.master_json,
            has_full_text = EXCLUDED.has_full_text OR sciencedirect.papers.has_full_text,
            updated_at = NOW();
        """,
        (
            canonical_id,
            doi,
            record["doi_url"],
            title,
            abstract,
            year,
            topic_group,
            journal,
            publisher,
            Jsonb(pub),
            Jsonb(record.get("authors", [])),
            Jsonb(record.get("keywords", [])),
            Jsonb(record["doi_validation"]),
            Jsonb(record.get("rights", {})),
            Jsonb(record.get("provenance", {})),
            Jsonb(record.get("quality", {})),
            Jsonb(record),
            has_full_text,
        ),
    )

    # Upsert sections
    if sections:
        db.execute("DELETE FROM sciencedirect.sections WHERE canonical_id = %s", (canonical_id,))
        for idx, sec in enumerate(sections):
            body = " ".join(p.get("text", "") for p in sec.get("paragraphs", []))
            db.execute(
                """
                INSERT INTO sciencedirect.sections
                (canonical_id, section_idx, section_id, heading, body, paragraphs_json, extraction_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    canonical_id,
                    idx,
                    sec.get("section_id", f"sec_{idx}"),
                    sec.get("heading", "Untitled"),
                    body,
                    Jsonb(sec.get("paragraphs", [])),
                    sec.get("extraction_status", "complete"),
                ),
            )

    # Upsert references
    refs = record.get("references") or []
    if refs:
        db.execute("DELETE FROM sciencedirect.references WHERE canonical_id = %s", (canonical_id,))
        for idx, ref in enumerate(refs):
            db.execute(
                """
                INSERT INTO sciencedirect.references
                (canonical_id, ref_idx, raw_reference, doi, title, authors_json, year, resolution_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    canonical_id,
                    ref.get("ref_idx", idx),
                    ref.get("raw_reference", ""),
                    ref.get("doi"),
                    ref.get("title"),
                    Jsonb(ref.get("authors", [])),
                    ref.get("year"),
                    ref.get("resolution_status", "unresolved"),
                ),
            )

    # Upsert figures and tables
    ft = record.get("figures_and_tables") or []
    if ft:
        db.execute("DELETE FROM sciencedirect.figures_and_tables WHERE canonical_id = %s", (canonical_id,))
        for idx, item in enumerate(ft):
            db.execute(
                """
                INSERT INTO sciencedirect.figures_and_tables
                (canonical_id, item_idx, item_id, item_type, label, caption, data_json, extraction_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    canonical_id,
                    item.get("item_idx", idx),
                    item.get("item_id", f"item_{idx}"),
                    item.get("type", "figure"),
                    item.get("label"),
                    item.get("caption"),
                    Jsonb(item),
                    item.get("extraction_status", "complete"),
                ),
            )

    return status


def record_ingestion_run(
    db: Connection,
    target: int,
    candidates_count: int,
    verified_count: int,
    inserted_count: int,
    updated_count: int,
    unchanged_count: int,
    details: Optional[Dict[str, Any]] = None,
) -> str:
    """Record summary of an ingestion run into sciencedirect.ingestion_runs."""
    run_id = f"run_sciencedirect_{uuid.uuid4().hex[:12]}"
    db.execute(
        """
        INSERT INTO sciencedirect.ingestion_runs
        (run_id, target, candidates_count, verified_count, inserted_count, updated_count, unchanged_count, details_json, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
        """,
        (
            run_id,
            target,
            candidates_count,
            verified_count,
            inserted_count,
            updated_count,
            unchanged_count,
            Jsonb(details or {}),
        ),
    )
    return run_id


def sciencedirect_stats(db: Connection) -> Dict[str, Any]:
    """Retrieve statistics from sciencedirect schema."""
    result: Dict[str, Any] = {}
    for table in ("papers", "sections", "references", "figures_and_tables", "qas", "ingestion_runs"):
        result[table] = db.execute(f"SELECT count(*) FROM sciencedirect.{table}").fetchone()[0]

    result["by_topic"] = db.execute(
        "SELECT topic_group, count(*) FROM sciencedirect.papers GROUP BY topic_group ORDER BY count(*) DESC"
    ).fetchall()

    result["by_year"] = db.execute(
        "SELECT year, count(*) FROM sciencedirect.papers GROUP BY year ORDER BY year DESC"
    ).fetchall()

    result["with_full_text"] = db.execute(
        "SELECT count(*) FROM sciencedirect.papers WHERE has_full_text = TRUE"
    ).fetchone()[0]

    return result


def upsert_sciencedirect_enriched(db: Connection, enriched: Dict[str, Any]) -> str:
    """Idempotently upsert an enriched record into sciencedirect.paper_enrichments.

    Returns one of: 'inserted', 'updated', 'unchanged'
    """
    canonical_id = enriched["canonical_id"]
    schema_version = enriched.get("schema_version", "2.1")
    provenance = enriched.get("provenance") or {}
    pdf_sha256 = provenance.get("pdf_sha256")
    pdf_path = provenance.get("pdf_path")

    # Update papers table with PDF metadata if present
    if pdf_sha256 or pdf_path:
        db.execute(
            """
            UPDATE sciencedirect.papers
            SET pdf_sha256 = COALESCE(%s, pdf_sha256),
                pdf_path = COALESCE(%s, pdf_path),
                updated_at = NOW()
            WHERE canonical_id = %s
            """,
            (pdf_sha256, pdf_path, canonical_id),
        )

    existing = db.execute(
        "SELECT document FROM sciencedirect.paper_enrichments WHERE canonical_id = %s",
        (canonical_id,),
    ).fetchone()

    if existing:
        if existing[0] == enriched:
            return "unchanged"
        db.execute(
            """
            UPDATE sciencedirect.paper_enrichments
            SET schema_version = %s,
                document = %s,
                enriched_at = NOW()
            WHERE canonical_id = %s
            """,
            (schema_version, Jsonb(enriched), canonical_id),
        )
        return "updated"
    else:
        db.execute(
            """
            INSERT INTO sciencedirect.paper_enrichments (canonical_id, schema_version, document, enriched_at)
            VALUES (%s, %s, %s, NOW())
            """,
            (canonical_id, schema_version, Jsonb(enriched)),
        )
        return "inserted"


# Backward-compatible alias
upsert_paper_enrichment = upsert_sciencedirect_enriched


