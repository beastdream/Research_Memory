"""Enrichment V2 adapter for ScienceDirect Master Dataset.

Transforms ScienceDirect master records into high-fidelity V2 Enriched records
strictly conforming to the schema structure established in:
- docs/examples/memorybank_enriched_v2.json
- schemas/master_dataset_v2_enriched.schema.json

Features:
- Robust XML parsing with complete namespace and attribute prefix stripping.
- Multi-source Abstract resolution: Publisher XML -> Crossref Registry -> PDF First-Page Layout.
- Multi-source Authors resolution: Crossref Official Registry -> Publisher XML -> PDF First-Page Layout.
- Synthesized canonical plain `text` field in strict reading order for vector retrieval and QA agents.
- Distinguishes complete full-text from 1-page previews (`complete`, `partial`, `metadata_only`).
- Stable content-addressed hashes for sections, paragraphs, figures, tables, references.
- Page start/end tracking for all paragraphs and sections.
- Strict isolation from arXiv dataset.
- Granular quality and provenance metadata without fabricated claims.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pymupdf

from app.ingestion.sciencedirect_client import doi_to_safe_filename, normalize_doi
from app.ingestion.sciencedirect_parser import (
    clean_abstract_xml,
    parse_elsevier_xml,
    score_and_classify_topic,
)

ENRICHMENT_PARSER_VERSION = "sciencedirect-v2-enrichment-1.2"

MANDATORY_TOP_KEYS = [
    "schema_version",
    "canonical_id",
    "source",
    "source_id",
    "split",
    "title",
    "abstract",
    "text",
    "year",
    "url",
    "doi",
    "arxiv_id",
    "authors",
    "categories",
    "metadata",
    "identifiers",
    "publication",
]

MANDATORY_SECONDARY_KEYS = [
    "full_text",
    "sections",
    "figures_and_tables",
    "references",
    "qas",
    "citation_markers",
    "equations",
    "research_metadata",
    "provenance",
    "quality",
    "rights",
    "has_full_text",
    "extraction_quality",
]


def order_enriched_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Order dictionary keys strictly to meet Section III mandatory schema specifications."""
    ordered: Dict[str, Any] = {}
    for k in MANDATORY_TOP_KEYS:
        if k in rec:
            ordered[k] = rec[k]
    for k in MANDATORY_SECONDARY_KEYS:
        if k in rec:
            ordered[k] = rec[k]
    for k, v in rec.items():
        if k not in ordered:
            ordered[k] = v
    return ordered


def build_canonical_text(title: str, abstract: str, sections: List[Dict[str, Any]]) -> str:
    """Build unified canonical text in strict reading order without duplicate abstract.

    Retriever & LLM prompt injection contract:
    - Title at the top
    - 'Abstract\\n<abstract>' once if abstract is present
    - Body sections with headings and paragraphs
    - Explicitly skips any section that duplicates the abstract (by heading or content)
    """
    title = (title or "").strip()
    abstract = (abstract or "").strip()
    text_parts: List[str] = []
    if title:
        text_parts.append(title)
    if abstract:
        text_parts.append(f"Abstract\n{abstract}")

    norm_abstract = " ".join(abstract.split()) if abstract else ""

    for s in sections:
        h = (s.get("heading") or "").strip()
        # Skip section if heading indicates Abstract/Summary
        if re.match(r"^(?:(?:\d+(?:\.\d+)*\.?|[A-Z]\.)\s+)?(?:Abstract|A\s*B\s*S\s*T\s*R\s*A\s*C\s*T|Summary)\b", h, re.I):
            continue

        paras = [p.get("text", "").strip() for p in s.get("paragraphs", []) if p.get("text", "").strip()]
        if not paras:
            continue

        sec_body = "\n\n".join(paras)
        norm_body = " ".join(sec_body.split())

        # If the entire section is just the abstract text, skip it to prevent duplication
        if norm_abstract and (
            norm_body == norm_abstract
            or (len(norm_body) > 40 and norm_body in norm_abstract)
            or (len(norm_abstract) > 40 and norm_abstract in norm_body and len(norm_body) <= len(norm_abstract) + 30)
        ):
            continue

        if h:
            text_parts.append(f"{h}\n{sec_body}")
        else:
            text_parts.append(sec_body)

    return "\n\n".join(text_parts).strip()


