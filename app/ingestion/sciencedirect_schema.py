"""ScienceDirect Master Dataset schema validator and invariant enforcement.

Enforces:
- Draft 2020-12 JSON Schema compliance.
- Strict canonical identity: canonical_id == 'doi:' + normalize_doi(doi).
- 100% verified DOI invariant: doi_validation['status'] == 'verified' and title_match is True.
- Source invariant: source == 'sciencedirect'.
- Publication year invariant: 1950 <= year <= 2026 (no future publications).
- Cross-field extraction consistency.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from jsonschema import Draft202012Validator, FormatChecker

from app.ingestion.sciencedirect_client import normalize_doi

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas/sciencedirect_master.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def validate_sciencedirect_master(record: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a ScienceDirect Master Dataset record against JSON schema and business invariants.

    Raises ValueError or jsonschema.ValidationError on failure.
    """
    VALIDATOR.validate(record)

    # 1. Source invariant
    if record.get("source") != "sciencedirect":
        raise ValueError(f"Source must be 'sciencedirect', got: {record.get('source')!r}")

    # 2. Canonical identity invariant
    doi = normalize_doi(record.get("doi"))
    if not doi:
        raise ValueError("Record contains invalid or missing DOI")

    expected_canonical_id = f"doi:{doi}"
    if record.get("canonical_id") != expected_canonical_id:
        raise ValueError(
            f"Canonical ID mismatch: expected {expected_canonical_id!r}, "
            f"got {record.get('canonical_id')!r}"
        )

    # 3. DOI Verification mandatory invariant
    doi_val = record.get("doi_validation") or {}
    if doi_val.get("status") != "verified":
        raise ValueError(
            f"Paper DOI {doi} is not verified. "
            f"doi_validation.status={doi_val.get('status')!r}. "
            "Only verified DOIs are permitted in the Master Dataset."
        )

    if not doi_val.get("title_match"):
        raise ValueError(f"Paper DOI {doi} failed title verification match.")

    # 4. Publication year invariant: no future dates
    year = record.get("year")
    if not isinstance(year, int) or year < 1950 or year > 2026:
        raise ValueError(f"Invalid publication year {year}. Must be between 1950 and 2026.")

    # 5. Full-text extraction consistency
    full_text = record.get("full_text") or {}
    sections = full_text.get("sections", [])
    ft_status = full_text.get("extraction_status")
    if sections and ft_status in ("unavailable", "access_denied", "metadata_only"):
        raise ValueError(f"Inconsistent full_text: sections are present but extraction_status is {ft_status!r}")

    # 6. Figures and tables integrity
    for item in record.get("figures_and_tables", []):
        if not item.get("item_id"):
            raise ValueError("Figure/table item missing item_id")
        if item.get("type") not in ("figure", "table"):
            raise ValueError(f"Invalid item type: {item.get('type')!r}")

    # 7. References integrity
    for ref in record.get("references", []):
        if not ref.get("raw_reference"):
            raise ValueError("Reference missing raw_reference text")

    return record
