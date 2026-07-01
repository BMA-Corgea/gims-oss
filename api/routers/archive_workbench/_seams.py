# api/routers/archive_workbench/_seams.py
#
# Infra + service layer for the Archive Workbench router. Holds the 12 pytest
# monkeypatch seams (resolve_path / get_db_uri / load_schema / get_verb_schema /
# get_verb_group_log_config / load_verb_group_log / _jp_list_projects /
# _jp_project_exists / _HAS_S3 / DEBUG_ENABLED / _PSYCOPG_AVAILABLE / json_proxy)
# plus the `router` and every seam-reading helper, so those helpers see a patch
# applied to THIS module. Route handlers live in routes.py and read the patched
# seams via qualified `_seams.<name>` access. Tests patch attributes on THIS module.

from __future__ import annotations
from fastapi import APIRouter, Body, Query
from core.errors import AppError
from pathlib import Path
from typing import Any, Dict, List, Tuple, Iterable, Optional, Literal
import json
import sqlite3
import shutil
import os
import hashlib
from datetime import datetime
import contextlib
import re

# ------------------------------------------------------------------------------
# Debug control
# ------------------------------------------------------------------------------
from utils.logger import get_logger
log = get_logger(__name__)
DEBUG_ENABLED = log.is_debug()

# ------------------------------------------------------------------------------
# Optional Postgres (psycopg v3)
# ------------------------------------------------------------------------------
try:
    import psycopg  # pip install psycopg[binary]
    from psycopg import errors as pg_errors
    _PSYCOPG_AVAILABLE = True
except Exception as e:
    _PSYCOPG_AVAILABLE = False
    pg_errors = None
    log.debug("psycopg not available:", repr(e))

# ------------------------------------------------------------------------------
# S3-aware helpers (match verb_gui behavior)
# ensure_prefix: create visible "folder" (prefix) in S3 or mkdir locally
# touch: create empty file (S3 object or local file)
# read_text/write_text: S3-aware text IO
# + NEW: list_projects, list_dirnames, move_prefix, delete_prefix, prefix_exists, project_exists
# NOTE: When unavailable, fall back to local FS.
# ------------------------------------------------------------------------------
try:
    from api import json_proxy  # import the module so we can call all helpers off it
    ensure_prefix = json_proxy.ensure_prefix
    touch = json_proxy.touch
    read_text = json_proxy.read_text
    write_text = json_proxy.write_text
    _HAS_S3 = True
except Exception:
    _HAS_S3 = False
    json_proxy = None  # type: ignore

    def ensure_prefix(path: Path) -> bool:
        path.mkdir(parents=True, exist_ok=True)
        return True

    def touch(path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

    def read_text(path: Path, encoding: str = "utf-8") -> str:
        return path.read_text(encoding=encoding)

    def write_text(path: Path, data: str, encoding: str = "utf-8") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding=encoding)

# ---- local fallbacks for the new json_proxy helpers --------------------------
def _jp_list_projects_fallback() -> List[str]:
    from api.manifest.resolver import resolve_path
    projects_root = resolve_path(Path(), "project_root")
    return sorted([p.name for p in projects_root.iterdir() if p.is_dir()])

def _jp_list_dirnames_fallback(path_str: str, include_hidden: bool = False) -> List[str]:
    p = Path(path_str)
    if not p.exists() or not p.is_dir():
        return []
    return sorted([d.name for d in p.iterdir() if d.is_dir() and (include_hidden or not d.name.startswith("."))])

def _jp_move_prefix_fallback(src: str, dst: str) -> None:
    # move files/dirs on local FS
    shutil.move(src, dst)

def _jp_delete_prefix_fallback(prefix: str) -> None:
    p = Path(prefix)
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    elif p.exists():
        try:
            os.remove(p.as_posix())
        except FileNotFoundError:
            pass

def _jp_prefix_exists_fallback(prefix: str) -> bool:
    return Path(prefix).exists()

def _jp_project_exists_fallback(project_name: str) -> bool:
    from api.manifest.resolver import resolve_path
    projects_root = resolve_path(Path(), "project_root")
    return (projects_root / project_name).exists()

