"""Scientific parser and record builder for ScienceDirect articles.

Handles:
- Topic groups, proportional quota allocation, and relevance scoring.
- Scientific XML extraction for Elsevier articles (sections, paragraphs, figures, tables, references).
- Master record normalization strictly following Phase 6 schema.
"""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from app.ingestion.sciencedirect_client import normalize_doi

PARSER_VERSION = "sciencedirect-v1.0"

TOPIC_GROUPS: Dict[str, Tuple[int, List[str]]] = {
    "Large Language Models": (
        30,
        [
            "large language models",
            "llm",
            "foundation models",
            "generative ai",
            "transformer language models",
            "gpt",
            "pre-trained language models",
        ],
    ),
    "RAG and Information Retrieval": (
        35,
        [
            "retrieval augmented generation",
            "retrieval-augmented generation",
            "dense retrieval",
            "neural information retrieval",
            "reranking",
            "knowledge-intensive question answering",
            "vector search",
            "passage retrieval",
        ],
    ),
    "AI Agents and Long-Term Memory": (
        35,
        [
            "llm agents",
            "autonomous agents",
            "agentic memory",
            "long-term memory",
            "long term memory",
            "adaptive memory",
            "memory consolidation",
            "forgetting mechanism",
            "conversational memory",
            "memory bank",
        ],
    ),
    "Hallucination and Faithfulness": (
        25,
        [
            "llm hallucination",
            "hallucination",
            "faithfulness",
            "factual consistency",
            "ai verification",
            "grounded generation",
            "factuality",
            "hallucination detection",
        ],
    ),
    "Scientific NLP and QA": (
        25,
        [
            "scientific question answering",
            "scientific literature analysis",
            "long document understanding",
            "scientific knowledge graph",
            "research assistants",
            "scientific information extraction",
            "scholarly document processing",
        ],
    ),
}


