"""Offline unit tests and mock integration tests for ScienceDirect pipeline.

Tests:
- DOI normalization and syntax validation
- Crossref DOI verification and title mismatch rejection
- Elsevier API client error handling (401, 403, 429, missing key)
- ScienceDirect XML parsing (sections, paragraphs, figures, tables, references)
- Topic quota allocation and classifier
- ScienceDirect Master Dataset schema invariants and jsonschema compliance
- PostgreSQL sciencedirect schema DDL, constraints and idempotent upserts (mocked DB)
- Strict separation from arXiv dataset
- License rights enforcement and rejection routing
"""
from __future__ import annotations

import copy
import json
from unittest.mock import MagicMock, patch

import pytest
from jsonschema import ValidationError

from app.ingestion.sciencedirect_client import (
    ElsevierClient,
    is_valid_doi_syntax,
    normalize_doi,
    verify_doi_crossref,
)
from app.ingestion.sciencedirect_parser import (
    TOPIC_GROUPS,
    build_sciencedirect_record,
    compute_quotas,
    parse_elsevier_xml,
    score_and_classify_topic,
)
from app.ingestion.sciencedirect_schema import validate_sciencedirect_master
from app.storage.sciencedirect_postgres import (
    SCHEMA_STATEMENTS,
    init_schema,
    upsert_sciencedirect_paper,
)


@pytest.fixture
def sample_valid_paper():
    return {
        "canonical_id": "doi:10.1016/j.artint.2024.103980",
        "source": "sciencedirect",
        "doi": "10.1016/j.artint.2024.103980",
        "doi_url": "https://doi.org/10.1016/j.artint.2024.103980",
        "pii": "S0004-3702(24)00045-8",
        "title": "Autonomous Agentic Memory for Long-Horizon Reasoning",
        "abstract": "We present an adaptive long-term memory architecture for large language model agents.",
        "year": 2024,
        "authors": ["Alice Researcher", "Bob Scientist"],
        "keywords": ["long-term memory", "autonomous agents", "llm"],
        "topic_group": "AI Agents and Long-Term Memory",
        "publication": {
            "journal": "Artificial Intelligence",
            "publisher": "Elsevier B.V.",
            "publication_date": "2024-05-15",
            "volume": "330",
            "issue": None,
            "pages": "103980",
            "article_number": "103980",
            "issn": "0004-3702",
        },
        "full_text": {
            "sections": [
                {
                    "section_id": "sec_1",
                    "heading": "Introduction",
                    "paragraphs": [
                        {
                            "paragraph_id": "p_1_1",
                            "text": "Long-term memory enables agents to preserve state over extended tasks.",
                            "section_id": "sec_1",
                            "source_span": None,
                        }
                    ],
                    "original_xml_reference": "sec1",
                    "extraction_status": "complete",
                }
            ],
            "extraction_status": "complete",
        },
        "qas": [],
        "figures_and_tables": [
            {
                "item_id": "fig_1",
                "item_idx": 1,
                "type": "figure",
                "label": "Figure 1",
                "caption": "Memory consolidation pipeline.",
                "extraction_status": "extracted",
            }
        ],
        "references": [
            {
                "ref_idx": 1,
                "raw_reference": "Vaswani, A. et al. (2017). Attention is all you need.",
                "doi": "10.5555/3295222.3295349",
                "title": "Attention is all you need",
                "authors": ["Ashish Vaswani"],
                "year": 2017,
                "resolution_status": "resolved_doi",
            }
        ],
        "doi_validation": {
            "status": "verified",
            "registry": "crossref",
            "title_match": True,
            "authors_match": True,
            "publication_match": True,
            "validated_at": "2026-10-28T07:00:00Z",
        },
        "rights": {
            "open_access": False,
            "license": "Elsevier User License",
            "full_text_entitlement": "authorized",
            "tdm_permission": "conditional_subscription",
            "ai_training_permission": "restricted_by_license",
            "redistribution_permission": "no_commercial_redistribution",
            "retention_policy": "academic_research_cache",
        },
        "provenance": {
            "source_url": "https://doi.org/10.1016/j.artint.2024.103980",
            "api_endpoint": "https://api.elsevier.com/content/article/doi",
            "retrieved_at": "2026-10-28T07:00:00Z",
            "parser_version": "sciencedirect-v1.0",
            "content_sha256": "abcdef1234567890",
        },
        "quality": {
            "metadata_status": "complete",
            "full_text_status": "complete",
            "references_status": "extracted",
            "figures_status": "extracted",
            "qa_status": "not_annotated",
        },
    }


