"""Read-only report: papers flagged full text but containing only SciFact abstracts.
This file never modifies production records.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.storage.postgres import connect

SQL = """
SELECT count(*)
FROM papers p
WHERE p.has_full_text
AND EXISTS (SELECT 1 FROM paper_sources s WHERE s.canonical_id = p.canonical_id AND s.source = 'scifact')
AND NOT EXISTS (SELECT 1 FROM paper_sources s WHERE s.canonical_id = p.canonical_id AND s.source <> 'scifact')
AND NOT EXISTS (SELECT 1 FROM sections sec WHERE sec.canonical_id = p.canonical_id AND sec.heading <> 'Abstract')
"""

def main():
    with connect() as db:
        identity = db.execute('SELECT current_database()').fetchone()[0]
        print('Database:', identity)
        print('SciFact abstract-only papers marked as full text:', db.execute(SQL).fetchone()[0])
        print('Read-only check. No data was changed.')

if __name__ == '__main__':
    main()
