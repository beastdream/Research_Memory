"""ScienceDirect and Crossref client for Elsevier article discovery, DOI verification, and content retrieval.

Handles authentication, entitlement verification, rate limits (HTTP 429), access restrictions (HTTP 401/403),
and DOI registration validation via Crossref and DOI registration agencies.
Secrets (API keys, institution tokens) are NEVER logged or written to disk.
"""
from __future__ import annotations

import datetime
import hashlib
import os
from pathlib import Path
import re
import socket
import time
import unicodedata
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import pymupdf
import requests
from dotenv import load_dotenv

DOI_PATTERN = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Za-z0-9]+$", re.IGNORECASE)
CROSSREF_API_URL = "https://api.crossref.org/works"
ELSEVIER_SEARCH_URL = "https://api.elsevier.com/content/search/sciencedirect"
ELSEVIER_ARTICLE_URL = "https://api.elsevier.com/content/article/doi/{doi}"


def mask_secret(value: Optional[str]) -> str:
    """Safely mask secret strings so no API keys or tokens appear in logs, CLI, or reports."""
    if not value or not str(value).strip():
        return "[NOT_CONFIGURED]"
    text = str(value).strip()
    if len(text) < 8:
        return "***"
    return f"{text[:3]}***{text[-3:]}"


def doi_to_safe_filename(doi: str) -> str:
    """Generate a filesystem-safe filename string from a DOI without collisions."""
    norm = normalize_doi(doi)
    if not norm:
        return "unknown_doi"
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", norm)
    return safe


def normalize_doi(value: Any) -> str:
    """Normalize DOI by stripping prefixes (https://doi.org/, doi:), trimming whitespace,

    preserving valid characters, and lowercasing for case-insensitive comparison.
    Does not restrict to 10.1016 prefix.
    """
    if not value:
        return ""
    text = str(value).strip()
    text = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", text, flags=re.IGNORECASE).strip()
    text = text.rstrip(".,;) ")
    lowered = text.lower()
    return lowered if DOI_PATTERN.match(lowered) else ""


def is_valid_doi_syntax(doi: str) -> bool:
    """Check if the string satisfies standard DOI syntax."""
    if not doi:
        return False
    return bool(DOI_PATTERN.match(doi.strip().lower()))


def _normalize_title(title: str) -> str:
    """Normalize title for fuzzy comparison by lowercasing, removing punctuation and diacritics."""
    if not title:
        return ""
    text = unicodedata.normalize("NFKD", title)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return " ".join(text.split())


def _title_similarity(t1: str, t2: str) -> float:
    """Compute token-level Jaccard similarity and containment ratio between two titles."""
    norm1 = _normalize_title(t1)
    norm2 = _normalize_title(t2)
    if not norm1 or not norm2:
        return 0.0
    if norm1 == norm2:
        return 1.0
    tokens1 = set(norm1.split())
    tokens2 = set(norm2.split())
    if not tokens1 or not tokens2:
        return 0.0
    intersection = tokens1.intersection(tokens2)
    union = tokens1.union(tokens2)
    jaccard = len(intersection) / len(union)
    containment = max(len(intersection) / len(tokens1), len(intersection) / len(tokens2))
    return max(jaccard, containment * 0.95)


