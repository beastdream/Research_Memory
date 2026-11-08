"""Show PostgreSQL counts; optionally search paper title/abstract."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.storage.postgres import connect, stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--search", default="", help="Case-insensitive title / abstract search")
    args = parser.parse_args()
    with connect() as db:
        # Safe diagnostic: prints database identity, NEVER DATABASE_URL/password.
        identity = db.execute(
            "SELECT current_database(), current_user, inet_server_addr()::text, inet_server_port()"
        ).fetchone()
        print("DATABASE:", identity[0], "USER:", identity[1],
              "SERVER:", identity[2], "PORT:", identity[3])
        print("COUNTS:", stats(db))
        if args.search:
            rows = db.execute(
                """
                SELECT canonical_id, title, has_full_text
                  FROM papers
                 WHERE title ILIKE %s OR abstract ILIKE %s
                 ORDER BY title LIMIT 15
                """,
                (f"%{args.search}%", f"%{args.search}%"),
            ).fetchall()
            print("SEARCH RESULTS:")
            for row in rows:
                print("  ", row)
            if not rows:
                print("  No match, try another keyword.")
        else:
            print("FIRST PAPERS:")
            for row in db.execute("SELECT canonical_id, title FROM papers ORDER BY title LIMIT 10"):
                print("  ", row)


if __name__ == "__main__":
    main()
