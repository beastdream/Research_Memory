"""Search OpenAlex for metadata ONLY (no claim of full-text retrieval)."""
import argparse
import json
import os
import sys
from pathlib import Path
import requests
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.ingestion.normalize import make_paper, write_jsonl

parser = argparse.ArgumentParser()
parser.add_argument('query', help='Scientific search terms')
parser.add_argument('--limit', type=int, default=20)
args = parser.parse_args()

params = {'search': args.query, 'per-page': max(1, min(100, args.limit)),
          'select': 'id,display_name,doi,ids,authorships,publication_year,primary_location,abstract_inverted_index'}
api_key = os.getenv('OPENALEX_API_KEY')
if api_key:
    params['api_key'] = api_key
else:
    print('WARNING: no OPENALEX_API_KEY; unauthenticated queries may be limited.')

response = requests.get(
    'https://api.openalex.org/works',
    params=params,
    timeout=45,
    headers={'User-Agent': 'ResearchMemory-week1/0.1 (educational research)'},
)
response.raise_for_status()
works = response.json().get('results', [])


def extract_abstract(inverted):
    if not inverted:
        return ''
    pos = [(idx, token) for token, indexes in inverted.items() for idx in indexes]
    pos.sort()
    return ' '.join(token for _, token in pos)


papers = []
for work in works:
    ids = work.get('ids') or {}
    doi = work.get('doi') or ''
    arxiv = ''
    # OpenAlex ids do not always include an arXiv id. Do not infer one from DOI.
    record = make_paper(
        'openalex', work['id'].rsplit('/', 1)[-1], work.get('display_name') or '',
        extract_abstract(work.get('abstract_inverted_index')),
        [], doi=doi, arxiv_id=arxiv,
        url=work.get('id') or '', split='metadata_only',
        metadata={'openalex_id': work.get('id'), 'publication_year': work.get('publication_year'),
                  'authors': [a.get('author', {}).get('display_name')
                              for a in work.get('authorships') or []],
                  'access_type': 'metadata_only'}
    )
    papers.append(record)

output = ROOT / 'datasets' / 'processed' / 'openalex_metadata.jsonl'
print('OpenAlex results:', write_jsonl(papers, output))
print('Saved metadata-only papers:', output)