def test_doi_normalization():
    # Various valid prefix formats
    assert normalize_doi("https://doi.org/10.1016/j.artint.2024.103980") == "10.1016/j.artint.2024.103980"
    assert normalize_doi("http://dx.doi.org/10.1016/J.ARTINT.2024.103980") == "10.1016/j.artint.2024.103980"
    assert normalize_doi("doi: 10.1016/j.artint.2024.103980") == "10.1016/j.artint.2024.103980"
    assert normalize_doi("  DOI:10.1016/S0004-3702(24)00045-8.  ") == "10.1016/s0004-3702(24)00045-8"
    # Non-10.1016 valid prefixes must be preserved
    assert normalize_doi("10.1051/article/202301") == "10.1051/article/202301"
    assert normalize_doi("10.1145/3442188.3445922") == "10.1145/3442188.3445922"
    # Invalid DOIs
    assert normalize_doi("not-a-doi") == ""
    assert normalize_doi("arxiv:2305.10250") == ""
    assert normalize_doi(None) == ""


def test_doi_syntax_validation():
    assert is_valid_doi_syntax("10.1016/j.artint.2024.103980") is True
    assert is_valid_doi_syntax("10.1000/182") is True
    assert is_valid_doi_syntax("invalid/doi") is False
    assert is_valid_doi_syntax("") is False


def test_crossref_doi_verification_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "message": {
            "title": ["Autonomous Agentic Memory for Long-Horizon Reasoning"],
            "author": [{"family": "Researcher", "given": "Alice"}],
            "issued": {"date-parts": [[2024, 5]]},
            "container-title": ["Artificial Intelligence"],
            "publisher": "Elsevier BV",
        }
    }
    with patch("requests.get", return_value=mock_resp):
        res = verify_doi_crossref(
            doi="10.1016/j.artint.2024.103980",
            candidate_title="Autonomous Agentic Memory for Long Horizon Reasoning",
            candidate_authors=["Alice Researcher"],
            candidate_year=2024,
        )
        assert res["doi_validation"]["status"] == "verified"
        assert res["doi_validation"]["title_match"] is True
        assert res["doi_validation"]["authors_match"] is True
        assert res["doi_validation"]["publication_match"] is True


def test_crossref_doi_verification_mismatch():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "message": {
            "title": ["Completely Unrelated Study on Agricultural Economics"],
            "author": [{"family": "Farmer"}],
            "issued": {"date-parts": [[2019]]},
        }
    }
    with patch("requests.get", return_value=mock_resp):
        res = verify_doi_crossref(
            doi="10.1016/j.artint.2024.103980",
            candidate_title="Autonomous Agentic Memory for Long Horizon Reasoning",
        )
        assert res["doi_validation"]["status"] == "mismatch"
        assert res["doi_validation"]["title_match"] is False


def test_crossref_doi_verification_404_invalid():
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    with patch("requests.get", return_value=mock_resp):
        res = verify_doi_crossref(
            doi="10.1016/non.existent.doi.99999",
            candidate_title="Some Paper",
        )
        assert res["doi_validation"]["status"] == "invalid"
        assert "not found" in res["doi_validation"]["error"].lower()