# Small wrappers that call json_proxy if present, else local fallbacks
def _jp_list_projects() -> List[str]:
    if _HAS_S3 and hasattr(json_proxy, "list_projects"):
        return sorted(json_proxy.list_projects())
    return _jp_list_projects_fallback()

def _jp_list_dirnames(path_str: str, include_hidden: bool = False) -> List[str]:
    if _HAS_S3 and hasattr(json_proxy, "list_dirnames"):
        return sorted(json_proxy.list_dirnames(path_str, include_hidden=include_hidden))
    return _jp_list_dirnames_fallback(path_str, include_hidden)

def _jp_move_prefix(src: str, dst: str) -> None:
    if _HAS_S3 and hasattr(json_proxy, "move_prefix"):
        return json_proxy.move_prefix(src, dst)
    return _jp_move_prefix_fallback(src, dst)

def _jp_delete_prefix(prefix: str) -> None:
    if _HAS_S3 and hasattr(json_proxy, "delete_prefix"):
        return json_proxy.delete_prefix(prefix)
    return _jp_delete_prefix_fallback(prefix)

def _jp_prefix_exists(prefix: str) -> bool:
    if _HAS_S3 and hasattr(json_proxy, "prefix_exists"):
        return bool(json_proxy.prefix_exists(prefix))
    return _jp_prefix_exists_fallback(prefix)

def _jp_project_exists(project_name: str) -> bool:
    if _HAS_S3 and hasattr(json_proxy, "project_exists"):
        return bool(json_proxy.project_exists(project_name))
    return _jp_project_exists_fallback(project_name)

# ------------------------------------------------------------------------------
# Imports from your codebase
# ------------------------------------------------------------------------------
from api.manifest.resolver import resolve_path, get_db_uri  # project-aware paths + RDS DSNs
from api.i_o import (
    load_schema,
    get_verb_schema,
    get_verb_group_log_config,    # S3-aware (DB-first) log config load
    load_verb_group_log,          # S3-aware (DB-first) JSONL log loader
    io_list_projects,
)
from core.archive_workbench import (
    Plan, PlanStep, EnsureSoftColumns, EnsureArchiveTable, SQLStep, FileOp,
    plan_apply_archive_policy_for_nouns,
    plan_soft_archive_nouns, plan_hard_archive_nouns,
    plan_restore_nouns_soft, plan_restore_nouns_hard,
    plan_archive_runs_soft, plan_archive_runs_hard, plan_restore_runs
)

# ------------------------------------------------------------------------------
# Package submodules (split from the original 2417-line archive_workbench.py).
# Bodies were moved VERBATIM (see each submodule). The route handlers and every
# helper that reads a monkeypatch-seam name (resolve_path / get_db_uri /
# load_schema / get_verb_schema / get_verb_group_log_config / load_verb_group_log
# / _HAS_S3 / json_proxy / _jp_* / DEBUG_ENABLED / _PSYCOPG_AVAILABLE) stay in
# this __init__ on purpose: pytest patches attributes on THIS package object, and
# a function only sees such patches when it is defined in this same namespace.
# Only seam-independent helpers were extracted below.
# ------------------------------------------------------------------------------
import api.i_o as i_o  # exposed so tests can patch awg.i_o.<name>
from .db_meta import _DBHandle, _normalize_for_psycopg
from .index_tables import (
    _ExecContext,
    _ensure_soft_columns,
    _ensure_aux_archive_tables,
    _ensure_archive_table,
)
from .sql_exec import _exec_sql_step, _safe_commit, _safe_rollback
from .archive_index import (
    _insert_noun_archive_index_rows,
    _insert_runs_archive_index_row,
)
from .noun_store import (
    _rec_is_archived,
    _rec_run_id,
    _archived_noun_ids,
    _archive_noun_ids,
    _restore_noun_ids,
)
from .plans import _serialize_plan

# ------------------------------------------------------------------------------
# Router
# ------------------------------------------------------------------------------
router = APIRouter(prefix="/api/archive_workbench", tags=["Archive_Workbench"])