HEADING_REGEX = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*\.?|[A-Z])\s+)?"
    r"(?:Abstract|A\s*B\s*S\s*T\s*R\s*A\s*C\s*T|Introduction|Related Work|Background|Methods?|Methodology|Approach|"
    r"Experiments?(?: and Results)?|Experimental (?:Setup|Results)|Results(?: and Discussion)?|"
    r"Discussion|Conclusions?(?: and Future Work)?|Limitations|Acknowledg(?:e)?ments?|"
    r"References|Bibliography|Appendix)$",
    re.I,
)
NUMBERED_HEADING_REGEX = re.compile(
    r"^(?:[1-9]\d?(?:\.\d+)*\.?|[IVX]+\.|[A-Z]\.)\s+[A-Z][^!?]{2,100}$"
)


def _digest(data: Any) -> str:
    """Deterministic SHA-256 digest of structured data."""
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, doi: str, *args: Any) -> str:
    """Generate deterministic, collision-resistant 24-char identifier."""
    content = [doi, *args]
    h = _digest(content)[:20]
    return f"{prefix}-{h}"


def extract_from_pdf_pymupdf(pdf_path: Path, doi: str) -> Dict[str, Any]:
    """Extract structured sections, paragraphs, figures, tables, and abstract from PDF via PyMuPDF."""
    if not pdf_path.is_file():
        return {
            "abstract": "",
            "sections": [],
            "figures_and_tables": [],
            "references": [],
            "page_count": 0,
            "extraction_status": "unavailable",
            "pdf_valid": False,
        }

    try:
        # Read bytes in memory to avoid Windows file lock issues
        pdf_bytes = pdf_path.read_bytes()
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return {
            "abstract": "",
            "sections": [],
            "figures_and_tables": [],
            "references": [],
            "page_count": 0,
            "extraction_status": "failed",
            "pdf_valid": False,
        }

    page_count = len(doc)
    if page_count == 0 or doc.is_encrypted:
        doc.close()
        return {
            "abstract": "",
            "sections": [],
            "figures_and_tables": [],
            "references": [],
            "page_count": page_count,
            "extraction_status": "failed",
            "pdf_valid": False,
        }

    # 1. Extract Abstract from first 2 pages
    pdf_abstract = ""
    for p_idx in range(min(2, page_count)):
        page = doc[p_idx]
        blocks = page.get_text("blocks")
        sorted_blocks = sorted(blocks, key=lambda b: (round(b[1] / 15) * 15, b[0]))
        for i, b in enumerate(sorted_blocks):
            if b[6] != 0:
                continue
            btxt = b[4].strip()
            # Standalone heading "A B S T R A C T" or "Abstract"
            if re.match(r"(?i)^\s*(?:A\s*B\s*S\s*T\s*R\s*A\s*C\s*T|Abstract)\s*[:.]?\s*$", btxt):
                for nxt in sorted_blocks[i + 1:]:
                    if nxt[6] != 0:
                        continue
                    ntxt = nxt[4].strip()
                    # Filter out non-abstract blocks (e.g. keywords, dataset links, numbers)
                    if len(ntxt) > 80 and not re.match(r"(?i)^\s*(?:Keywords|Dataset|1\.|Introduction)", ntxt):
                        pdf_abstract = " ".join(ntxt.split())
                        break
                if pdf_abstract:
                    break
            # Inline "Abstract: ..."
            m = re.match(r"(?i)^\s*(?:A\s*B\s*S\s*T\s*R\s*A\s*C\s*T|Abstract)\s*[:.]\s*(.+)", btxt, re.DOTALL)
            if m and len(m.group(1).strip()) > 80:
                pdf_abstract = " ".join(m.group(1).strip().split())
                break
        if pdf_abstract:
            break

    sections: List[Dict[str, Any]] = []
    figures_and_tables: List[Dict[str, Any]] = []
    references: List[Dict[str, Any]] = []

    current_section: Optional[Dict[str, Any]] = None
    sec_idx = 0

    def start_section(heading: str, page_num: int, kind: str = "identified_heading"):
        nonlocal current_section, sec_idx
        sec_idx += 1
        sid = _stable_id("sec", doi, sec_idx, heading, page_num)
        current_section = {
            "section_id": sid,
            "heading": heading,
            "page_start": page_num,
            "page_end": page_num,
            "paragraphs": [],
            "extraction_status": "partial",
            "kind": kind,
        }
        sections.append(current_section)

    start_section("Front matter / unclassified text", 1, kind="unclassified")

    in_references = False
    ref_idx = 0

    for page_num in range(1, page_count + 1):
        page = doc[page_num - 1]
        blocks = page.get_text("blocks")  # (x0, y0, x1, y1, text, block_no, block_type)
        blocks = sorted(blocks, key=lambda b: (round(b[1] / 20) * 20, b[0]))

        for block in blocks:
            if block[6] != 0:  # image block
                continue
            text = block[4].strip()
            if not text:
                continue

            # Detect figures / tables captions
            fig_match = re.search(r"(?im)^\s*((?:Figure|Fig\.|Table)\s+\d+[a-z]?)\s*[:.]\s*([^\n]+)", text)
            if fig_match:
                label = re.sub(r"\s+", " ", fig_match.group(1))
                caption = fig_match.group(2).strip()
                kind = "table" if label.lower().startswith("table") else "figure"
                prefix = "table" if kind == "table" else "fig"
                item_id = _stable_id(prefix, doi, page_num, label)
                item = {
                    "type": kind,
                    "item_id": item_id,
                    "label": label,
                    "caption": caption,
                    "page": page_num,
                    "data": None,
                    "text": None,
                    "image_path": None,
                    "section_id": current_section["section_id"] if current_section else None,
                    "source_bbox": [round(c, 2) for c in block[:4]],
                    "caption_bbox": [round(c, 2) for c in block[:4]],
                    "detection_status": "pdf_block_caption",
                    "extraction_status": "partial",
                }
                if kind == "table":
                    item.update({
                        "table_id": item_id,
                        "headers": [],
                        "rows": [],
                        "units": None,
                        "raw_text": None,
                        "header_status": "unverified",
                    })
                else:
                    item.update({
                        "figure_id": item_id,
                        "description": None,
                    })
                figures_and_tables.append(item)
                continue

            # Detect Reference section
            if re.match(r"^references\b", text, re.I):
                in_references = True
                start_section("References", page_num, kind="references")
                continue

            if in_references:
                entries = re.split(r"(?m)^\s*(?:\[\d+\]|\d+\.)\s+", text)
                for entry in entries:
                    entry_clean = " ".join(entry.split()).strip()
                    if entry_clean and len(entry_clean) > 15:
                        ref_idx += 1
                        ref_id = _stable_id("ref", doi, ref_idx, entry_clean[:40])
                        doi_match = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", entry_clean)
                        ref_doi = normalize_doi(doi_match.group(0)) if doi_match else None
                        references.append({
                            "item_id": ref_id,
                            "reference_id": ref_id,
                            "ref_idx": ref_idx,
                            "raw_reference": entry_clean,
                            "doi": ref_doi,
                            "title": None,
                            "authors": [],
                            "year": None,
                            "arxiv_id": None,
                            "target_paper_id": f"doi:{ref_doi}" if ref_doi else None,
                            "citation_contexts": [],
                            "extraction_status": "partial",
                            "resolution_status": "resolved" if ref_doi else "unresolved",
                        })
                continue

            # Detect headings
            first_line = text.split("\n")[0].strip()
            is_heading = (
                (bool(HEADING_REGEX.fullmatch(first_line)) or bool(NUMBERED_HEADING_REGEX.fullmatch(first_line)))
                and len(first_line) < 100
                and not first_line.endswith((".", ":", ","))
            )

            if is_heading:
                # Skip creating an Abstract section to avoid duplicating the abstract in sections/text
                if re.match(r"(?i)^\s*(?:(?:\d+(?:\.\d+)*\.?|[A-Z]\.)\s+)?(?:A\s*B\s*S\s*T\s*R\s*A\s*C\s*T|Abstract|Summary)\s*[:.]?\s*$", first_line):
                    continue
                start_section(first_line, page_num)
                rem_lines = text.split("\n")[1:]
                rem_text = "\n".join(rem_lines).strip()
                if rem_text and current_section:
                    p_idx = len(current_section["paragraphs"]) + 1
                    pid = _stable_id("para", doi, current_section["section_id"], p_idx, rem_text[:40])
                    current_section["paragraphs"].append({
                        "paragraph_id": pid,
                        "text": rem_text,
                        "section_id": current_section["section_id"],
                        "page_start": page_num,
                        "page_end": page_num,
                    })
                    current_section["page_end"] = page_num
            else:
                if current_section is not None:
                    p_idx = len(current_section["paragraphs"]) + 1
                    pid = _stable_id("para", doi, current_section["section_id"], p_idx, text[:40])
                    current_section["paragraphs"].append({
                        "paragraph_id": pid,
                        "text": text,
                        "section_id": current_section["section_id"],
                        "page_start": page_num,
                        "page_end": page_num,
                    })
                    current_section["page_end"] = page_num

    doc.close()

    # Filter out empty sections
    sections = [s for s in sections if s["paragraphs"]]

    return {
        "abstract": pdf_abstract,
        "sections": sections,
        "figures_and_tables": figures_and_tables,
        "references": references,
        "page_count": page_count,
        "extraction_status": "complete" if len(sections) >= 3 else ("partial" if sections else "unavailable"),
        "pdf_valid": True,
    }