def test_elsevier_client_entitlement():
    # 1. Missing key
    client_no_key = ElsevierClient(api_key="")
    entitled, status, msg = client_no_key.check_entitlement()
    assert entitled is False
    assert status == "missing_key"

    # 2. 401 Authentication Error
    client_invalid_key = ElsevierClient(api_key="bad-key")
    mock_401 = MagicMock(status_code=401)
    with patch.object(client_invalid_key.session, "get", return_value=mock_401):
        entitled, status, _ = client_invalid_key.check_entitlement()
        assert entitled is False
        assert status == "access_denied"

    # 3. 429 Rate Limit
    mock_429 = MagicMock(status_code=429, headers={"Retry-After": "1"})
    with patch.object(client_invalid_key.session, "get", return_value=mock_429):
        entitled, status, _ = client_invalid_key.check_entitlement()
        assert entitled is False
        assert status == "quota_exceeded"

    # 4. 200 Authorized
    mock_200 = MagicMock(status_code=200)
    with patch.object(client_invalid_key.session, "get", return_value=mock_200):
        entitled, status, _ = client_invalid_key.check_entitlement()
        assert entitled is True
        assert status == "authorized"


def test_quota_computation_across_targets():
    # 150 base
    q150 = compute_quotas(150)
    assert sum(q150.values()) == 150
    assert q150["Large Language Models"] == 30
    assert q150["RAG and Information Retrieval"] == 35
    assert q150["AI Agents and Long-Term Memory"] == 35
    assert q150["Hallucination and Faithfulness"] == 25
    assert q150["Scientific NLP and QA"] == 25

    # 100 scaled
    q100 = compute_quotas(100)
    assert sum(q100.values()) == 100

    # 200 scaled
    q200 = compute_quotas(200)
    assert sum(q200.values()) == 200

    with pytest.raises(ValueError):
        compute_quotas(90)
    with pytest.raises(ValueError):
        compute_quotas(250)


def test_topic_classification():
    topic, score = score_and_classify_topic(
        title="Dense Retrieval and Reranking for Retrieval-Augmented Generation",
        abstract="We evaluate vector search and dense passage retrieval.",
    )
    assert topic == "RAG and Information Retrieval"
    assert score > 0

    topic2, score2 = score_and_classify_topic(
        title="Mitigating Hallucination and Ensuring Faithfulness in Foundation Models",
        abstract="Factual consistency and AI verification are essential.",
    )
    assert topic2 == "Hallucination and Faithfulness"
    assert score2 > 0


def test_scientific_xml_parser():
    xml_data = """
    <article>
        <abstract>
            <ce:para>This is the article abstract on agent memory.</ce:para>
        </abstract>
        <section id="sec01">
            <section-title>1. Introduction</section-title>
            <para>Autonomous agents require persistent long-term storage.</para>
            <para>Memory consolidation is inspired by neuroscience.</para>
        </section>
        <figure id="fig01">
            <label>Figure 1</label>
            <caption>Architecture flow diagram.</caption>
        </figure>
        <table id="tbl01">
            <label>Table 1</label>
            <caption>Benchmark comparison.</caption>
        </table>
        <bib-reference id="ref01">
            <ce:para>Devlin et al. BERT. <ce:doi>10.18653/v1/N19-1423</ce:doi></ce:para>
        </bib-reference>
    </article>
    """
    res = parse_elsevier_xml(xml_data)
    assert res["extraction_status"] == "complete"
    assert res["abstract"] == "This is the article abstract on agent memory."
    assert len(res["sections"]) == 1
    assert res["sections"][0]["heading"] == "1. Introduction"
    assert len(res["sections"][0]["paragraphs"]) == 2
    assert len(res["figures_and_tables"]) == 2
    assert res["figures_and_tables"][0]["type"] == "figure"
    assert res["figures_and_tables"][1]["type"] == "table"
    assert len(res["references"]) == 1
    assert res["references"][0]["doi"] == "10.18653/v1/n19-1423"


def test_master_schema_validation_success(sample_valid_paper):
    validated = validate_sciencedirect_master(sample_valid_paper)
    assert validated["canonical_id"] == "doi:10.1016/j.artint.2024.103980"
    assert validated["source"] == "sciencedirect"


