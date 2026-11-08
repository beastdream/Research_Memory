# ResearchMemory - PostgreSQL Migration (Windows 10/11)

This patch replaces only database scripts. It preserves your existing datasets,
normalizers, SQLite module and pytest tests. **Back up the original `scripts` files first.**

1. Install PostgreSQL and pgAdmin from https://www.postgresql.org/download/windows/
   - Use PostgreSQL 16, 17 or 18 (a supported release).
   - Set/remember the `postgres` administrator password.
   - Default port: `5432`; consider selecting a data directory on D: to save space on C:.
   - Ensure PostgreSQL server is running.

2. In pgAdmin, select the default `postgres` database -> Query Tool.
   Run the following two SQL statements **separately** as superuser:

   ```sql
   CREATE ROLE researchmemory WITH LOGIN PASSWORD 'YOUR_STRONG_PASSWORD';
   ```
   ```sql
   CREATE DATABASE researchmemory OWNER researchmemory;
   ```
   If you've already created these, do not re-run them. Password is only illustrative;
   use your own unique password. For a URL, special characters must be URL-encoded.

3. From VS Code CMD terminal (`(.venv) D:\ResearchMemory>`):

   ```cmd
   cd /d D:\ResearchMemory
   python -m pip install -r requirements-postgres.txt
   copy .env.example .env
   notepad .env
   ```

   Edit `.env` and set `DATABASE_URL` to local credentials. Do not commit it.
   `.gitignore` in the original project already excludes `.env`.
   **Do not use superuser `postgres` as the application's database account.**

4. Load and verify:

   ```cmd
   python scripts\init_db.py
   python scripts\import_db.py
   python scripts\check_db.py
   python scripts\check_db.py --search memory
   python scripts\import_db.py
   python scripts\check_db.py
   ```

5. Re-run your existing tests (`test_pipeline.py` uses SQLite as a local
   unit test, not the PostgreSQL integration test):

   ```cmd
   python -m pytest -q
   ```

6. Confirm via pgAdmin by opening `researchmemory -> Schemas -> public -> Tables`.
   A simple manual SQL query:

   ```sql
   SELECT count(*) FROM papers;
   SELECT source, count(*) FROM paper_sources GROUP BY source ORDER BY source;
   ```

## How patch works

- PostgreSQL adapter: `app/storage/postgres.py` using `psycopg` and JSONB.
- PostgreSQL scripts: `scripts/init_db.py`, `scripts/import_db.py`, `scripts/check_db.py`.
- `.env.example` is a template; `.env` stays local.
- `requirements-postgres.txt` includes the existing requirements and DB driver.
- Import uses a safe allowlist. Evaluation answers and LongMemEval remain separate.
- Repeat imports are idempotent by canonical ID and source/section composite keys.
- Existing SQLite data is NOT automatically migrated. Re-run import from prepared JSONL.
- Does not create a `memories` table yet: `memory_events` is a future-use scaffold.
- PDF bytes and FAISS indices remain files on disk, not rows in PostgreSQL.

## Troubleshooting

- `ModuleNotFoundError: No module named psycopg` -> install requirements-postgres.txt in `.venv`.
- `Connection refused` -> start PostgreSQL server; check host/port.
- `password authentication failed` -> check `.env` and DB username/password.
- `database "researchmemory" does not exist` -> create it in pgAdmin.
- `permission denied for schema public` -> ensure `researchmemory` owns the new DB (or ask a DBA to grant CREATE on `public`).
- `InvalidPassword / bad URL` -> URL-encode reserved special chars (such as @, :, /, #) in the password.
- `no relation "papers"` -> run `python scripts\init_db.py` first.