def compute_quotas(target: int = 150, allow_small: bool = False) -> Dict[str, int]:
    """Compute topic group quotas for a given target between 100 and 200,
    proportionally allocated to preserve group weights (30:35:35:25:25).
    """
    if not allow_small and not (100 <= target <= 200):
        raise ValueError("Target must be between 100 and 200")
    if target <= 0:
        raise ValueError("Target must be a positive integer")
    total_base = sum(weight for weight, _ in TOPIC_GROUPS.values())
    quotas = {group: (weight * target) // total_base for group, (weight, _) in TOPIC_GROUPS.items()}
    remainder = target - sum(quotas.values())
    # Distribute remainder by highest fractional parts
    sorted_groups = sorted(
        TOPIC_GROUPS.keys(),
        key=lambda g: -((TOPIC_GROUPS[g][0] * target) % total_base),
    )
    for g in sorted_groups[:remainder]:
        quotas[g] += 1
    return quotas


def score_and_classify_topic(title: str, abstract: str) -> Tuple[str, float]:
    """Classify text into one of the 5 topic groups and calculate relevance score."""
    text = (title + " " + abstract).lower().replace("-", " ")
    scores: Dict[str, float] = {}
    for group, (_, keywords) in TOPIC_GROUPS.items():
        score = 0.0
        for kw in keywords:
            kw_clean = kw.lower().replace("-", " ")
            if kw_clean in title.lower():
                score += 8.0
            if kw_clean in abstract.lower():
                score += 3.0
            elif kw_clean in text:
                score += 1.0
        scores[group] = score

    best_group = max(scores, key=scores.get)
    best_score = scores[best_group]
    return best_group, best_score


def clean_abstract_xml(abstract_raw: str) -> str:
    """Clean abstract text by stripping JATS/Elsevier XML tags (e.g. <jats:p>, <ce:para>),

    normalizing whitespace, repairing broken hyphenated linebreaks, and preserving
    verbatim scientific notation and mathematical formulas.
    """
    if not abstract_raw:
        return ""
    # Strip explicit title headers inside abstract XML
    cleaned = re.sub(r"<(?:ce:)?section-title[^>]*>.*?</(?:ce:)?section-title>", " ", abstract_raw, flags=re.IGNORECASE)
    cleaned = re.sub(r"<(?:ce:)?title[^>]*>.*?</(?:ce:)?title>", " ", cleaned, flags=re.IGNORECASE)
    # Strip remaining XML tags
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    # Repair hyphenation across linebreaks (e.g., 'trans- former' -> 'transformer')
    cleaned = re.sub(r"(\b[a-zA-Z]{3,})-\s+([a-zA-Z]{3,}\b)", r"\1\2", cleaned)
    # Normalize whitespace
    cleaned = " ".join(cleaned.split())
    # Strip leading "Abstract" or "ABSTRACT"
    cleaned = re.sub(r"^(?:Abstract|ABSTRACT)[:\s\-]+", "", cleaned).strip()
    return cleaned


def parse_elsevier_xml(xml_content: str) -> Dict[str, Any]:
    """Parse Elsevier / ScienceDirect full-text XML into structured sections,

    figures/tables, references, authors, and publication metadata.
    """
    if not xml_content or not xml_content.strip():
        return {
            "abstract": "",
            "authors": [],
            "sections": [],
            "figures_and_tables": [],
            "references": [],
            "publication_meta": {},
            "extraction_status": "unavailable",
        }

    try:
        # Strip XML namespace declarations, tag prefixes, and attribute prefixes for robust traversal
        clean_xml = re.sub(r' xmlns(:\w+)?="[^"]*"', "", xml_content)
        clean_xml = re.sub(r'<(/?)(\w+:)', r'<\1', clean_xml)
        clean_xml = re.sub(r'(\s+)[\w\-]+:([\w\-]+=)', r'\1\2', clean_xml)
        root = ET.fromstring(clean_xml)
    except Exception:
        return {
            "abstract": "",
            "authors": [],
            "sections": [],
            "figures_and_tables": [],
            "references": [],
            "publication_meta": {},
            "extraction_status": "partial",
        }

    # Extract abstract text
    abstract_text = ""
    for abs_elem in root.findall(".//abstract"):
        abs_raw = " ".join("".join(abs_elem.itertext()).split()).strip()
        abs_clean = clean_abstract_xml(abs_raw)
        if abs_clean and len(abs_clean) > len(abstract_text):
            abstract_text = abs_clean

    # Fallback to coredata description if abstract element was missing
    if not abstract_text:
        desc_elem = root.find(".//coredata/description") or root.find(".//description")
        if desc_elem is not None:
            desc_text = " ".join("".join(desc_elem.itertext()).split()).strip()
            # Do not use 'Re: ...' or empty descriptions as abstract
            if desc_text and not desc_text.lower().startswith("re: "):
                abstract_text = clean_abstract_xml(desc_text)

    # Extract paper authors (avoiding authors in citations/bib-references)
    authors: List[str] = []
    # Primary: author-group
    for ag in root.findall(".//author-group"):
        for au in ag.findall(".//author"):
            fn = au.find("given-name")
            sn = au.find("surname")
            given = " ".join(fn.itertext()).strip() if fn is not None else ""
            family = " ".join(sn.itertext()).strip() if sn is not None else ""
            name = f"{given} {family}".strip() if (given or family) else ""
            # Strip affiliation markers, footnote indicators, asterisks, digits
            name = re.sub(r"[\d\*\†\‡\^\#]+", "", name).strip()
            if name and name not in authors and len(name) > 1:
                authors.append(name)

    # Secondary: check dc:creator from coredata if author-group missing or smaller
    creator_authors: List[str] = []
    for c in root.findall(".//coredata/creator") + root.findall(".//creator"):
        c_text = " ".join(c.itertext()).strip()
        c_text = re.sub(r"[\d\*\†\‡\^\#]+", "", c_text).strip()
        if not c_text:
            continue
        if "," in c_text:
            parts = [p.strip() for p in c_text.split(",", 1)]
            formatted = f"{parts[1]} {parts[0]}".strip()
        else:
            formatted = c_text
        if formatted and formatted not in creator_authors and len(formatted) > 1:
            creator_authors.append(formatted)

    if len(creator_authors) > len(authors):
        authors = creator_authors
    elif not authors and creator_authors:
        authors = creator_authors

    # Extract publication metadata from coredata
    publication_meta: Dict[str, Any] = {}
    coredata = root.find(".//coredata")
    if coredata is not None:
        def _get_val(tag_name: str) -> Optional[str]:
            elem = coredata.find(tag_name)
            if elem is not None:
                val = " ".join("".join(elem.itertext()).split()).strip()
                return val if val else None
            return None

        publication_meta["journal"] = _get_val("publicationName")
        publication_meta["publisher"] = _get_val("publisher")
        publication_meta["volume"] = _get_val("volume")
        publication_meta["issue"] = _get_val("issueIdentifier")
        publication_meta["starting_page"] = _get_val("startingPage")
        publication_meta["ending_page"] = _get_val("endingPage")
        publication_meta["page_range"] = _get_val("pageRange")
        publication_meta["article_number"] = _get_val("articleNumber")
        publication_meta["doi"] = _get_val("doi")
        publication_meta["cover_date"] = _get_val("coverDate")
        publication_meta["cover_display_date"] = _get_val("coverDisplayDate")
        publication_meta["issn"] = _get_val("issn")

    sections: List[Dict[str, Any]] = []
    sec_idx = 1

    # Check for direct body paragraphs before or outside explicit sections (letters, commentaries)
    body = root.find(".//body")
    unscoped_paras: List[str] = []
    if body is not None:
        secs_container = body.find("sections")
        if secs_container is not None:
            for child in secs_container:
                if child.tag in ("para", "p"):
                    ptxt = " ".join("".join(child.itertext()).split()).strip()
                    if ptxt:
                        unscoped_paras.append(ptxt)
        for child in body:
            if child.tag in ("para", "p"):
                ptxt = " ".join("".join(child.itertext()).split()).strip()
                if ptxt and ptxt not in unscoped_paras:
                    unscoped_paras.append(ptxt)

    if unscoped_paras:
        m_paras = []
        for p_i, p_t in enumerate(unscoped_paras, 1):
            m_paras.append({
                "paragraph_id": f"p_{sec_idx}_{p_i}",
                "text": p_t,
                "section_id": f"sec_{sec_idx}",
                "source_span": None,
            })
        sections.append({
            "section_id": f"sec_{sec_idx}",
            "heading": "Main Text",
            "paragraphs": m_paras,
            "original_xml_reference": "unscoped_body",
            "extraction_status": "complete",
        })
        sec_idx += 1

    # Extract body sections (traversing both sections and sec)
    sections_found = root.findall(".//section")
    if not sections_found:
        sections_found = root.findall(".//sec")

    for sec in sections_found:
        title_elem = sec.find("section-title")
        if title_elem is None:
            title_elem = sec.find("title")
        heading = " ".join(title_elem.itertext()).strip() if title_elem is not None else f"Section {sec_idx}"

        # Skip section if it explicitly duplicates the abstract
        if re.match(r"(?i)^\s*(?:A\s*B\s*S\s*T\s*R\s*A\s*C\s*T|Abstract)\s*[:.]?\s*$", heading):
            continue

        paragraphs: List[Dict[str, Any]] = []
        p_idx = 1
        paras_found = sec.findall("para")
        if not paras_found:
            paras_found = sec.findall("p")
        if not paras_found:
            paras_found = sec.findall(".//para") + sec.findall(".//p")

        for p in paras_found:
            p_text = " ".join(p.itertext()).strip()
            if p_text:
                paragraphs.append({
                    "paragraph_id": f"p_{sec_idx}_{p_idx}",
                    "text": p_text,
                    "section_id": f"sec_{sec_idx}",
                    "source_span": None,
                })
                p_idx += 1

        if paragraphs:
            sections.append({
                "section_id": f"sec_{sec_idx}",
                "heading": heading,
                "paragraphs": paragraphs,
                "original_xml_reference": sec.attrib.get("id"),
                "extraction_status": "complete",
            })
            sec_idx += 1

    # Extract figures and tables
    figures_and_tables: List[Dict[str, Any]] = []
    fig_idx = 1
    figs_found = root.findall(".//figure")
    if not figs_found:
        figs_found = root.findall(".//fig")

    for fig in figs_found:
        caption_elem = fig.find("caption")
        caption = " ".join(caption_elem.itertext()).strip() if caption_elem is not None else ""
        label_elem = fig.find("label")
        label = " ".join(label_elem.itertext()).strip() if label_elem is not None else f"Figure {fig_idx}"
        figures_and_tables.append({
            "item_id": f"fig_{fig_idx}",
            "item_idx": fig_idx,
            "type": "figure",
            "label": label,
            "caption": caption,
            "extraction_status": "extracted" if caption else "partial",
        })
        fig_idx += 1

    tbl_idx = 1
    tables_found = root.findall(".//table")
    if not tables_found:
        tables_found = root.findall(".//table-wrap")

    for tbl in tables_found:
        caption_elem = tbl.find("caption")
        caption = " ".join(caption_elem.itertext()).strip() if caption_elem is not None else ""
        label_elem = tbl.find("label")
        label = " ".join(label_elem.itertext()).strip() if label_elem is not None else f"Table {tbl_idx}"
        figures_and_tables.append({
            "item_id": f"table_{tbl_idx}",
            "item_idx": tbl_idx,
            "type": "table",
            "label": label,
            "caption": caption,
            "extraction_status": "extracted" if caption else "partial",
        })
        tbl_idx += 1

    # Extract references
    references: List[Dict[str, Any]] = []
    ref_idx = 1
    refs_found = root.findall(".//bib-reference")
    if not refs_found:
        refs_found = root.findall(".//ref")

    for ref in refs_found:
        raw_ref = " ".join(ref.itertext()).strip()
        doi_elem = ref.find(".//doi")
        ref_doi = normalize_doi(" ".join(doi_elem.itertext()).strip()) if doi_elem is not None else None
        if raw_ref:
            references.append({
                "ref_idx": ref_idx,
                "raw_reference": raw_ref,
                "doi": ref_doi,
                "title": None,
                "authors": [],
                "year": None,
                "resolution_status": "resolved_doi" if ref_doi else "raw_only",
            })
            ref_idx += 1

    status = "complete" if (len(sections) >= 2 or (len(sections) >= 1 and abstract_text)) else ("partial" if sections else "unavailable")
    return {
        "abstract": abstract_text,
        "authors": authors,
        "sections": sections,
        "figures_and_tables": figures_and_tables,
        "references": references,
        "extraction_status": status,
    }


def build_sciencedirect_record(
    candidate: Dict[str, Any],
    doi_validation: Dict[str, Any],
    xml_content: Optional[str] = None,
    rights: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble a complete ScienceDirect Master Dataset record conforming to Phase 6 schema."""
    doi = normalize_doi(candidate.get("doi"))
    canonical_id = f"doi:{doi}"
    title = candidate.get("title", "").strip()
    abstract = clean_abstract_xml(candidate.get("abstract", "")).strip()
    year = int(candidate.get("year", 2024))
    authors = candidate.get("authors", [])

    # Full text processing
    if xml_content:
        parsed_xml = parse_elsevier_xml(xml_content)
        sections = parsed_xml["sections"]
        figures_and_tables = parsed_xml["figures_and_tables"]
        references = parsed_xml["references"]
        full_text_status = parsed_xml["extraction_status"]
        content_sha256 = hashlib.sha256(xml_content.encode("utf-8")).hexdigest()
        if not abstract and parsed_xml.get("abstract"):
            abstract = parsed_xml["abstract"]
        if (not authors or len(authors) <= 1) and parsed_xml.get("authors"):
            authors = parsed_xml["authors"]
    else:
        sections = []
        figures_and_tables = []
        references = []
        full_text_status = candidate.get("full_text_status", "access_denied")
        content_sha256 = None

    topic_group = candidate.get("topic_group")
    if not topic_group:
        topic_group, _ = score_and_classify_topic(title, abstract)

    publication = {
        "journal": candidate.get("journal", "ScienceDirect Journal"),
        "publisher": candidate.get("publisher", "Elsevier B.V."),
        "publication_date": candidate.get("publication_date"),
        "volume": candidate.get("volume"),
        "issue": candidate.get("issue"),
        "pages": candidate.get("pages"),
        "article_number": candidate.get("article_number"),
        "issn": candidate.get("issn"),
    }

    rights_record = {
        "open_access": candidate.get("is_oa", False),
        "license": candidate.get("license"),
        "full_text_entitlement": "authorized" if xml_content else "access_denied",
        "tdm_permission": candidate.get("tdm_permission", "conditional_subscription"),
        "ai_training_permission": candidate.get("ai_training_permission", "restricted_by_license"),
        "redistribution_permission": candidate.get("redistribution_permission", "no_commercial_redistribution"),
        "retention_policy": candidate.get("retention_policy", "academic_research_cache"),
    }
    if rights:
        rights_record.update(rights)

    provenance = {
        "source_url": candidate.get("url") or f"https://doi.org/{doi}",
        "api_endpoint": candidate.get("api_endpoint", "https://api.elsevier.com/content/article/doi"),
        "retrieved_at": candidate.get("retrieved_at") or doi_validation.get("validated_at"),
        "parser_version": PARSER_VERSION,
        "content_sha256": content_sha256,
    }

    quality = {
        "metadata_status": "complete" if title and abstract and year and authors else "partial",
        "full_text_status": full_text_status,
        "references_status": "extracted" if references else "none",
        "figures_status": "extracted" if figures_and_tables else "none",
        "qa_status": "not_annotated",
    }

    record = {
        "canonical_id": canonical_id,
        "source": "sciencedirect",
        "doi": doi,
        "doi_url": f"https://doi.org/{doi}",
        "pii": candidate.get("pii"),
        "title": title,
        "abstract": abstract,
        "year": year,
        "authors": [str(a) for a in authors],
        "keywords": candidate.get("keywords", []),
        "topic_group": topic_group,
        "publication": publication,
        "full_text": {
            "sections": sections,
            "extraction_status": full_text_status,
        },
        "qas": [],  # ScienceDirect papers do not furnish human-annotated QA benchmarks; synthetic QA disabled
        "figures_and_tables": figures_and_tables,
        "references": references,
        "doi_validation": doi_validation,
        "rights": rights_record,
        "provenance": provenance,
        "quality": quality,
    }

    return record