def test_master_schema_validation_failures(sample_valid_paper):
    # 1. Invalid canonical id
    bad_id = copy.deepcopy(sample_valid_paper)
    bad_id["canonical_id"] = "doi:10.1016/other.mismatched.doi"
    with pytest.raises(ValueError, match="Canonical ID mismatch"):
        validate_sciencedirect_master(bad_id)

    # 1b. Non-DOI prefix rejected by schema
    bad_prefix = copy.deepcopy(sample_valid_paper)
    bad_prefix["canonical_id"] = "arxiv:2401.12345"
    with pytest.raises(ValidationError):
        validate_sciencedirect_master(bad_prefix)

    # 2. Unverified DOI status
    unverified = copy.deepcopy(sample_valid_paper)
    unverified["doi_validation"]["status"] = "unverified"
    with pytest.raises((ValueError, ValidationError)):
        validate_sciencedirect_master(unverified)

    # 3. DOI title mismatch
    mismatch = copy.deepcopy(sample_valid_paper)
    mismatch["doi_validation"]["title_match"] = False
    with pytest.raises(ValueError, match="failed title verification"):
        validate_sciencedirect_master(mismatch)

    # 4. Future year
    future = copy.deepcopy(sample_valid_paper)
    future["year"] = 2030
    with pytest.raises((ValueError, ValidationError)):
        validate_sciencedirect_master(future)

    # 5. Wrong source
    wrong_src = copy.deepcopy(sample_valid_paper)
    wrong_src["source"] = "arxiv"
    with pytest.raises((ValueError, ValidationError)):
        validate_sciencedirect_master(wrong_src)


def test_postgres_schema_initialization_statements():
    # Verify DDL contains isolated schema and check constraint
    ddl = "\n".join(SCHEMA_STATEMENTS)
    assert "CREATE SCHEMA IF NOT EXISTS sciencedirect;" in ddl
    assert "CREATE TABLE IF NOT EXISTS sciencedirect.papers" in ddl
    assert "CONSTRAINT check_canonical_doi CHECK (canonical_id LIKE 'doi:%')" in ddl
    assert "CONSTRAINT check_doi_verified CHECK ((doi_validation->>'status') = 'verified')" in ddl
    assert "sciencedirect.sections" in ddl
    assert "sciencedirect.references" in ddl
    assert "sciencedirect.figures_and_tables" in ddl
    assert "sciencedirect.ingestion_runs" in ddl


def test_mock_postgres_idempotent_upsert(sample_valid_paper):
    mock_db = MagicMock()
    # First call: existing record is None -> inserted
    mock_db.execute.return_value.fetchone.return_value = None

    status = upsert_sciencedirect_paper(mock_db, sample_valid_paper)
    assert status == "inserted"

    # Second call with identical record -> unchanged
    mock_db.execute.return_value.fetchone.return_value = (sample_valid_paper, True)
    status2 = upsert_sciencedirect_paper(mock_db, sample_valid_paper)
    assert status2 == "unchanged"


def test_dataset_separation_from_arxiv(sample_valid_paper):
    # Verify sciencedirect records cannot be inserted into arxiv canonical_id space
    assert sample_valid_paper["source"] == "sciencedirect"
    assert sample_valid_paper["canonical_id"].startswith("doi:")
    assert not sample_valid_paper["canonical_id"].startswith("arxiv:")
    # arXiv master validator must reject ScienceDirect canonical_id
    from app.ingestion.master_schema import validate_master
    with pytest.raises(Exception):
        validate_master(sample_valid_paper)


def test_doctor_preflight_missing_key():
    client = ElsevierClient(api_key="")
    report = client.doctor_preflight(smoke_doi="10.1016/j.iswa.2026.200734")
    assert report["overall_status"] == "fail"
    assert report["key_configured"] is False
    assert report["key_masked"] == "[NOT_CONFIGURED]"
    assert "Missing ELSEVIER_API_KEY" in report["error"]


