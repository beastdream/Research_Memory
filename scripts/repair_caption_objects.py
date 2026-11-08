"""Add source-bound caption candidates missed by the original line parser.

This additive repair reuses existing text/paragraph IDs, checks source checksums,
preserves richer DB documents, and never treats caption-only tables as complete.
"""
import copy
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.dataset_inspector import DEFAULT,load_file,local_path
from app.ingestion.enrichment import improve_object_captions,recover_caption_tables,validate_enriched,digest
from app.ingestion.arxiv_client import sha256
from app.storage.postgres import connect
from app.storage.enrichment import upsert_enrichment
from scripts.enrich_arxiv import write_jsonl,snapshot


def run():
    papers=load_file(DEFAULT)
    stats={'updated':0,'unchanged':0,'inserted':0}
    with connect() as db:
        _,before=snapshot(db)
        for number,original in enumerate(papers,1):
            p=copy.deepcopy(original)
            pdf=local_path(p['provenance']['pdf_path'])
            if sha256(pdf)!=p['provenance']['pdf_sha256']:
                raise ValueError('PDF checksum changed')
            improve_object_captions(p['figures_and_tables'],pdf,p)
            recover_caption_tables(p['figures_and_tables'],pdf)
            p['provenance']['object_parser_revision']='pdf-caption-block-1.1-ruled-tables'
            for kind in ('figure','table'):
                if any(o['type']==kind for o in p['figures_and_tables']):
                    p['quality'][kind+'s_status']='partial'
            validate_enriched(p)
            stats[upsert_enrichment(db,p)]+=1
            if number%10==0:
                print(f'Repaired caption/table candidates {number}/{len(papers)}',file=sys.stderr,flush=True)
        _,after=snapshot(db)
        if before!=after:
            raise ValueError('Legacy content preservation failed')
        stored=dict(db.execute('SELECT canonical_id,document FROM paper_enrichments'))
        exported=[stored[p['canonical_id']] for p in papers]
    write_jsonl(DEFAULT,exported)
    report=dict(stats,legacy_unchanged=True,export_digest=digest(exported),papers=len(exported),
        figure_candidates=sum(o['type']=='figure' for p in exported for o in p['figures_and_tables']),
        table_candidates=sum(o['type']=='table' for p in exported for o in p['figures_and_tables']))
    (ROOT/'reports/caption_repair.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    print(json.dumps(run(),indent=2))
