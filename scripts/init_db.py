"""Create PostgreSQL tables (safe to rerun)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.storage.postgres import connect, init_schema


def main() -> None:
    with connect() as db:
        init_schema(db)
    print("PostgreSQL schema ready. Tables: papers, paper_sources, sections, sessions, messages, memory_events, paper_master_sources, paper_master_versions")


if __name__ == "__main__":
    main()