def test_doctor_preflight_mocked_success(tmp_path):
    client = ElsevierClient(api_key="1234567890abcdef1234567890abcdef")
    # Mock network calls
    with patch("socket.gethostbyname", return_value="104.17.39.96"), \
         patch.object(client, "get_article_pdf") as mock_pdf, \
         patch.object(client, "get_article_xml") as mock_xml, \
         patch.object(client, "search_sciencedirect") as mock_search, \
         patch("app.ingestion.sciencedirect_client.verify_doi_crossref") as mock_crossref:

        mock_pdf.return_value = (
            True,
            "pdf_downloaded",
            {
                "doi": "10.1016/j.iswa.2026.200734",
                "sha256": "abcdef1234567890",
                "size_bytes": 1024,
                "page_count": 5,
                "pdf_path": str(tmp_path / "test.pdf"),
                "content_type": "application/pdf",
            },
        )
        mock_xml.return_value = ("<article><title>Test</title></article>", "complete")
        mock_search.return_value = {"search-results": {"opensearch:totalResults": "100"}}
        mock_crossref.return_value = {
            "doi_validation": {"status": "verified", "title_match": True},
            "registry_metadata": {"title": "Test Paper", "publisher": "Elsevier BV"},
        }

        report = client.doctor_preflight(smoke_doi="10.1016/j.iswa.2026.200734", pdf_dir=tmp_path)
        assert report["overall_status"] == "pass"
        assert report["key_configured"] is True
        assert report["key_masked"] == "123***def"
        assert report["pdf_entitlement"] == "authorized"
        assert report["xml_entitlement"] == "authorized"
        assert report["search_entitlement"] == "authorized"
        assert report["crossref_validation"] == "verified"


def test_pdf_download_html_disguised_error(tmp_path):
    client = ElsevierClient(api_key="test_key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"Content-Type": "text/html; charset=utf-8"}

    with patch.object(client.session, "get", return_value=mock_resp):
        ok, status, details = client.get_article_pdf("10.1016/j.artint.2024.103980", out_path=tmp_path / "test.pdf")
        assert ok is False
        assert status == "invalid_content_type"
        assert "Expected application/pdf" in details["error"]


def test_pdf_download_corrupt_zero_pages(tmp_path):
    client = ElsevierClient(api_key="test_key")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"Content-Type": "application/pdf"}
    # %PDF header but corrupted trailing data
    mock_resp.iter_content.return_value = [b"%PDF-1.4\ncorrupted trailing data garbage"]

    with patch.object(client.session, "get", return_value=mock_resp):
        ok, status, details = client.get_article_pdf("10.1016/j.artint.2024.103980", out_path=tmp_path / "test.pdf")
        assert ok is False
        assert status == "corrupted_pdf"


@pytest.mark.parametrize("status_code,expected_status", [
    (401, "access_denied"),
    (403, "access_denied"),
    (404, "not_found"),
    (429, "quota_exceeded"),
    (500, "http_500"),
])
def test_pdf_download_http_errors(tmp_path, status_code, expected_status):
    client = ElsevierClient(api_key="test_key")
    mock_resp = MagicMock()
    mock_resp.status_code = status_code

    with patch.object(client.session, "get", return_value=mock_resp):
        ok, status, details = client.get_article_pdf("10.1016/j.artint.2024.103980", out_path=tmp_path / "test.pdf")
        assert ok is False
        assert status == expected_status



def test_enrichment_v2_structure(sample_valid_paper, tmp_path):
    from app.ingestion.sciencedirect_enrichment import enrich_sciencedirect_record

    # Create dummy valid PDF using pymupdf
    import pymupdf
    pdf_file = tmp_path / "dummy.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 72), "1. Introduction\nLarge language models require long-term memory.")
    page.insert_text((50, 150), "Figure 1: Architectural diagram of the agent memory system.")
    page.insert_text((50, 300), "References\n[1] Vaswani et al. Attention is all you need. 2017.")
    doc.save(str(pdf_file))
    doc.close()

    enriched = enrich_sciencedirect_record(sample_valid_paper, pdf_path=pdf_file)

    # Invariants conforming to memorybank_enriched_v2.json
    assert enriched["schema_version"] == "2.1"
    assert enriched["canonical_id"] == "doi:10.1016/j.artint.2024.103980"
    assert enriched["source"] == "sciencedirect"
    assert enriched["split"] == "corpus"
    assert enriched["identifiers"]["doi"] == "10.1016/j.artint.2024.103980"
    assert enriched["publication"]["publication_status"] == "published"
    assert enriched["qas"] == []  # Mandatory invariant: empty list, no fabricated annotations
    assert enriched["quality"]["pdf_valid"] is True
    assert enriched["quality"]["qa_status"] == "not_annotated"
    assert enriched["provenance"]["pdf_page_count"] == 1
    assert len(enriched["full_text"]["sections"]) > 0
    assert enriched["rights"]["open_access"] is False
    assert enriched["rights"]["full_text_entitlement"] == "authorized"