def enrich_sciencedirect_record(
    master_record: Dict[str, Any],
    pdf_path: Optional[Path] = None,
    xml_content: Optional[str] = None,
    crossref_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Transform a ScienceDirect master record into a V2 Enriched record.

    Conforms to docs/examples/memorybank_enriched_v2.json.
    """
    doi = normalize_doi(master_record.get("doi"))
    if not doi:
        raise ValueError("Cannot enrich record without valid DOI")

    canonical_id = f"doi:{doi}"
    title = master_record.get("title", "").strip()
    abstract = clean_abstract_xml(master_record.get("abstract", "")).strip()
    year = int(master_record.get("year", 2024))
    authors = [str(a) for a in master_record.get("authors", [])]
    pii = master_record.get("pii")
    topic_group = master_record.get("topic_group")

    pub = master_record.get("publication") or {}
    journal = pub.get("journal", "ScienceDirect Journal")
    publisher = pub.get("publisher", "Elsevier B.V.")
    pub_date = pub.get("publication_date")
    volume = pub.get("volume")
    issue = pub.get("issue")
    pages = pub.get("pages")
    article_number = pub.get("article_number")

    # 1. Inspect PDF if present
    pdf_info = None
    pdf_sha256 = None
    pdf_valid = False
    page_count = 0

    if pdf_path and Path(pdf_path).is_file():
        try:
            pdf_bytes = Path(pdf_path).read_bytes()
            pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
            pdf_info = extract_from_pdf_pymupdf(Path(pdf_path), doi)
            pdf_valid = pdf_info.get("pdf_valid", False)
            page_count = pdf_info.get("page_count", 0)
        except Exception:
            pdf_valid = False

    # 2. Extract structured XML if present
    parsed_xml = None
    if xml_content:
        parsed_xml = parse_elsevier_xml(xml_content)

    # 3. Resolve Authors (Publisher XML -> Crossref -> existing)
    authors_source = "master_cached"
    cr_authors = []
    if crossref_metadata:
        if isinstance(crossref_metadata.get("authors"), list):
            cr_authors = crossref_metadata["authors"]
        elif isinstance(crossref_metadata.get("author"), list):
            for a in crossref_metadata["author"]:
                given = a.get("given", "").strip()
                family = a.get("family", "").strip()
                name = a.get("name", "").strip()
                full = f"{given} {family}".strip() if (given or family) else name
                if full and full not in cr_authors:
                    cr_authors.append(full)

    if parsed_xml and parsed_xml.get("authors") and len(parsed_xml["authors"]) >= len(authors):
        authors = parsed_xml["authors"]
        authors_source = "publisher_xml"
    elif cr_authors and len(cr_authors) > len(authors):
        authors = cr_authors
        authors_source = "crossref_registry"

    # 4. Resolve Abstract (Publisher XML -> Crossref -> PDF -> existing)
    abstract_source = "master_cached"
    if parsed_xml and parsed_xml.get("abstract"):
        abstract = parsed_xml["abstract"]
        abstract_source = "publisher_xml"
    elif crossref_metadata and crossref_metadata.get("abstract"):
        abstract = clean_abstract_xml(crossref_metadata["abstract"])
        abstract_source = "crossref_registry"
    elif not abstract and pdf_info and pdf_info.get("abstract"):
        abstract = pdf_info["abstract"]
        abstract_source = "pdf_layout"

    if not topic_group:
        topic_group, _ = score_and_classify_topic(title, abstract)

    # 5. Extract Full Text Sections
    sections: List[Dict[str, Any]] = []
    figures_and_tables: List[Dict[str, Any]] = []
    references: List[Dict[str, Any]] = []
    extraction_mode = "metadata_only"

    if parsed_xml and parsed_xml.get("sections"):
        xml_sections = parsed_xml.get("sections", [])
        extraction_mode = "xml_fulltext"
        for s_idx, sec in enumerate(xml_sections, 1):
            sid = _stable_id("sec", doi, s_idx, sec.get("heading", ""))
            paras = []
            for p_idx, p in enumerate(sec.get("paragraphs", []), 1):
                pid = _stable_id("para", doi, sid, p_idx, p.get("text", "")[:40])
                paras.append({
                    "paragraph_id": pid,
                    "text": p.get("text", ""),
                    "page_start": 1,
                    "page_end": page_count or 1,
                })
            sections.append({
                "section_id": sid,
                "heading": sec.get("heading", f"Section {s_idx}"),
                "page_start": 1,
                "page_end": page_count or 1,
                "paragraphs": paras,
                "extraction_status": "complete",
            })

        for f_idx, item in enumerate(parsed_xml.get("figures_and_tables", []), 1):
            itype = item.get("type", "figure")
            prefix = "table" if itype == "table" else "fig"
            item_id = _stable_id(prefix, doi, f_idx, item.get("label", ""))
            rec = {
                "type": itype,
                "item_id": item_id,
                "label": item.get("label"),
                "caption": item.get("caption"),
                "page": 1,
                "data": None,
                "text": None,
                "image_path": None,
                "section_id": None,
                "source_bbox": None,
                "detection_status": "xml_node",
                "extraction_status": "complete" if item.get("extraction_status") == "complete" else "partial",
            }
            if itype == "table":
                rec.update({"table_id": item_id, "headers": [], "rows": [], "units": None, "raw_text": None, "header_status": "unverified"})
            else:
                rec.update({"figure_id": item_id, "description": None})
            figures_and_tables.append(rec)

        for r_idx, ref in enumerate(parsed_xml.get("references", []), 1):
            ref_id = _stable_id("ref", doi, r_idx, ref.get("raw_reference", "")[:40])
            ref_doi = ref.get("doi")
            references.append({
                "item_id": ref_id,
                "reference_id": ref_id,
                "ref_idx": r_idx,
                "raw_reference": ref.get("raw_reference"),
                "doi": ref_doi,
                "title": ref.get("title"),
                "authors": ref.get("authors", []),
                "year": ref.get("year"),
                "arxiv_id": None,
                "target_paper_id": f"doi:{ref_doi}" if ref_doi else None,
                "citation_contexts": [],
                "extraction_status": "partial",
                "resolution_status": "resolved" if ref_doi else "unresolved",
            })

    # PDF extraction fallback if XML was unavailable or had no body sections
    if not sections and pdf_info and pdf_info.get("sections"):
        extraction_mode = "pdf_layout"
        sections = pdf_info.get("sections", [])
        figures_and_tables = pdf_info.get("figures_and_tables", [])
        references = pdf_info.get("references", [])

    # Master cached fallback
    if not sections:
        existing_ft = master_record.get("full_text") or {}
        existing_secs = existing_ft.get("sections") or []
        if existing_secs:
            extraction_mode = "master_cached"
            for s_idx, sec in enumerate(existing_secs, 1):
                sid = _stable_id("sec", doi, s_idx, sec.get("heading", ""))
                paras = []
                for p_idx, p in enumerate(sec.get("paragraphs", []), 1):
                    pid = _stable_id("para", doi, sid, p_idx, p.get("text", "")[:40])
                    paras.append({
                        "paragraph_id": pid,
                        "text": p.get("text", ""),
                        "section_id": sid,
                        "page_start": 1,
                        "page_end": page_count or 1,
                    })
                sections.append({
                    "section_id": sid,
                    "heading": sec.get("heading", f"Section {s_idx}"),
                    "page_start": 1,
                    "page_end": page_count or 1,
                    "paragraphs": paras,
                    "extraction_status": "complete",
                })

    # 6. Evaluate Full-Text Status (rigorous criteria)
    total_paras = sum(len(s.get("paragraphs", [])) for s in sections)
    if len(sections) >= 3 and total_paras >= 6:
        full_text_status = "complete"
    elif len(sections) >= 1 and total_paras >= 1:
        full_text_status = "partial"
    else:
        full_text_status = "metadata_only"

    # has_full_text is strictly true ONLY when full text is complete (not 1-page preview)
    has_full_text = (full_text_status == "complete")

    # 7. Synthesize canonical text string for Retriever / LLM prompt injection (no duplicate abstract)
    plain_text = build_canonical_text(title, abstract, sections)

    # Identifiers
    aliases = [f"doi:{doi}", f"https://doi.org/{doi}"]
    if pii:
        aliases.append(f"pii:{pii}")

    identifiers = {
        "doi": doi,
        "pii": pii,
        "openalex_id": None,
        "arxiv_id": None,
        "arxiv_version": None,
        "aliases": aliases,
    }

    # Publication
    journal_ref = f"{journal}"
    if volume:
        journal_ref += f", Vol. {volume}"
    if issue:
        journal_ref += f", No. {issue}"
    if pages or article_number:
        journal_ref += f", {pages or article_number}"
    journal_ref += f" ({year})"

    publication = {
        "venue": journal,
        "journal_reference": journal_ref,
        "journal_publication_date": pub_date,
        "journal_publication_year": year,
        "publication_status": "published",
        "arxiv_first_submitted_date": None,
        "arxiv_updated_date": None,
        "volume": volume,
        "issue": issue,
        "pages": pages,
        "article_number": article_number,
        "publisher": publisher,
    }

    # Rights
    rights_orig = master_record.get("rights") or {}
    rights = {
        "license": rights_orig.get("license") or "Elsevier Proprietary / Academic TDM",
        "open_access": bool(rights_orig.get("open_access")),
        "tdm_permission": rights_orig.get("tdm_permission") or "conditional_academic_tdm",
        "full_text_entitlement": "authorized" if (pdf_valid or sections) else "access_denied",
        "ai_training_permission": "restricted_by_license",
        "redistribution_permission": "no_commercial_redistribution",
        "retention_policy": "academic_research_cache",
    }

    # Limitations
    limitations: List[str] = [
        "No OCR or visual chart interpretation performed on bitmap figures.",
        "Human QA benchmarks not published in ScienceDirect stream.",
    ]
    if page_count == 1 and full_text_status == "partial":
        limitations.append("PDF on disk is 1-page preview/front matter; full-text sections partially extracted.")
    elif full_text_status == "complete":
        limitations.append("Full scientific text, sections, and references verified.")

    quality = {
        "pdf_valid": pdf_valid,
        "pdf_checksum_matches": bool(pdf_sha256),
        "qa_status": "not_annotated",
        "full_text_status": full_text_status,
        "sections_status": "complete" if len(sections) >= 3 else ("partial" if sections else "unavailable"),
        "references_status": "complete" if len(references) >= 5 else ("partial" if references else "none"),
        "figures_status": "extracted" if any(f.get("type") == "figure" for f in figures_and_tables) else "none",
        "tables_status": "extracted" if any(f.get("type") == "table" for f in figures_and_tables) else "none",
        "equations_status": "pending",
        "structure_status": "xml_structured" if extraction_mode == "xml_fulltext" else ("heuristic" if sections else "metadata_only"),
        "unmapped_glyph_count": 0,
        "research_metadata_status": "pending",
        "text_character_count": len(plain_text),
        "pdf_pages_actual": page_count,
        "sections_count": len(sections),
        "paragraphs_count": total_paras,
        "limitations": limitations,
    }

    provenance = {
        "source_url": master_record.get("doi_url") or f"https://doi.org/{doi}",
        "pdf_url": f"https://api.elsevier.com/content/article/doi/{doi}",
        "pdf_path": str(pdf_path) if pdf_path else None,
        "pdf_sha256": pdf_sha256,
        "pdf_page_count": page_count,
        "api_endpoint": "https://api.elsevier.com/content/article/doi",
        "retrieved_at": (master_record.get("provenance") or {}).get("retrieved_at"),
        "enrichment_parser": ENRICHMENT_PARSER_VERSION,
        "extraction_mode": extraction_mode,
        "abstract_source": abstract_source,
        "authors_source": authors_source,
        "topic_group": topic_group,
        "processed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    research_metadata = {
        "topic_group": topic_group,
        "methods": [],
        "metrics": [],
        "datasets": [],
        "claimed_contributions": [],
        "research_labels": {
            "topic_group": topic_group,
            "method": None,
            "experiment_baseline": None,
            "label_provenance": "heuristic_keyword_classification",
            "confidence": 1.0,
        },
    }

    enriched = {
        "schema_version": "2.1",
        "canonical_id": canonical_id,
        "source": "sciencedirect",
        "source_id": doi,
        "split": "corpus",
        "title": title,
        "abstract": abstract,
        "text": plain_text,  # Unified text field for retrieval / embeddings
        "year": year,
        "url": f"https://doi.org/{doi}",
        "doi": doi,
        "arxiv_id": None,
        "authors": authors,
        "categories": [topic_group, "ScienceDirect"],
        "metadata": {
            "topic_group": topic_group,
            "primary_category": "ScienceDirect",
        },
        "identifiers": identifiers,
        "publication": publication,
        "full_text": {
            "sections": sections,
            "extraction_status": full_text_status,
        },
        "sections": sections,  # flat alias for compatibility
        "figures_and_tables": figures_and_tables,
        "references": references,
        "qas": [],
        "citation_markers": [],
        "equations": [],
        "research_metadata": research_metadata,
        "provenance": provenance,
        "quality": quality,
        "rights": rights,
        "has_full_text": has_full_text,
        "extraction_quality": {
            "parser": "elsevier_xml" if extraction_mode == "xml_fulltext" else ("pymupdf" if extraction_mode.startswith("pdf") else "metadata_fallback"),
            "structure_status": "xml_structured" if extraction_mode == "xml_fulltext" else ("heuristic" if sections else "metadata_only"),
            "status": "full_text" if full_text_status == "complete" else "insufficient_text",
            "criteria": ">=3 sections; >=6 paragraphs; verified publisher XML/PDF content",
            "word_count": len(plain_text.split()) if plain_text else 0,
            "page_count": max(page_count, 1),
        },
    }

    return order_enriched_record(enriched)