def verify_doi_crossref(
    doi: str,
    candidate_title: str,
    candidate_authors: Optional[List[str]] = None,
    candidate_year: Optional[int] = None,
    timeout: int = 15,
) -> Dict[str, Any]:
    """Verify a DOI against Crossref REST API or secondary registries.

    Matches title, authors, and publication info.
    Returns structured validation record:
    - status: 'verified', 'mismatch', 'invalid', 'unverified'
    - registry: 'crossref' or other
    - title_match: bool
    - authors_match: bool or None
    - publication_match: bool or None
    - validated_at: ISO8601 string
    """
    normalized = normalize_doi(doi)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if not normalized or not is_valid_doi_syntax(normalized):
        return {
            "doi": doi,
            "doi_url": f"https://doi.org/{doi}" if doi else "",
            "doi_validation": {
                "status": "invalid",
                "registry": "crossref",
                "title_match": False,
                "authors_match": False,
                "publication_match": False,
                "validated_at": now_iso,
                "error": "Invalid DOI syntax",
            },
        }

    doi_url = f"https://doi.org/{normalized}"
    req_url = f"{CROSSREF_API_URL}/{quote(normalized, safe='')}"
    headers = {
        "User-Agent": "ResearchMemoryBot/1.0 (mailto:researchmemory@project.local; educational-science-eval)",
        "Accept": "application/json",
    }

    try:
        resp = requests.get(req_url, headers=headers, timeout=timeout)
        if resp.status_code == 404:
            # Check handle resolution as fallback
            handle_resp = requests.get(f"https://doi.org/api/handles/{quote(normalized, safe='')}", timeout=10)
            if handle_resp.status_code == 200:
                # Registered handle exists but not in Crossref; marked unverified pending registry metadata
                return {
                    "doi": normalized,
                    "doi_url": doi_url,
                    "doi_validation": {
                        "status": "unverified",
                        "registry": "doi_handle",
                        "title_match": False,
                        "authors_match": None,
                        "publication_match": None,
                        "validated_at": now_iso,
                        "error": "DOI handle exists but metadata record unavailable from Crossref",
                    },
                }
            return {
                "doi": normalized,
                "doi_url": doi_url,
                "doi_validation": {
                    "status": "invalid",
                    "registry": "crossref",
                    "title_match": False,
                    "authors_match": False,
                    "publication_match": False,
                    "validated_at": now_iso,
                    "error": "DOI not found in registry (HTTP 404)",
                },
            }

        if resp.status_code != 200:
            return {
                "doi": normalized,
                "doi_url": doi_url,
                "doi_validation": {
                    "status": "unverified",
                    "registry": "crossref",
                    "title_match": False,
                    "authors_match": None,
                    "publication_match": None,
                    "validated_at": now_iso,
                    "error": f"Crossref returned HTTP {resp.status_code}",
                },
            }

        item = resp.json().get("message", {})
        cr_titles = item.get("title", [])
        cr_title = cr_titles[0] if cr_titles else ""

        sim = _title_similarity(candidate_title, cr_title)
        title_match = sim >= 0.75 or _normalize_title(candidate_title) in _normalize_title(cr_title) or _normalize_title(cr_title) in _normalize_title(candidate_title)

        authors_match: Optional[bool] = None
        if candidate_authors:
            cr_authors = item.get("author", [])
            cr_family_names = [a.get("family", "").lower() for a in cr_authors if a.get("family")]
            cand_tokens = {w.lower() for a in candidate_authors for w in a.split()}
            overlap = any(fam in cand_tokens for fam in cr_family_names)
            authors_match = overlap if cr_family_names else True

        pub_match: Optional[bool] = None
        if candidate_year:
            date_parts = item.get("issued", {}).get("date-parts", [[]])[0]
            if date_parts:
                cr_year = date_parts[0]
                pub_match = abs(cr_year - candidate_year) <= 1

        if not title_match:
            status = "mismatch"
        else:
            status = "verified"

        return {
            "doi": normalized,
            "doi_url": doi_url,
            "doi_validation": {
                "status": status,
                "registry": "crossref",
                "title_match": title_match,
                "authors_match": authors_match,
                "publication_match": pub_match,
                "validated_at": now_iso,
            },
            "registry_metadata": {
                "crossref_title": cr_title,
                "publisher": item.get("publisher", ""),
                "container_title": (item.get("container-title") or [""])[0],
                "issued": item.get("issued"),
                "is_referenced_by_count": item.get("is-referenced-by-count", 0),
            },
        }

    except requests.exceptions.RequestException as exc:
        return {
            "doi": normalized,
            "doi_url": doi_url,
            "doi_validation": {
                "status": "unverified",
                "registry": "crossref",
                "title_match": False,
                "authors_match": None,
                "publication_match": None,
                "validated_at": now_iso,
                "error": f"Network exception: {type(exc).__name__}",
            },
        }