def test_upsert_sciencedirect_enriched(sample_valid_paper):
    from app.ingestion.sciencedirect_enrichment import enrich_sciencedirect_record
    from app.storage.sciencedirect_postgres import upsert_sciencedirect_enriched

    enriched = enrich_sciencedirect_record(sample_valid_paper)
    mock_db = MagicMock()

    # Case 1: First insert -> inserted
    mock_db.execute.return_value.fetchone.return_value = None
    st1 = upsert_sciencedirect_enriched(mock_db, enriched)
    assert st1 == "inserted"

    # Case 2: Identical record -> unchanged
    mock_db.execute.return_value.fetchone.return_value = (enriched,)
    st2 = upsert_sciencedirect_enriched(mock_db, enriched)
    assert st2 == "unchanged"

    # Case 3: Modified record -> updated
    mock_db.execute.return_value.fetchone.return_value = ({"different": True},)
    st3 = upsert_sciencedirect_enriched(mock_db, enriched)
    assert st3 == "updated"


# =========================================================================
# Repair Pipeline Unit Tests
# =========================================================================


def test_repair_extract_authors_from_crossref():
    from scripts.repair_sciencedirect import extract_authors_from_crossref

    msg = {
        "author": [
            {"given": "Jane", "family": "Doe", "sequence": "first"},
            {"given": "John", "family": "Smith*", "sequence": "additional"},
            {"name": "Research Consortium", "sequence": "additional"},
            {"given": "Jane", "family": "Doe", "sequence": "additional"},  # duplicate
        ]
    }
    authors = extract_authors_from_crossref(msg)
    assert authors == ["Jane Doe", "John Smith", "Research Consortium"]


def test_repair_extract_abstract_from_crossref():
    from scripts.repair_sciencedirect import extract_abstract_from_crossref

    # Case 1: JATS tags
    msg = {
        "abstract": "<jats:title>Abstract</jats:title><jats:p>This study presents a novel retrieval-augmented generation framework.</jats:p>"
    }
    clean = extract_abstract_from_crossref(msg)
    assert "This study presents a novel retrieval-augmented" in clean
    assert "<jats:" not in clean

    # Case 2: Leading Abstract prefix
    msg2 = {"abstract": "Abstract: Comprehensive analysis of agent long term memory."}
    clean2 = extract_abstract_from_crossref(msg2)
    assert clean2 == "Comprehensive analysis of agent long term memory."

    # Case 3: Empty abstract
    assert extract_abstract_from_crossref({}) == ""


def test_repair_pdf_info(tmp_path):
    from scripts.repair_sciencedirect import get_pdf_info
    import pymupdf

    # Case 1: Non-existent file
    info_missing = get_pdf_info(tmp_path / "does_not_exist.pdf")
    assert info_missing["exists"] is False
    assert info_missing["valid"] is False

    # Case 2: Valid multi-page PDF
    pdf_p = tmp_path / "test_doc.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    doc.save(str(pdf_p))
    doc.close()

    info_valid = get_pdf_info(pdf_p)
    assert info_valid["exists"] is True
    assert info_valid["valid"] is True
    assert info_valid["page_count"] == 2
    assert info_valid["sha256"] is not None


def test_repair_clean_abstract_xml():
    from app.ingestion.sciencedirect_parser import clean_abstract_xml

    xml_snippet = "<ce:abstract><ce:section-title>Abstract</ce:section-title><ce:simple-para>Deep neural models require calibrated uncertainty.</ce:simple-para></ce:abstract>"
    cleaned = clean_abstract_xml(xml_snippet)
    assert "Deep neural models require calibrated uncertainty." in cleaned
    assert "<ce:" not in cleaned
    assert "Abstract" not in cleaned.split("Deep")[0]  # section-title stripped


