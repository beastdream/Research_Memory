"""Sequential official arXiv Atom API and bounded, atomic PDF downloads."""
from __future__ import annotations

import hashlib
import re
import time
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
import pymupdf

API = 'https://export.arxiv.org/api/query'
NS = {'a': 'http://www.w3.org/2005/Atom', 'x': 'http://arxiv.org/schemas/atom'}
ID_RE = re.compile(r'^(\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z]{2})?/\d{7})(v[1-9]\d*)?$', re.I)


def arxiv_identity(value):
    value = str(value or '').strip()
    value = re.sub(r'^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/', '', value, flags=re.I)
    value = re.sub(r'^arxiv:', '', value, flags=re.I).removesuffix('.pdf')
    match = ID_RE.fullmatch(value)
    if not match:
        raise ValueError('Invalid arXiv identifier')
    return match[1].lower(), match[2] or None


def normalize_doi(value):
    value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', str(value or '').strip(), flags=re.I).lower()
    return value if re.fullmatch(r'10\.\d{4,9}/\S+', value) else ''


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def parse_atom(content):
    root = ET.fromstring(content)
    rows = []
    for entry in root.findall('a:entry', NS):
        def get(name):
            return ' '.join((entry.findtext(name, default='', namespaces=NS) or '').split())
        base, version = arxiv_identity(get('a:id'))
        links = entry.findall('a:link', NS)
        rows.append(dict(
            arxiv_id=base, arxiv_version=version, title=get('a:title'), abstract=get('a:summary'),
            authors=[a.findtext('a:name', default='', namespaces=NS) for a in entry.findall('a:author', NS)],
            published=get('a:published') or None, updated=get('a:updated') or None,
            categories=[c.get('term') for c in entry.findall('a:category', NS)],
            primary_category=(entry.find('x:primary_category', NS).get('term') if entry.find('x:primary_category', NS) is not None else None),
            doi=normalize_doi(get('x:doi')) or None, journal_reference=get('x:journal_ref') or None,
            abstract_url=next((l.get('href') for l in links if l.get('rel') == 'alternate'), None),
            pdf_url=next((l.get('href').replace('http://', 'https://', 1) for l in links if l.get('title') == 'pdf' or l.get('type') == 'application/pdf'), None),
            license=get('x:license') or None,
        ))
    return rows


def valid_pdf(path):
    try:
        with Path(path).open('rb') as stream:
            if stream.read(5) != b'%PDF-':
                return False
        with pymupdf.open(path) as doc:
            return doc.is_pdf and not doc.needs_pass and len(doc) > 0
    except (OSError, RuntimeError, ValueError):
        return False


class ArxivClient:
    # Shared process lock prevents overlapping requests across client instances.
    _lock = threading.Lock()
    _last_request = 0.0

    def __init__(self, session=None, retries=3, delay=3.1):
        self.session = session or requests.Session()
        self.session.headers.update({'User-Agent': 'ResearchMemory/2.0 (research data preparation)'})
        self.retries = retries
        self.delay = max(3.0, delay)

    def _pause(self):
        time.sleep(max(0, self.delay - (time.monotonic() - ArxivClient._last_request)))
        ArxivClient._last_request = time.monotonic()

    def request(self, url, consume, **kwargs):
        with self._lock:
            for attempt in range(self.retries + 1):
                self._pause()
                wait = 2 ** attempt * 3
                try:
                    with self.session.get(url, timeout=(20, 60), **kwargs) as response:
                        if response.status_code in (429, 503):
                            retry = response.headers.get('Retry-After', '')
                            try:
                                wait = max(wait, float(retry))
                            except ValueError:
                                try:
                                    wait = max(wait, (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds())
                                except (ValueError, TypeError):
                                    pass
                        response.raise_for_status()
                        return consume(response)
                except requests.RequestException as exc:
                    if attempt == self.retries or (isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code not in (429, 500, 502, 503, 504)):
                        status = exc.response.status_code if exc.response is not None else 'transport'
                        raise RuntimeError(f'arXiv request failed ({status}); retry later') from None
                    time.sleep(wait)

    def query(self, search_query='', start=0, max_results=50, sortBy='relevance', sortOrder='descending', id_list=None):
        params = dict(search_query=search_query, start=start, max_results=max_results, sortBy=sortBy, sortOrder=sortOrder)
        if id_list:
            params['id_list'] = ','.join(id_list)
        return self.request(API, lambda r: parse_atom(r.content), params=params)

    def download(self, url, dest, max_bytes, budget):
        dest = Path(dest)
        if dest.exists() and valid_pdf(dest):
            return sha256(dest)
        if dest.exists():
            raise ValueError('Existing invalid PDF retained for manual inspection')
        # Only official arXiv PDF endpoints, including when a CSV is edited.
        if not re.fullmatch(r'https://(?:export\.)?arxiv\.org/pdf/[\w./-]+', url or ''):
            raise ValueError('Expected official arXiv PDF URL')
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix('.pdf.part')

        def consume(response):
            size = 0
            with part.open('wb') as stream:
                for chunk in response.iter_content(65536):
                    if not chunk:
                        continue
                    size += len(chunk)
                    budget[0] -= len(chunk)  # Retries also consume the invocation's transfer budget.
                    if size > max_bytes or budget[0] < 0:
                        raise ValueError('PDF or total download budget exceeded')
                    stream.write(chunk)
            if not valid_pdf(part):
                raise ValueError('Downloaded response is not a readable PDF')
            part.replace(dest)
            return sha256(dest)
        return self.request(url, consume, stream=True)