class ElsevierClient:
    """Official Elsevier API client for ScienceDirect Search and Article Retrieval.

    Reads API key and optional institutional token strictly from environment variables.
    Adheres to rate limits, quota limits, and TDM licenses.
    Never exposes secrets in logs or error messages.
    """

    def __init__(self, api_key: Optional[str] = None, inst_token: Optional[str] = None):
        load_dotenv()
        self.api_key = api_key if api_key is not None else os.environ.get("ELSEVIER_API_KEY", "").strip()
        self.inst_token = inst_token if inst_token is not None else os.environ.get("ELSEVIER_INST_TOKEN", "").strip()
        self.session = requests.Session()

    def _headers(self, accept: str = "application/json") -> Dict[str, str]:
        headers = {
            "Accept": accept,
            "User-Agent": "ResearchMemory/1.0 (Elsevier API Integration)",
        }
        if self.api_key:
            headers["X-ELS-APIKey"] = self.api_key
        if self.inst_token:
            headers["X-ELS-Insttoken"] = self.inst_token
        return headers

    def check_entitlement(self) -> Tuple[bool, str, str]:
        """Check whether Elsevier API key is present and entitled.

        Returns (is_entitled, status_code, message).
        Status values: 'authorized', 'missing_key', 'access_denied', 'quota_exceeded', 'error'
        """
        if not self.api_key:
            return (
                False,
                "missing_key",
                "ELSEVIER_API_KEY is not configured in environment or .env. "
                "Register at https://dev.elsevier.com/ to obtain an API key.",
            )

        try:
            # Lightweight probe
            resp = self.session.get(
                ELSEVIER_SEARCH_URL,
                params={"query": "test", "count": "1"},
                headers=self._headers(),
                timeout=15,
            )
            if resp.status_code == 200:
                return (True, "authorized", "Elsevier API key is valid and entitled for search.")
            elif resp.status_code in (401, 403):
                return (
                    False,
                    "access_denied",
                    f"Elsevier API returned HTTP {resp.status_code}: Access Denied or Entitlement Missing.",
                )
            elif resp.status_code == 429:
                return (
                    False,
                    "quota_exceeded",
                    "Elsevier API returned HTTP 429: Rate limit or quota exceeded.",
                )
            else:
                return (
                    False,
                    "error",
                    f"Elsevier API probe returned unexpected status HTTP {resp.status_code}.",
                )
        except requests.exceptions.RequestException as exc:
            return (False, "error", f"Elsevier API connection failed: {type(exc).__name__}")

    def search_sciencedirect(
        self,
        query: str,
        count: int = 25,
        start: int = 0,
        date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute ScienceDirect Search API GET request."""
        if not self.api_key:
            raise PermissionError("ELSEVIER_API_KEY is required to query ScienceDirect Search API")

        params: Dict[str, Any] = {
            "query": query,
            "count": min(count, 100),
            "start": start,
            "view": "STANDARD",
        }
        if date:
            params["date"] = date

        resp = self.session.get(ELSEVIER_SEARCH_URL, params=params, headers=self._headers(), timeout=30)
        self._handle_rate_limits(resp)
        resp.raise_for_status()
        return resp.json()

    def search_v2(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Execute ScienceDirect Search V2 API with PUT method per Elsevier documentation."""
        if not self.api_key:
            raise PermissionError("ELSEVIER_API_KEY is required to query ScienceDirect Search V2 API")

        headers = self._headers("application/json")
        headers["Content-Type"] = "application/json"
        resp = self.session.put(ELSEVIER_SEARCH_URL, json=payload, headers=headers, timeout=30)
        self._handle_rate_limits(resp)
        resp.raise_for_status()
        return resp.json()

    def get_article_xml(self, doi: str) -> Tuple[Optional[str], str]:
        """Retrieve full-text XML for an article via Article Retrieval API.

        Returns (xml_content, status).
        Status is one of: 'complete', 'access_denied', 'unavailable', 'quota_exceeded', 'error'
        """
        normalized = normalize_doi(doi)
        if not self.api_key:
            return None, "access_denied"

        url = ELSEVIER_ARTICLE_URL.format(doi=normalized)
        headers = self._headers(accept="text/xml, application/xml")
        try:
            resp = self.session.get(url, headers=headers, timeout=30)
            self._handle_rate_limits(resp)
            if resp.status_code == 200:
                return resp.text, "complete"
            elif resp.status_code in (401, 403):
                return None, "access_denied"
            elif resp.status_code == 429:
                return None, "quota_exceeded"
            elif resp.status_code == 404:
                return None, "unavailable"
            else:
                return None, f"http_{resp.status_code}"
        except requests.exceptions.RequestException:
            return None, "error"

    def get_article_pdf(
        self,
        doi: str,
        out_path: Optional[Path] = None,
        timeout: int = 45,
        save_path: Optional[Path] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Retrieve full-text PDF for an article via official Article Retrieval API.

        Validates Content-Type, magic bytes %PDF-, PyMuPDF page count > 0, unencrypted,
        calculates SHA256, and atomically writes .part -> .pdf.
        Never bypasses, scrapes, or exposes API keys.
        Returns (success: bool, status_code_or_str: str, metadata_dict: dict).
        """
        out_path = out_path or save_path
        normalized = normalize_doi(doi)
        if not normalized:
            return False, "invalid_doi_syntax", {"error": "Invalid DOI format"}
        if not self.api_key:
            return False, "missing_key", {"error": "ELSEVIER_API_KEY is not configured"}

        url = ELSEVIER_ARTICLE_URL.format(doi=normalized)
        headers = self._headers(accept="application/pdf")
        params = {"view": "FULL"}

        try:
            resp = self.session.get(url, headers=headers, params=params, stream=True, timeout=timeout)
            self._handle_rate_limits(resp)

            if resp.status_code == 401:
                return False, "access_denied", {"http_status": 401, "error": "Invalid API Key or unauthorized"}
            if resp.status_code == 403:
                return False, "access_denied", {"http_status": 403, "error": "No entitlement to PDF content for this DOI"}
            if resp.status_code == 404:
                return False, "not_found", {"http_status": 404, "error": "Article not found in Article Retrieval API"}
            if resp.status_code == 429:
                return False, "quota_exceeded", {"http_status": 429, "error": "Rate limit or quota exceeded"}
            if resp.status_code != 200:
                return False, f"http_{resp.status_code}", {"http_status": resp.status_code}

            content_type = resp.headers.get("Content-Type", "")
            if not content_type.lower().startswith("application/pdf"):
                return False, "invalid_content_type", {"content_type": content_type, "error": "Expected application/pdf"}

            # Stream download to target .part file if out_path supplied
            if out_path:
                out_path = Path(out_path)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                part_path = out_path.with_suffix(".pdf.part")
                hasher = hashlib.sha256()
                total_bytes = 0
                first_chunk = True

                with part_path.open("wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if not chunk:
                            continue
                        if first_chunk:
                            if not chunk.startswith(b"%PDF-"):
                                f.close()
                                part_path.unlink(missing_ok=True)
                                return False, "invalid_pdf_magic_bytes", {"error": "File does not start with %PDF- magic bytes"}
                            first_chunk = False
                        f.write(chunk)
                        hasher.update(chunk)
                        total_bytes += len(chunk)

                # Validate with PyMuPDF
                try:
                    pdf_bytes = part_path.read_bytes()
                    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
                        page_count = len(doc)
                        is_enc = doc.is_encrypted
                        if page_count <= 0:
                            part_path.unlink(missing_ok=True)
                            return False, "empty_pdf_pages", {"error": "PDF has 0 pages"}
                        if is_enc:
                            part_path.unlink(missing_ok=True)
                            return False, "encrypted_pdf", {"error": "PDF is encrypted and cannot be processed"}
                except Exception as exc:
                    try:
                        part_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return False, "corrupted_pdf", {"error": f"PyMuPDF failed to parse PDF: {type(exc).__name__}"}

                # Atomic rename
                part_path.replace(out_path)
                sha256_hex = hasher.hexdigest()
                return True, "pdf_downloaded", {
                    "doi": normalized,
                    "sha256": sha256_hex,
                    "size_bytes": total_bytes,
                    "page_count": page_count,
                    "pdf_path": str(out_path),
                    "content_type": content_type,
                }
            else:
                # In-memory validation
                raw_bytes = resp.content
                if not raw_bytes.startswith(b"%PDF-"):
                    return False, "invalid_pdf_magic_bytes", {"error": "Missing %PDF- header"}
                with pymupdf.open(stream=raw_bytes, filetype="pdf") as doc:
                    page_count = len(doc)
                    if page_count <= 0 or doc.is_encrypted:
                        return False, "invalid_pdf", {"page_count": page_count}
                sha256_hex = hashlib.sha256(raw_bytes).hexdigest()
                return True, "pdf_validated", {
                    "doi": normalized,
                    "sha256": sha256_hex,
                    "size_bytes": len(raw_bytes),
                    "page_count": page_count,
                    "content_type": content_type,
                }

        except requests.exceptions.RequestException as exc:
            return False, "network_error", {"error": f"Request failed: {type(exc).__name__}"}

    def doctor_preflight(
        self,
        smoke_doi: str = "10.1016/j.iswa.2026.200734",
        pdf_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Perform comprehensive preflight doctor verification on credentials, network, and live smoke DOI."""
        report: Dict[str, Any] = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "key_configured": bool(self.api_key),
            "key_masked": mask_secret(self.api_key),
            "inst_token_configured": bool(self.inst_token),
            "inst_token_masked": mask_secret(self.inst_token),
            "smoke_doi": smoke_doi,
            "dns_resolution": False,
            "pdf_entitlement": "pending",
            "xml_entitlement": "pending",
            "search_entitlement": "pending",
            "crossref_validation": "pending",
            "overall_status": "fail",
        }

        if not self.api_key:
            report["error"] = "Missing ELSEVIER_API_KEY in .env or environment"
            return report

        # 1. DNS Resolution
        try:
            ip = socket.gethostbyname("api.elsevier.com")
            report["dns_resolution"] = True
            report["api_host_ip"] = ip
        except Exception as exc:
            report["dns_resolution"] = False
            report["error"] = f"DNS resolution failed for api.elsevier.com: {exc}"
            return report

        # 2. Search API entitlement check
        try:
            s_res = self.search_sciencedirect("Large Language Models", count=1)
            total = s_res.get("search-results", {}).get("opensearch:totalResults", "0")
            report["search_entitlement"] = "authorized"
            report["search_sample_total"] = total
        except Exception as exc:
            report["search_entitlement"] = f"failed: {type(exc).__name__}"

        # 3. Full-text XML retrieval check on smoke DOI
        xml_content, xml_status = self.get_article_xml(smoke_doi)
        report["xml_entitlement"] = "authorized" if xml_content else xml_status
        report["xml_length"] = len(xml_content) if xml_content else 0

        # 4. Article Retrieval API PDF check on smoke DOI
        target_pdf = None
        if pdf_dir:
            pdf_dir = Path(pdf_dir)
            pdf_dir.mkdir(parents=True, exist_ok=True)
            safe_name = doi_to_safe_filename(smoke_doi) + ".pdf"
            target_pdf = pdf_dir / safe_name

        pdf_ok, pdf_status, pdf_meta = self.get_article_pdf(smoke_doi, out_path=target_pdf)
        report["pdf_entitlement"] = "authorized" if pdf_ok else pdf_status
        report["pdf_details"] = pdf_meta

        # 5. Crossref validation of smoke DOI
        cr_res = verify_doi_crossref(
            doi=smoke_doi,
            candidate_title="An intelligent RAG-based framework for evidence-grounded verification of LLM-generated CVE-ATT&CK mappings",
            candidate_year=2026,
        )
        report["crossref_validation"] = cr_res.get("doi_validation", {}).get("status")
        report["crossref_metadata"] = cr_res.get("registry_metadata")

        if pdf_ok and report["crossref_validation"] == "verified":
            report["overall_status"] = "pass"
        else:
            report["overall_status"] = "partial_or_fail"

        return report

    def _handle_rate_limits(self, resp: requests.Response) -> None:
        """Inspect rate limit headers and respect Retry-After."""
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            wait_sec = int(retry_after) if retry_after and retry_after.isdigit() else 5
            time.sleep(wait_sec)



class CrossrefScienceDirectDiscoveryClient:
    """Discovers ScienceDirect articles by querying Crossref for works published by Elsevier

    (doi prefix 10.1016 or publisher Elsevier BV) across designated topic keywords.
    Provides verified bibliographic metadata, license info, and publication details.
    """

    def __init__(self, user_agent: str = "ResearchMemoryBot/1.0 (mailto:researchmemory@project.local)"):
        self.session = requests.Session()
        self.headers = {
            "User-Agent": user_agent,
            "Accept": "application/json",
        }

    def search_topic(
        self,
        query: str,
        rows: int = 50,
        from_pub_date: str = "2018-01-01",
        until_pub_date: str = "2026-10-28",
    ) -> List[Dict[str, Any]]:
        """Query Crossref for Elsevier journal articles matching query within date range."""
        params = {
            "query": query,
            "filter": f"prefix:10.1016,type:journal-article,from-pub-date:{from_pub_date},until-pub-date:{until_pub_date}",
            "rows": min(rows, 100),
            "sort": "relevance",
            "select": "DOI,title,abstract,author,container-title,published-print,published-online,issued,volume,issue,page,article-number,ISSN,publisher,URL,link,license,subject",
        }
        try:
            resp = self.session.get(CROSSREF_API_URL, params=params, headers=self.headers, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("message", {}).get("items", [])
            return []
        except requests.exceptions.RequestException:
            return []