def test_repair_record_logic(sample_valid_paper):
    from scripts.repair_sciencedirect import repair_record
    from app.ingestion.sciencedirect_schema import validate_sciencedirect_master

    # Set up paper with empty abstract and single author
    paper = dict(sample_valid_paper)
    paper["abstract"] = ""
    paper["authors"] = ["Only One"]
    paper["text"] = "Old title"

    new_master, new_enriched, diff = repair_record(paper, client=None)

    # Master schema validity check
    validate_sciencedirect_master(new_master)
    assert new_master["canonical_id"] == paper["canonical_id"]
    assert new_master["text"].startswith(paper["title"])
    assert isinstance(new_master["authors"], list)
    assert isinstance(new_master["abstract"], str)


def test_regression_doi_a_acags_6_authors():
    from scripts.repair_sciencedirect import MASTER_PATH, repair_record
    from app.ingestion.sciencedirect_client import normalize_doi

    target_doi = "10.1016/j.acags.2026.100420"
    lines = [json.loads(l) for l in MASTER_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    rec = next(r for r in lines if normalize_doi(r.get("doi")) == target_doi)

    new_m, new_e, diff = repair_record(rec)

    expected_authors = [
        "Bohan Chen",
        "Jialong Lai",
        "Ting Huang",
        "Zhengyang Zou",
        "Yong Zhao",
        "David Carlos Fernandez-Remolar",
    ]
    assert new_m["authors"] == expected_authors
    assert new_e["authors"] == expected_authors
    assert new_m["year"] == 2026
    assert new_e["year"] == 2026
    assert diff["authors_source"] == "publisher_xml"


def test_regression_doi_e_aei_2_authors_and_sections():
    from scripts.repair_sciencedirect import MASTER_PATH, repair_record
    from app.ingestion.sciencedirect_client import normalize_doi

    target_doi = "10.1016/j.aei.2026.105154"
    lines = [json.loads(l) for l in MASTER_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    rec = next(r for r in lines if normalize_doi(r.get("doi")) == target_doi)

    new_m, new_e, diff = repair_record(rec)

    assert new_m["authors"] == ["Lu Yang", "Yuliang Li"]
    assert new_e["authors"] == ["Lu Yang", "Yuliang Li"]
    assert diff["pdf_pages"] == 1
    assert diff["sections_count"] > 25
    assert diff["full_text_status"] == "complete"
    assert new_m["has_full_text"] is True


def test_regression_doi_b_acra_no_abstract():
    from scripts.repair_sciencedirect import MASTER_PATH, repair_record
    from app.ingestion.sciencedirect_client import normalize_doi
    from app.ingestion.sciencedirect_schema import validate_sciencedirect_master

    target_doi = "10.1016/j.acra.2024.08.012"
    lines = [json.loads(l) for l in MASTER_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    rec = next(r for r in lines if normalize_doi(r.get("doi")) == target_doi)

    new_m, new_e, diff = repair_record(rec)

    assert new_m["abstract"] == ""
    assert new_e["abstract"] == ""
    assert diff["abstract_source"] == "no_abstract_published"
    assert new_m["year"] == 2024
    validate_sciencedirect_master(new_m)


def test_regression_doi_c_unicode_preserved():
    from scripts.repair_sciencedirect import MASTER_PATH, repair_record
    from app.ingestion.sciencedirect_client import normalize_doi

    target_doi = "10.1016/j.aei.2024.103007"
    lines = [json.loads(l) for l in MASTER_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    rec = next(r for r in lines if normalize_doi(r.get("doi")) == target_doi)

    new_m, new_e, diff = repair_record(rec)

    assert "José Antonio Heredia Álvaro" in new_m["authors"]
    assert "Javier González Barreda" in new_m["authors"]
    assert len(new_m["abstract"]) > 500


def test_regression_doi_d_1page_pdf_xml_fulltext():
    from scripts.repair_sciencedirect import MASTER_PATH, repair_record
    from app.ingestion.sciencedirect_client import normalize_doi

    target_doi = "10.1016/j.apenergy.2026.129012"
    lines = [json.loads(l) for l in MASTER_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    rec = next(r for r in lines if normalize_doi(r.get("doi")) == target_doi)

    new_m, new_e, diff = repair_record(rec)

    assert diff["pdf_pages"] == 1
    assert diff["full_text_status"] == "complete"
    assert diff["sections_count"] >= 40
    assert new_m["has_full_text"] is True
    assert new_m["year"] == 2026


def test_deduplication_build_canonical_text():
    from app.ingestion.sciencedirect_enrichment import build_canonical_text

    title = "Advanced Scientific RAG"
    abstract = "We present an evidence-grounded long-term memory system."
    sections = [
        {
            "heading": "Abstract",
            "paragraphs": [{"text": "We present an evidence-grounded long-term memory system."}],
        },
        {
            "heading": "1. Introduction",
            "paragraphs": [{"text": "Scientific RAG requires precision and grounded evidence."}],
        },
        {
            "heading": "2. Methods",
            "paragraphs": [{"text": "We propose a dual-tower indexing structure."}],
        },
    ]

    canonical = build_canonical_text(title, abstract, sections)

    # Abstract must only appear once in the canonical text
    assert canonical.count("We present an evidence-grounded long-term memory system.") == 1
    assert canonical.count("Abstract\nWe present") == 1
    assert "1. Introduction\nScientific RAG" in canonical
    assert "2. Methods\nWe propose" in canonical


def test_mandatory_top_17_keys_ordering():
    from app.ingestion.sciencedirect_enrichment import (
        MANDATORY_TOP_KEYS,
        MANDATORY_SECONDARY_KEYS,
        order_enriched_record,
    )

    dummy = {
        "rights": {"open_access": True},
        "title": "Test Title",
        "schema_version": "2.1",
        "canonical_id": "doi:10.1016/test",
        "source": "sciencedirect",
        "source_id": "10.1016/test",
        "split": "corpus",
        "abstract": "Test abstract",
        "text": "Full text",
        "year": 2026,
        "url": "https://doi.org/10.1016/test",
        "doi": "10.1016/test",
        "arxiv_id": None,
        "authors": ["Author One"],
        "categories": ["LLM"],
        "metadata": {},
        "identifiers": {},
        "publication": {},
        "full_text": {},
        "sections": [],
        "figures_and_tables": [],
        "references": [],
        "qas": [],
        "citation_markers": [],
        "equations": [],
        "research_metadata": {},
        "provenance": {},
        "quality": {},
        "has_full_text": True,
        "extraction_quality": {},
    }

    ordered = order_enriched_record(dummy)
    ordered_keys = list(ordered.keys())

    assert ordered_keys[:17] == MANDATORY_TOP_KEYS
    assert ordered_keys[17:30] == MANDATORY_SECONDARY_KEYS


def test_idempotent_repair_no_degradation(sample_valid_paper):
    from scripts.repair_sciencedirect import repair_record

    paper = dict(sample_valid_paper)
    paper["year"] = 2024
    paper["authors"] = ["Author A", "Author B", "Author C"]
    paper["abstract"] = "Verified abstract with complete details."
    paper["text"] = "Verified canonical text."

    # First repair
    m1, e1, d1 = repair_record(paper, client=None)
    assert len(m1["authors"]) >= 3
    assert m1["year"] == 2024

    # Second repair with deficient candidate source: Ensure no degradation
    m2, e2, d2 = repair_record(m1, client=None)
    assert m2["authors"] == m1["authors"]
    assert m2["year"] == m1["year"]
    assert m2["abstract"] == m1["abstract"]
    assert m2["text"] == m1["text"]
    assert m2["has_full_text"] == m1["has_full_text"]


def test_unscoped_body_paragraphs_extraction():
    from app.ingestion.sciencedirect_parser import parse_elsevier_xml

    xml_snippet = """
    <article>
        <abstract><para>Brief abstract.</para></abstract>
        <body>
            <para>This is an unscoped body paragraph in a correspondence paper.</para>
            <para>This is a second unscoped body paragraph.</para>
        </body>
    </article>
    """
    res = parse_elsevier_xml(xml_snippet)
    assert res["abstract"] == "Brief abstract."
    assert len(res["sections"]) >= 1
    assert res["sections"][0]["heading"] == "Main Text"
    assert len(res["sections"][0]["paragraphs"]) == 2



