# api/routers/backup/pg_dump.py
#
# Postgres (RDS) logical-dump engine: DSN normalization, connection helper, and
# per-DB COPY-to-CSV dumpers. Moved VERBATIM from the former single-file
# api/routers/backup.py (no logic changes).

from pathlib import Path
from typing import Optional, Dict, Any, List
import contextlib

from api.manifest.resolver import get_db_uri  # RDS-aware resolver
from api.storage_aws import normalize_pg_dsn as _normalize_for_psycopg

from ._router import log
from .local_capture import KNOWN_DB_KEYS

# Optional Postgres client for RDS
try:
    import psycopg  # psycopg v3
    _PSYCOPG_AVAILABLE = True
except Exception as e:
    _PSYCOPG_AVAILABLE = False
    log.debug("psycopg not available:", repr(e))


# ──────────────────────────────────────────────────────────────────────────────
# DSN helpers (RDS)
# ──────────────────────────────────────────────────────────────────────────────
@contextlib.contextmanager
def _pg_conn(dsn: str):
    if not _PSYCOPG_AVAILABLE:
        raise RuntimeError("psycopg not available")
    conn = psycopg.connect(dsn, autocommit=True)
    try:
        with conn.cursor() as cur:
            try:
                cur.execute("SET search_path TO public;")
            except Exception:
                pass
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass

# ──────────────────────────────────────────────────────────────────────────────
# Postgres logical dumps (RDS)
# ──────────────────────────────────────────────────────────────────────────────
def _get_key_dsn(key: str) -> Optional[str]:
    try:
        uri = get_db_uri(key)
        if uri and uri.startswith("postgresql"):
            return _normalize_for_psycopg(uri)
    except Exception as e:
        log.debug("[dsn] resolver failed for", key, "->", repr(e))
    return None