# ------------------------------------------------------------------------------
# Backend selection (SQLite vs RDS Postgres)
# ------------------------------------------------------------------------------


def _effective_db_target(project_path: Path, key: str) -> Tuple[str, str]:
    """
    Returns (kind, target):
      - ("pg", DSN) if resolver returns a Postgres URI for the key
      - ("sqlite", /abs/path/to.db) otherwise
    Keys are 'object_sql_db' and 'archive_sql_db'.
    """
    try:
        uri = get_db_uri(key)
        log.debug(f"[dsn] {key} ->", uri)
    except Exception as e:
        log.debug(f"[dsn] {key} resolver failed:", repr(e))
        uri = None

    if uri and uri.startswith("postgresql"):
        return ("pg", _normalize_for_psycopg(uri))

    db_path = resolve_path(project_path, key)
    return ("sqlite", db_path.as_posix())


@contextlib.contextmanager
def _open_db(project_path: Path, key: str) -> _DBHandle:
    """
    Open a single DB (hot or archive) with autocommit OFF for PG; caller manages tx boundaries.
    Adds safe timeouts to avoid indefinite stalls.
    """
    kind, target = _effective_db_target(project_path, key)
    if kind == "pg" and _PSYCOPG_AVAILABLE:
        log.debug("[db] connect PG:", key, target)
        conn = psycopg.connect(target, autocommit=False)
        try:
            with conn.cursor() as cur:
                # Keep operations in the 'public' schema (harmless if public default)
                try:
                    cur.execute("SET search_path TO public;")
                except Exception:
                    pass
                # Avoid indefinite lock/wait hangs
                try:
                    cur.execute("SET lock_timeout = '5s';")
                    cur.execute("SET statement_timeout = '30s';")
                    cur.execute("SET idle_in_transaction_session_timeout = '60s';")
                except Exception as e:
                    log.debug("[db][pg] timeout SETs failed (non-fatal):", repr(e))
            yield _DBHandle("pg", conn)
        except Exception:
            # On exit/error: best-effort rollback (ignore if admin terminated)
            try:
                conn.rollback()
            except Exception as e:
                if not (pg_errors and isinstance(e, pg_errors.AdminShutdown)):
                    log.debug("[db][pg] rollback failed:", repr(e))
            raise
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return

    if kind == "pg" and not _PSYCOPG_AVAILABLE:
        log.debug("[db] psycopg missing; falling back to SQLite for", key)

    # SQLite fallback
    path = resolve_path(project_path, key)
    ensure_prefix(path.parent)  # S3/FS-safe parent creation
    log.debug("[db] connect SQLite:", key, path)
    conn = sqlite3.connect(path.as_posix(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("PRAGMA foreign_keys=ON;")
        yield _DBHandle("sqlite", conn)
    finally:
        try:
            conn.close()
        except Exception:
            pass

@contextlib.contextmanager
def _open_hot_and_arc(project_path: Path) -> Tuple[_DBHandle, _DBHandle]:
    """
    Open both hot (object_sql_db) and archive (archive_sql_db) DBs.
    Transaction boundaries are controlled by the executor.
    """
    with _open_db(project_path, "object_sql_db") as hot:
        with _open_db(project_path, "archive_sql_db") as arc:
            yield hot, arc

# ------------------------------------------------------------------------------
# Helpers: table names & columns (RDS-aware)
# ------------------------------------------------------------------------------

_SAN_RE = re.compile(r"[^0-9a-zA-Z_]")




# ── Instances-backed noun archive (Phase 6/R17) ─────────────────────────────────
# Nouns live in the unified `instances` store, not per-noun SQL tables. Hot records are in
# objects.db; hard-archived records move to a separate archive.db (same schema). These helpers
# replace the legacy per-noun-table SQL for noun archive/restore/candidates/linked-ids.
# Run-folder archiving + the *_archive_index tables are unchanged.
from core.storage.factory import get_record_store, get_archive_record_store, collection_for_noun


def _noun_pf(project_path: Path, noun_type: str, entry: Optional[dict] = None) -> str:
    if entry is None:
        entry = (load_schema(project_path, "noun") or {}).get(noun_type) or {}
    return (entry or {}).get("primary_id_field") or "id"






# ------------------------------------------------------------------------------
# Generic DB metadata & execution helpers
# ------------------------------------------------------------------------------







# ------------------------------------------------------------------------------
# Utilities (unchanged signatures; now S3-aware where applicable)
# ------------------------------------------------------------------------------

def _resolve_project_path(project_name: str) -> Path:
    log.debug("[_resolve_project_path] input:", project_name)
    projects_root = resolve_path(Path(), "project_root")
    candidate = projects_root / project_name
    log.debug("[_resolve_project_path] candidate:", candidate)
    # S3-aware existence check (falls back to FS)
    if not _jp_project_exists(project_name):
        raise AppError("PROJECT_NOT_FOUND", f"Project '{project_name}' not found", status=404, details={"project": project_name})
    return candidate







def _load_policy(project_path: Path) -> Dict[str, Any]:
    policy_path = resolve_path(project_path, "archive_policy")
    log.debug("[_load_policy] path:", policy_path)
    try:
        payload = read_text(policy_path, encoding="utf-8")  # S3-aware
    except FileNotFoundError:
        log.debug("[_load_policy] missing; using empty {}")
        return {}
    except Exception as e:
        raise AppError("INVALID_ARCHIVE_POLICY", f"Invalid archive_policy.json: {e}", status=400)
    try:
        data = json.loads(payload)
        log.debug("[_load_policy] loaded keys:", list(data.keys()))
        return data
    except Exception as e:
        raise AppError("INVALID_ARCHIVE_POLICY", f"Invalid archive_policy.json: {e}", status=400)


# ------------------------------------------------------------------------------
# Plan executor (DB + FS)  — RDS-aware
# ------------------------------------------------------------------------------






# ── Filesystem journal: makes archive plans atomic-or-rollback across DB + FS (R17) ──
def _journal_backup_path(src: str) -> str:
    """A unique sibling prefix to move a to-be-deleted path aside, so the delete is reversible.
    Uses a uuid so two concurrent plans can never collide on a backup path."""
    import uuid
    return f"{src}.__arcjournal_bak_{uuid.uuid4().hex}__"


def _safe_rmdir(p: Path) -> None:
    """Best-effort removal of a directory we created and left empty (undo of mkdir_p)."""
    try:
        if p.is_dir() and not any(p.iterdir()):
            p.rmdir()
    except Exception:
        pass


def _read_text_or_none(p: Path) -> Optional[str]:
    try:
        return read_text(p, encoding="utf-8")
    except FileNotFoundError:
        return None
    except Exception:
        return None


class _FsJournal:
    """Runs each FileOp AND records its inverse, so a failed plan rolls the filesystem
    back. Paired with a single DB commit at the very end of the plan, this makes an
    archive/restore atomic across DB + FS: a mid-plan failure leaves NEITHER partial DB
    nor partial FS state (the previous executor committed the DB before each FS op, so a
    failure stranded files with no way back). Inverses (replayed in REVERSE on rollback):
      • mkdir_p   → rmdir the dir if we created it and left it empty
      • move      → move it back
      • write_text→ restore the prior text, or delete the file if it was new
      • delete    → moved aside to a backup first; move it back (backups purged on success)
    """

    def __init__(self) -> None:
        self._undo: List[Any] = []      # callables; replayed in REVERSE on rollback
        self._backups: List[str] = []   # delete-aside backups; purged on success

    def exec_op(self, step: FileOp) -> None:
        op = step.op
        log.debug("[journal] op:", op, "src:", step.src, "dst:", step.dst)
        if op == "mkdir_p" and step.dst:
            p = Path(step.dst)
            existed = _jp_prefix_exists(str(p))
            ensure_prefix(p)
            if not existed:
                self._undo.append(lambda p=p: _safe_rmdir(p))
        elif op == "move" and step.src and step.dst:
            _jp_move_prefix(step.src, step.dst)
            self._undo.append(lambda s=step.src, d=step.dst: _jp_move_prefix(d, s))
        elif op == "write_text" and step.dst is not None:
            p = Path(step.dst)
            existed = _jp_prefix_exists(str(p))                     # decide by EXISTENCE...
            prior = _read_text_or_none(p) if existed else None      # ...not readability
            ensure_prefix(p.parent)
            write_text(p, step.text or "", encoding="utf-8")
            if not existed:
                # brand-new file → undo by deleting it
                self._undo.append(lambda p=p: _jp_delete_prefix(str(p)))
            elif prior is not None:
                # existed + readable → restore the prior text
                self._undo.append(lambda p=p, t=prior: write_text(p, t, encoding="utf-8"))
            # else: existed but UNREADABLE → no-op undo (never delete a file that was there)
        elif op == "delete" and step.src:
            # Move aside instead of hard-deleting so the delete is reversible; the backup is
            # purged only after the whole plan (incl. the DB) commits.
            bak = _journal_backup_path(step.src)
            _jp_move_prefix(step.src, bak)
            self._backups.append(bak)
            self._undo.append(lambda s=step.src, b=bak: _jp_move_prefix(b, s))

    def rollback(self) -> None:
        for fn in reversed(self._undo):
            try:
                fn()
            except Exception as e:
                log.warning("[journal] undo step failed (continuing):", repr(e))

    def discard_backups(self) -> None:
        for b in self._backups:
            try:
                _jp_delete_prefix(b)
            except Exception as e:
                log.debug("[journal] backup purge failed:", repr(e))


def _execute_plan(project_path: Path, plan: Plan) -> Dict[str, Any]:
    """Execute an archive/restore plan atomically across DB + FS (R17).

    All SQL runs in ONE transaction (commit deferred to the end); every FileOp runs
    through an :class:`_FsJournal` that records its inverse. On success the DB commits
    once and the journal's delete-backups are purged. On ANY failure the DB transaction
    is rolled back AND the journal replays its inverses, so the plan is all-or-nothing.
    """
    log.debug("[_execute_plan] description:", plan.description, "steps:", len(plan.steps))
    with _open_hot_and_arc(project_path) as (hot, arc):
        _ensure_aux_archive_tables(arc)

        ctx = _ExecContext(hot, arc)

        if hot.kind == "sqlite":
            hot.conn.execute("BEGIN")
        if arc.kind == "sqlite":
            arc.conn.execute("BEGIN")

        journal = _FsJournal()
        try:
            for s in plan.steps:
                if isinstance(s, EnsureSoftColumns):
                    _ensure_soft_columns(ctx, s.table)
                elif isinstance(s, EnsureArchiveTable):
                    _ensure_archive_table(ctx, s.source_table, s.dest_table, s.columns, s.include_meta)
                elif isinstance(s, SQLStep):
                    _exec_sql_step(ctx, s)
                elif isinstance(s, FileOp):
                    # FS op + recorded inverse; NO DB commit here (deferred to the end).
                    journal.exec_op(s)
                else:
                    log.debug("[_execute_plan] ! unknown step:", type(s).__name__)

            # All steps applied and the FS journal fully recorded — commit the DB, then the FS
            # changes (incl. delete-backups) become permanent. NOTE: hot + arc are two SEPARATE
            # SQLite files, so this final commit pair is not a single atomic transaction — if the
            # 2nd commit fails after the 1st succeeds there is a narrow non-atomic window (the
            # journal still reverts the FS). This is inherent to two on-disk databases; the big win
            # is that any failure DURING the plan now rolls back DB + FS together (previously the DB
            # was committed before every FileOp). A future single-DB (Postgres) backing could make
            # the whole thing one transaction.
            if hot.kind == "sqlite":
                hot.conn.commit()
            else:
                _safe_commit(hot.conn)
            if arc.kind == "sqlite":
                arc.conn.commit()
            else:
                _safe_commit(arc.conn)

            journal.discard_backups()
            log.debug("[_execute_plan] commit complete")
            return {"ok": True, "description": plan.description, "meta": plan.meta}

        except Exception as e:
            # Atomic rollback: undo the DB transaction AND replay the FS journal inverses,
            # so a mid-plan failure leaves neither partial DB nor partial FS state.
            if hot.kind == "sqlite":
                try:
                    hot.conn.rollback()
                except Exception as e2:
                    log.debug("[_execute_plan] hot rollback failed:", repr(e2))
            else:
                _safe_rollback(hot.conn)

            if arc.kind == "sqlite":
                try:
                    arc.conn.rollback()
                except Exception as e2:
                    log.debug("[_execute_plan] arc rollback failed:", repr(e2))
            else:
                _safe_rollback(arc.conn)

            journal.rollback()
            log.debug("[_execute_plan] X rollback (DB + FS) due to:", e)
            raise AppError("PLAN_EXECUTION_FAILED", str(e), status=500, details={"description": plan.description})

# ------------------------------------------------------------------------------
# JSON helpers (S3-aware)
# ------------------------------------------------------------------------------





# ------------------------------------------------------------------------------
# Noun linkage & scanning (RDS-aware)
# ------------------------------------------------------------------------------

def _split_noun_ids_by_actual_archive(project_path: Path, noun_type: str, ids: List[str]) -> Tuple[List[str], List[str]]:
    """
    Return (soft_ids, hard_ids):
      - soft: nouns HOT DB has archived=1
      - hard: archive DB has the row
    """
    if not ids:
        return [], []
    coll = collection_for_noun(noun_type)
    pf = _noun_pf(project_path, noun_type)
    hot = get_record_store(project_path)
    arc = get_archive_record_store(project_path)

    soft_ids = [
        i for i in ids
        if (lambda r: r is not None and _rec_is_archived(r))(hot.get_record(coll, pf, i))
    ]
    hard_ids = [
        i for i in ids
        if i not in soft_ids and arc.get_record(coll, pf, i) is not None
    ]
    return soft_ids, hard_ids

def _collect_linked_noun_ids(project_path: Path, test_type: Optional[str], run_id: str) -> Tuple[Optional[str], List[str]]:
    """
    Finds linked nouns based on verb schema hint.
    Safely skips if the hinted table does not have a run ID column.
    """
    if not test_type:
        return None, []
    try:
        vs = get_verb_schema(project_path, test_type) or {}
    except Exception:
        vs = {}
    noun_type = ((vs.get("data_entry_schema") or {}).get("set_up_inputs") or {}).get("noun_type_ref")
    if not noun_type:
        return None, []

    coll = collection_for_noun(noun_type)
    pf = _noun_pf(project_path, noun_type)
    recs = get_record_store(project_path).list_records(coll)
    ids = [str(r.get(pf)) for r in recs if _rec_run_id(r) == run_id and r.get(pf) is not None]
    return noun_type, ids

def _collect_linked_noun_ids_by_scan(project_path: Path, run_id: str) -> Dict[str, List[str]]:
    """
    Brute-force scan all noun tables for rows with a run ID column == run_id
    across HOT and ARCHIVE DBs.
    Safely skips tables that do not have a run ID column.
    Returns {noun_type: [ids...]} with IDs deduped and sorted.
    """
    log.debug("[_collect_linked_noun_ids_by_scan] run_id:", run_id)
    out: Dict[str, List[str]] = {}

    noun_schema = load_schema(project_path, "noun") or {}
    noun_items = list((noun_schema or {}).items())

    for noun_type, entry in noun_items:
        pf = _noun_pf(project_path, noun_type, entry)
        coll = collection_for_noun(noun_type)
        ids: set[str] = set()
        for r in (
            get_record_store(project_path).list_records(coll)
            + get_archive_record_store(project_path).list_records(coll)
        ):
            if _rec_run_id(r) == run_id and r.get(pf) is not None:
                ids.add(str(r.get(pf)))
        if ids:
            out[noun_type] = sorted(ids)

    return out


# ----------------------- Schema index & legacy (RDS-aware) --------------------

ARCHIVE_OP_TIMEOUT_SEC = 90.0








