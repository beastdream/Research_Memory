"""Explicitly opt-in HTTP provider adapter. No calls occur on ordinary enrichment.

The configured service accepts JSON {model, task, count, evidence} and returns
{qas: [{question, answer, paragraph_id, quoted_evidence, category}]}.
Only extractive, traceable answers survive the local gate; semantic validation
and human verification remain separate tasks.
"""
import re
import requests
from app.ingestion.enrichment import digest


def generate_candidates(paper, *, provider, model, endpoint, api_key=None):
    if provider!='grounded-json-http' or not model or not endpoint:
        raise ValueError('Configure provider=grounded-json-http, model and endpoint explicitly')
    paragraphs = {p['paragraph_id']: dict(p, section_id=s['section_id'])
                  for s in paper['full_text']['sections'] if s.get('kind')!='unclassified'
                  for p in s['paragraphs']}
    if not paragraphs:
        return []
    evidence = list(paragraphs.values())[:60]
    response = requests.post(endpoint, json=dict(model=model, count=5,
        task='Propose 3-5 diverse scientific QA candidates: methodology, contributions, results, comparisons, datasets/metrics. Answer only using an exact extractive quote from supplied evidence. Include paragraph_id, quoted_evidence, category, question, answer.',
        evidence=evidence), headers={'Authorization':'Bearer '+api_key} if api_key else {}, timeout=120)
    response.raise_for_status()
    candidates = response.json().get('qas', [])
    accepted, seen = [], set()
    for candidate in candidates:
        question = candidate.get('question','').strip()
        answer = candidate.get('answer','').strip()
        quote = candidate.get('quoted_evidence','')
        p = paragraphs.get(candidate.get('paragraph_id'))
        key = re.sub(r'\W+', ' ', question.casefold()).strip()
        if not key or key in seen or not p or not quote or quote not in p['text'] or not answer or answer not in quote:
            continue
        seen.add(key)
        accepted.append(dict(qa_id='synthetic-'+digest([paper['canonical_id'],model,key,quote]),
            paper_id=paper['canonical_id'], question=question, answer=answer, answer_type='extractive',
            source_type='synthetic', generator_model=model, generator_provider=provider,
            validation_status='candidate', answerability_status='extractive_evidence_checked',
            category=candidate.get('category'), split=None, training_eligible=False,
            evidence_refs=[dict(section_id=p['section_id'],paragraph_id=p['paragraph_id'],
                                table_id=None,figure_id=None,quoted_evidence=quote)]))
        if len(accepted)==5:
            break
    return accepted
