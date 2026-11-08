"""Offline JSON/JSONL/schema inventory; never reads credentials or embeds labels."""
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.ingestion.master_schema import validate_master


def run():
    inventory=[]
    for folder in ('datasets','docs/examples','schemas'):
        for path in sorted((ROOT/folder).rglob('*')):
            if path.suffix not in ('.json','.jsonl','.parquet') or not path.is_file():
                continue
            item=dict(path=str(path.relative_to(ROOT)),bytes=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),records=0,master_records=0,master_validation_errors=0)
            try:
                if path.suffix=='.parquet':
                    import pyarrow.parquet as pq
                    meta=pq.ParquetFile(path)
                    item.update(records=meta.metadata.num_rows,columns=meta.schema.names,status='metadata_read')
                else:
                    if path.suffix=='.jsonl':
                        with path.open(encoding='utf-8-sig') as stream:
                            try:
                                rows=[json.loads(line) for line in stream if line.strip()]
                            except json.JSONDecodeError:
                                value=json.loads(path.read_text(encoding='utf-8-sig'))
                                rows=value if isinstance(value,list) else [value]
                    else:
                        value=json.loads(path.read_text(encoding='utf-8-sig'))
                        rows=value if isinstance(value,list) else [value]
                    item['records']=len(rows)
                    for row in rows:
                        if isinstance(row,dict) and row.get('schema_version') in ('2.0','2.1'):
                            item['master_records']+=1
                            try:
                                validate_master(row)
                            except Exception:
                                item['master_validation_errors']+=1
                    item['status']='parsed'
            except Exception as error:
                item.update(status='failed',error=type(error).__name__)
            inventory.append(item)
    out=ROOT/'reports/dataset_inventory.json'
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(inventory,indent=2),encoding='utf-8')
    print(json.dumps(dict(files=len(inventory),parse_failures=sum(r['status']=='failed' for r in inventory),
        master_validation_errors=sum(r['master_validation_errors'] for r in inventory)),indent=2))


if __name__=='__main__':
    run()