def _pg_copy_to_csv(conn, sql: str, params: dict, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    log.debug("[pg][copy] →", out_path.name, "::", sql.replace("\n", " "))
    with conn.cursor() as cur, out_path.open("w", encoding="utf-8", newline="") as f:
        with cur.copy(sql, params=params) as copy:
            while True:
                chunk = copy.read()
                if not chunk:
                    break
                if isinstance(chunk, memoryview):
                    chunk = chunk.tobytes()
                f.write(chunk.decode("utf-8"))

def _pg_list_project_prefixed_tables(conn, project: str) -> List[str]:
    like_pattern = project + r"\_%"
    sql = """
      SELECT quote_ident(n.nspname) || '.' || quote_ident(c.relname) AS fqtn
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      WHERE n.nspname = 'public'
        AND c.relkind IN ('r','p')
        AND c.relname LIKE %(pfx)s ESCAPE '\\'
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"pfx": like_pattern})
        return [r[0] for r in cur.fetchall()]

def _pg_list_meta_tables_with_project_col(conn) -> List[str]:
    sql = """
      SELECT quote_ident(n.nspname) || '.' || quote_ident(c.relname) AS fqtn
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'project'
      WHERE n.nspname = 'public'
        AND c.relkind IN ('r','p')
        AND c.relname LIKE 'meta%%'
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return [r[0] for r in cur.fetchall()]

def _dump_archive_sql_db_pg(project: str, out_dir: Path, dsn: str) -> Dict[str, Any]:
    files = []
    with _pg_conn(dsn) as conn:
        for fqtn in _pg_list_project_prefixed_tables(conn, project):
            table_name = fqtn.split(".")[-1]
            out = out_dir / f"{table_name}.csv"
            _pg_copy_to_csv(conn, f"COPY (SELECT * FROM {fqtn}) TO STDOUT WITH CSV HEADER", {}, out)
            files.append(out.name)
        for name in ("noun_archive_index", "runs_archive_index"):
            out = out_dir / f"{name}.csv"
            _pg_copy_to_csv(
                conn,
                f"COPY (SELECT * FROM public.{name} WHERE project = %(project)s) TO STDOUT WITH CSV HEADER",
                {"project": project},
                out,
            )
            files.append(out.name)
    return {"backend": "pg", "dir": out_dir.name, "files": files, "file_count": len(files)}

def _dump_objects_sql_db_pg(project: str, out_dir: Path, dsn: str) -> Dict[str, Any]:
    files = []
    with _pg_conn(dsn) as conn:
        for fqtn in _pg_list_project_prefixed_tables(conn, project):
            table_name = fqtn.split(".")[-1]
            out = out_dir / f"{table_name}.csv"
            _pg_copy_to_csv(conn, f"COPY (SELECT * FROM {fqtn}) TO STDOUT WITH CSV HEADER", {}, out)
            files.append(out.name)
        for fqtn in _pg_list_meta_tables_with_project_col(conn):
            table_name = fqtn.split(".")[-1]
            out = out_dir / f"{table_name}.csv"
            _pg_copy_to_csv(
                conn,
                f"COPY (SELECT * FROM {fqtn} WHERE project = %(project)s) TO STDOUT WITH CSV HEADER",
                {"project": project},
                out,
            )
            files.append(out.name)
    return {"backend": "pg", "dir": out_dir.name, "files": files, "file_count": len(files)}

def _dump_nodes_db_pg(project: str, out_dir: Path, dsn: str) -> Dict[str, Any]:
    files = []
    with _pg_conn(dsn) as conn:
        out = out_dir / "audit_log.csv"
        _pg_copy_to_csv(
            conn,
            """
            COPY (
              SELECT *
              FROM public.audit_log
              WHERE %(project)s = ANY ( string_to_array(project, ', ') )
            ) TO STDOUT WITH CSV HEADER
            """,
            {"project": project},
            out,
        )
        files.append(out.name)
        out = out_dir / "compliance_log.csv"
        _pg_copy_to_csv(
            conn,
            "COPY (SELECT * FROM public.compliance_log WHERE project = %(project)s) TO STDOUT WITH CSV HEADER",
            {"project": project},
            out,
        )
        files.append(out.name)
    return {"backend": "pg", "dir": out_dir.name, "files": files, "file_count": len(files)}

def _dump_logins_db_pg(project: str, out_dir: Path, dsn: str) -> Dict[str, Any]:
    files = []
    with _pg_conn(dsn) as conn:
        out = out_dir / "projects.csv"
        _pg_copy_to_csv(
            conn,
            "COPY (SELECT * FROM public.projects WHERE name = %(project)s) TO STDOUT WITH CSV HEADER",
            {"project": project},
            out,
        )
        files.append(out.name)
        out = out_dir / "accounts_projects.csv"
        _pg_copy_to_csv(
            conn,
            """
            COPY (
              SELECT ap.*
              FROM public.accounts_projects ap
              JOIN public.projects p ON p.id = ap.project_id
              WHERE p.name = %(project)s
            ) TO STDOUT WITH CSV HEADER
            """,
            {"project": project},
            out,
        )
        files.append(out.name)
        out = out_dir / "users.csv"
        _pg_copy_to_csv(
            conn,
            """
            COPY (
              SELECT u.*
              FROM public.users u
              WHERE u.id IN (
                SELECT ap.user_id
                FROM public.accounts_projects ap
                JOIN public.projects p ON p.id = ap.project_id
                WHERE p.name = %(project)s
              )
            ) TO STDOUT WITH CSV HEADER
            """,
            {"project": project},
            out,
        )
        files.append(out.name)
    return {"backend": "pg", "dir": out_dir.name, "files": files, "file_count": len(files)}

def _collect_pg_artifacts(project: str, project_path: Path, db_out_dir: Path) -> Dict[str, dict]:
    artifacts: Dict[str, dict] = {}
    for key in KNOWN_DB_KEYS:
        dsn = _get_key_dsn(key)
        if not dsn:
            continue
        out_dir = db_out_dir / key
        try:
            if key == "archive_sql_db":
                meta = _dump_archive_sql_db_pg(project, out_dir, dsn)
            elif key == "object_sql_db":
                meta = _dump_objects_sql_db_pg(project, out_dir, dsn)
            elif key == "nodes_db":
                meta = _dump_nodes_db_pg(project, out_dir, dsn)
            elif key == "logins_db":
                meta = _dump_logins_db_pg(project, out_dir, dsn)
            else:
                log.debug("[pg] unknown key, skipping:", key)
                continue
            meta["key"] = key
            artifacts[key] = meta
            log.debug("[pg] dumped", key, "->", meta)
        except Exception as e:
            log.debug("[pg] dump failed for", key, ":", repr(e))
            artifacts[key] = {"key": key, "backend": "pg", "dir": str(out_dir.relative_to(db_out_dir)), "error": str(e)}
    return artifacts
