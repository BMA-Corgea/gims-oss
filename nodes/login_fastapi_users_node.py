from __future__ import annotations

"""
Login (FastAPI Users) — JWT (Bearer) + Cookie sessions, CSRF helper, audit hooks.

CHANGES in this version:
  • Split storage:
      - GLOBAL (logins.db): User, AccountProject (user↔project_id↔role_name), Project (project_code registry).
      - PER-PROJECT (nodes.db): AuditLog, Task.
  • fastapi-users binds to GLOBAL logins.db (one identity store).
  • Role resolution reads roles.json in the *current* project; role *assignment*
    matches AccountProject.project_code against *aliases* for this project:
      { config project_code, project folder name, projects.code, projects.id }.
  • /auth/me returns { user, roles, scopes, feature_tags, projects:[names…], project_codes:[codes…] }.
  • Registration hook records chosen project_code -> AccountProject (unassigned role, pending)
    and ensures the project exists in GLOBAL projects table.
  • Enforce verification: non-superusers must be verified to pass require_scopes/require_login.
  • /auth/me suppresses roles/scopes/tags for unverified users (but still lists memberships).
  • PRAGMAs applied on *every SQLite connection* to prevent mixed journal modes.
  • Self-healing migration for per-project audit_log if a legacy FK exists.
  • NEW: RDS-aware engine selection via api.manifest.resolver (no POSIX ops in RDS).

  • UPDATE: AuditLog.project now stores a *comma-separated list of the user’s project names*
    (looked up from GLOBAL logins.db). The {project} URL segment still selects which nodes.db
    to write into; only the stored value changed.
  
  • S3-AWARE: Replaced direct json.load/path.exists for config.json and roles.json
    with i_o.load_data().
"""

import os
import json
import uuid
import secrets
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, List, Optional, Tuple, Union, Set

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

# Orchestration glue
from core.orchestration.node import Node, NodeKind
from core.orchestration.module import Module

# Project helpers
# Resolver imports are defensive: if your resolver doesn't expose a symbol yet, we fallback.
# Simplified: use the resolver’s unified interface
from api.manifest.resolver import resolve_path, get_db_uri, is_rds_key, RDS_ENABLED

# S3-AWARE I/O functions
from api import i_o 
from api.i_o import load_local_layout_map

# FastAPI Users
from fastapi_users import FastAPIUsers, BaseUserManager
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users.schemas import BaseUser, BaseUserCreate, BaseUserUpdate
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase

# SQLAlchemy async
from sqlalchemy import Boolean, String, Text, Float, event, select, and_, ForeignKey, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

# ──────────────────────────────────────────────────────────────────────────────
# Debug
# ──────────────────────────────────────────────────────────────────────────────
DEBUG_ENABLED = False

def debug(*args, **kwargs):
    if DEBUG_ENABLED:
        print("[login]", *args, **kwargs)

# ──────────────────────────────────────────────────────────────────────────────
# 0) Paths & roots
# ──────────────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/login", tags=["Login"])

def _api_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "api"

def _repo_root() -> Path:
    return _api_dir().parent

def _projects_root() -> Path:
    layout = load_local_layout_map(_api_dir())
    return _repo_root() / layout.get("project_root", "projects")

def _project_path(project: str) -> Path:
    return _projects_root() / project

def _project_config_path(project: str) -> Path:
    return _project_path(project) / "config.json"

def _read_json_if(path: Path) -> Any | None:
    """S3-AWARE: Uses i_o.load_data to read from local or S3."""
    try:
        # i_o.load_data is S3-aware and returns None on FileNotFoundError
        return i_o.load_data(path)
    except Exception as e:
        # Catch other errors (e.g., JSONDecodeError, S3 permission issues)
        debug("JSON read failed at", path.as_posix(), "->", repr(e))
    return None

def _project_code_for(project: str) -> str | None:
    cfg_path = _project_config_path(project)
    # This now uses the S3-aware _read_json_if
    cfg = _read_json_if(cfg_path) or {}
    pc = cfg.get("project_code")
    val = pc.strip() if isinstance(pc, str) and pc.strip() else None
    debug("config: project_code_for", {"project": project, "cfg_path": cfg_path.as_posix(), "cfg_has_code": bool(pc), "resolved": val})
    return val

# ──────────────────────────────────────────────────────────────────────────────
# 0.1) JWT & cookie settings
# ──────────────────────────────────────────────────────────────────────────────

def _load_jwt_secret() -> str:
    """
    NOTE: This reads a LOCAL file from the repo root for dev secrets.
    This is server config, NOT project data, so it remains non-S3-aware.
    """
    env = os.environ.get("GIMS_JWT_SECRET")
    if env:
        return env
    root = _repo_root()
    secret_file = root / ".dev_jwt_secret"
    if secret_file.exists():
        try:
            return secret_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    val = "dev-" + uuid.uuid4().hex
    try:
        secret_file.write_text(val, encoding="utf-8")
    except Exception:
        pass
    return val

JWT_SECRET = _load_jwt_secret()
JWT_TTL = int(os.environ.get("GIMS_JWT_TTL_SECONDS", "3600"))

bearer = BearerTransport(tokenUrl="auth/jwt/login")

def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=JWT_SECRET, lifetime_seconds=JWT_TTL)

COOKIE_NAME = os.environ.get("GIMS_COOKIE_NAME", "gims_session")
COOKIE_SAMESITE = os.environ.get("GIMS_COOKIE_SAMESITE", "lax")
COOKIE_SECURE = os.environ.get("GIMS_COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes")

cookie_transport = CookieTransport(
    cookie_name=COOKIE_NAME,
    cookie_max_age=JWT_TTL,
    cookie_path="/",
    cookie_domain=None,
    cookie_secure=COOKIE_SECURE,
    cookie_httponly=True,
    cookie_samesite=COOKIE_SAMESITE,
)
debug("CookieTransport:", {"cookie_name": COOKIE_NAME, "secure": COOKIE_SECURE, "samesite": COOKIE_SAMESITE, "max_age": JWT_TTL})

jwt_backend = AuthenticationBackend(name="jwt", transport=bearer, get_strategy=get_jwt_strategy)
cookie_backend = AuthenticationBackend(name="cookie", transport=cookie_transport, get_strategy=get_jwt_strategy)

# ──────────────────────────────────────────────────────────────────────────────
# 1) Declarative Bases & Models
# ──────────────────────────────────────────────────────────────────────────────

class BaseLogin(DeclarativeBase): pass

class User(BaseLogin):
    __tablename__ = "users"
    id: Mapped[str]             = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    email: Mapped[str]          = mapped_column(String, unique=True, index=True, nullable=False)
    hashed_password: Mapped[str]= mapped_column(String, nullable=False)
    is_active: Mapped[bool]     = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool]  = mapped_column(Boolean, default=False, nullable=False)
    is_verified: Mapped[bool]   = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[str]     = mapped_column(String, default=lambda: datetime.utcnow().isoformat() + "Z")
    memberships: Mapped[List["AccountProject"]] = relationship(back_populates="user", cascade="all, delete-orphan")

class AccountProject(BaseLogin):
    __tablename__ = "accounts_projects"
    id: Mapped[str]        = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    user_id: Mapped[str]   = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    role_name: Mapped[str]    = mapped_column(String, index=True)
    created_at: Mapped[str]   = mapped_column(String, default=lambda: datetime.utcnow().isoformat() + "Z")
    user: Mapped[User] = relationship(back_populates="memberships")

class Project(BaseLogin):
    __tablename__ = "projects"
    id: Mapped[str]        = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    name: Mapped[str]      = mapped_column(String, index=True)
    project_code: Mapped[str] = mapped_column(String, unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=lambda: datetime.utcnow().isoformat() + "Z")

class BaseProject(DeclarativeBase): pass

class AuditLog(BaseProject):
    __tablename__ = "audit_log"
    id: Mapped[str]           = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    ts: Mapped[str]           = mapped_column(String, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    project: Mapped[str]      = mapped_column(String, index=True)
    action: Mapped[str]       = mapped_column(String, index=True)
    resource: Mapped[str]     = mapped_column(String, index=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip: Mapped[Optional[str]]          = mapped_column(String, nullable=True)
    user_agent: Mapped[Optional[str]]  = mapped_column(String, nullable=True)
    path: Mapped[Optional[str]]        = mapped_column(String, nullable=True)
    method: Mapped[Optional[str]]      = mapped_column(String, nullable=True)

class Task(BaseProject):
    __tablename__ = "tasks"
    id: Mapped[str]           = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    project: Mapped[str]      = mapped_column(String, index=True)
    kind: Mapped[str]         = mapped_column(String, index=True)
    status: Mapped[str]       = mapped_column(String, index=True, default="queued")
    created_by: Mapped[str]   = mapped_column(String, index=True)
    assigned_to: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    run_at: Mapped[Optional[str]]      = mapped_column(String, nullable=True)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    progress: Mapped[Optional[float]]   = mapped_column(Float, nullable=True)
    created_at: Mapped[str]    = mapped_column(String, default=lambda: datetime.utcnow().isoformat() + "Z")
    updated_at: Mapped[str]    = mapped_column(String, default=lambda: datetime.utcnow().isoformat() + "Z")

# FastAPI Users schemas + response models
class UserRead(BaseUser[str]): ...
class UserCreate(BaseUserCreate):
    password: str = Field(min_length=8)
class UserUpdate(BaseUserUpdate): ...

class MeResponse(BaseModel):
    user: UserRead
    roles: List[str]
    scopes: List[str] = []
    feature_tags: List[str] = []
    projects: List[str] = []        # human names
    project_codes: List[str] = []   # raw codes seen in memberships

class TaskRow(BaseModel):
    id: str
    project: str
    kind: str
    status: str
    created_by: str
    assigned_to: Optional[str] = None
    run_at: Optional[str] = None
    payload_json: Optional[dict[str, Any]] = None
    progress: Optional[float] = None
    created_at: str
    updated_at: str

# ──────────────────────────────────────────────────────────────────────────────
# 2) Engines & sessions (RDS-aware + SQLite PRAGMAs)
# ──────────────────────────────────────────────────────────────────────────────

_logins_engine: Any | None = None
_logins_sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None
_logins_schema_ready: bool = False

def _sqlite_pragmas_event(dbapi_conn, conn_record):
    """Apply safety PRAGMAs on every SQLite connection."""
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
    finally:
        cur.close()

def _effective_logins_uri() -> str:
    """
    Priority:
      1) explicit env LOGINS_DB_URI
      2) delegate to resolver.get_db_uri('logins_db')
    """
    env_uri = os.environ.get("LOGINS_DB_URI")
    if env_uri:
        return env_uri

    uri = get_db_uri("logins_db")
    if RDS_ENABLED and uri.startswith("sqlite+"):
        debug("WARNING: RDS is enabled but login DB is falling back to SQLite. "
              "Check local_layout_map.json for 'RDS+' prefix on 'logins_db'.")
    return uri

def _get_logins_engine():
    global _logins_engine
    if _logins_engine is not None:
        return _logins_engine

    uri = _effective_logins_uri()

    # Only mkdir in local sqlite mode
    if uri.startswith("sqlite+aiosqlite:///"):
        db_file = uri.removeprefix("sqlite+aiosqlite:///")
        Path(db_file).parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(
        uri,
        future=True,
        pool_pre_ping=True,
        echo=False,
        connect_args=({"timeout": 30, "check_same_thread": False} if uri.startswith("sqlite+aiosqlite") else {}),
    )

    if uri.startswith("sqlite+aiosqlite"):
        event.listen(engine.sync_engine, "connect", _sqlite_pragmas_event)

    _logins_engine = engine
    debug("Logins engine URI:", uri)
    return engine

async def _ensure_logins_schema() -> None:
    async with _get_logins_engine().begin() as conn:
        await conn.run_sync(BaseLogin.metadata.create_all)
    debug("Logins schema ensured")

_schema_lock = asyncio.Lock()

async def _ensure_logins_schema_once() -> None:
    global _logins_schema_ready
    if _logins_schema_ready:
        return
    async with _schema_lock:
        if _logins_schema_ready:
            return
        await _ensure_logins_schema()
        _logins_schema_ready = True

async def _logins_session() -> AsyncIterator[AsyncSession]:
    sm = _get_logins_sessionmaker()
    async with sm() as session:
        yield session

def _get_logins_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _logins_sessionmaker
    if _logins_sessionmaker:
        return _logins_sessionmaker
    _logins_sessionmaker = async_sessionmaker(bind=_get_logins_engine(), expire_on_commit=False)
    return _logins_sessionmaker

# PROJECT: nodes.db (per-project audit & tasks)
_proj_engine_cache: dict[str, Any] = {}
_proj_session_cache: dict[str, async_sessionmaker[AsyncSession]] = {}

def _effective_nodes_uri(project: str) -> str:
    """
    Priority:
      1) explicit env NODES_DB_URI
      2) delegate to resolver.get_db_uri('nodes_db')
    """
    env_uri = os.environ.get("NODES_DB_URI")
    if env_uri:
        return env_uri

    uri = get_db_uri("nodes_db", project=project)
    if RDS_ENABLED and uri.startswith("sqlite+"):
        debug(f"WARNING: RDS is enabled but nodes DB for '{project}' is falling back to SQLite. "
              "Check local_layout_map.json for 'RDS+' prefix on 'nodes_db'.")
    return uri

def _engine_for_project(project: str):
    if project in _proj_engine_cache:
        return _proj_engine_cache[project]

    uri = _effective_nodes_uri(project)

    # Only mkdir in local sqlite mode
    if uri.startswith("sqlite+aiosqlite:///"):
        db_file = uri.removeprefix("sqlite+aiosqlite:///")
        Path(db_file).parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(
        uri,
        future=True,
        pool_pre_ping=True,
        echo=False,
        connect_args=({"timeout": 30, "check_same_thread": False} if uri.startswith("sqlite+") else {}),
    )

    if uri.startswith("sqlite+"):
        event.listen(engine.sync_engine, "connect", _sqlite_pragmas_event)

    _proj_engine_cache[project] = engine
    debug("Project engine URI:", project, "->", uri)
    return engine

def _sessionmaker_for_project(project: str) -> async_sessionmaker[AsyncSession]:
    if project in _proj_session_cache:
        return _proj_session_cache[project]
    sm = async_sessionmaker(bind=_engine_for_project(project), expire_on_commit=False)
    _proj_session_cache[project] = sm
    return sm

async def _ensure_project_schema(project: str) -> None:
    async with _engine_for_project(project).begin() as conn:
        # Explicitly create only the audit_log table to avoid creating other tables
        # like 'tasks' if they are not needed in every project context.
        await conn.run_sync(
            lambda sync_conn: BaseProject.metadata.create_all(
                sync_conn, tables=[AuditLog.__table__], checkfirst=True
            )
        )
    debug("Project schema (audit_log only) ensured for:", project)

async def _project_session(project: str) -> AsyncIterator[AsyncSession]:
    await _ensure_project_schema(project)
    sm = _sessionmaker_for_project(project)
    async with sm() as session:
        yield session

# ──────────────────────────────────────────────────────────────────────────────
# 2.1) Self-healing migration for legacy audit_log FKs
# ──────────────────────────────────────────────────────────────────────────────

async def _maybe_migrate_audit_log(project: str) -> None:
    """
    Some early versions created audit_log with a FOREIGN KEY (e.g., to users).
    That FK breaks inserts because per-project DB has no users table.
    Detect any FK on audit_log and rebuild the table without FKs.
    
    RDS-AWARE: This migration logic is SQLite-specific (uses PRAGMA)
    and is now skipped on PostgreSQL/RDS to prevent syntax errors.
    """
    async for session in _project_session(project):
        try:
            # Check the dialect. Only run PRAGMA commands for SQLite.
            if not session.bind or session.bind.dialect.name != "sqlite":
                debug("audit_log migration: skipping PRAGMA checks for non-sqlite dialect", 
                      {"dialect": session.bind.dialect.name if session.bind else "unknown"})
                return # Exit the function, do not attempt migration

            fk_rows = (await session.execute(text("PRAGMA foreign_key_list(audit_log)"))).all()
            if not fk_rows:
                return  # already good
            debug("audit_log migration: foreign keys detected -> rebuilding", {"count": len(fk_rows)})

            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS audit_log_new (
                  id TEXT PRIMARY KEY,
                  ts TEXT,
                  user_id TEXT,
                  project TEXT,
                  action TEXT,
                  resource TEXT,
                  resource_id TEXT,
                  payload_json TEXT,
                  ip TEXT,
                  user_agent TEXT,
                  path TEXT,
                  method TEXT
                )
            """))

            cols = [r[1] for r in (await session.execute(text("PRAGMA table_info(audit_log)"))).all()]
            wanted = ["id","ts","user_id","project","action","resource","resource_id","payload_json","ip","user_agent","path","method"]
            copy_cols = [c for c in wanted if c in cols]
            col_list = ", ".join(copy_cols)
            await session.execute(text(f"INSERT OR IGNORE INTO audit_log_new ({col_list}) SELECT {col_list} FROM audit_log"))

            await session.execute(text("DROP TABLE audit_log"))
            await session.execute(text("ALTER TABLE audit_log_new RENAME TO audit_log"))
            await session.commit()
            debug("audit_log migration: done")
        except Exception as e:
            await session.rollback()
            debug("audit_log migration: failed/ignored:", repr(e))
        finally:
            break

# ──────────────────────────────────────────────────────────────────────────────
# 3) fastapi-users bindings — GLOBAL logins.db
# ──────────────────────────────────────────────────────────────────────────────

async def get_logins_db_dep(_: Request) -> AsyncIterator[SQLAlchemyUserDatabase]:
    # Don't create schema here - it should be done at startup only
    async for session in _logins_session():
        yield SQLAlchemyUserDatabase(session, User)

class UserManager(BaseUserManager[User, str]):
    """
    We use string IDs (TEXT in SQLite) instead of UUID objects to match your DB.
    """
    user_id_type = str

    def parse_id(self, user_id: Any) -> str:
        return str(user_id)

    reset_password_token_secret = JWT_SECRET
    verification_token_secret = JWT_SECRET

    async def _ensure_project_row(self, project_code: str, name_hint: Optional[str] = None):
        if not project_code:
            return
        async for session in _logins_session():
            try:
                stmt = (
                    sqlite_insert(Project)
                    .values(
                        id=uuid.uuid4().hex,
                        name=name_hint or project_code,
                        project_code=project_code,
                        description=None,
                        created_at=datetime.utcnow().isoformat() + "Z",
                    )
                    .prefix_with("OR IGNORE")
                )
                await session.execute(stmt)
                await session.commit()
                debug("Projects: ensured", {"code": project_code, "name": name_hint or project_code})
            except (IntegrityError, OperationalError) as e:
                await session.rollback()
                debug("Projects ensure ignored:", repr(e))

    async def on_after_register(self, user: User, request: Optional[Request] = None):
        # Ensure NEW accounts start unverified and create a pending membership (no role)
        debug("on_after_register:", user.email)

        # 1) Force is_verified = 0 (False) at DB level to avoid session-attachment issues
        try:
            async for session in _logins_session():
                await session.execute(
                    text("UPDATE users SET is_verified=0 WHERE id=:uid"),
                    {"uid": str(user.id)},
                )
                await session.commit()
                break
        except Exception as e:
            debug("on_after_register: force unverified failed:", repr(e))

        # 2) Resolve intended project_code from body/header/URL
        chosen = None
        proj_param = None
        if request:
            proj_param = request.path_params.get("project")
            try:
                body = await request.json()
            except Exception:
                body = {}
            chosen = body.get("project_code") or request.headers.get("X-Project-Code")
            if not chosen and proj_param:
                # This now uses the S3-aware function
                chosen = _project_code_for(proj_param) 

        # 3) Ensure global Project row exists (registry), then insert pending membership (no role)
        if chosen:
            chosen = str(chosen)
            await self._ensure_project_row(chosen, name_hint=proj_param or chosen)

            async for session in _logins_session():
                proj_id = (await session.execute(
                    select(Project.id).where(Project.project_code == chosen)
                )).scalars().first()

                if proj_id:
                    stmt = (
                        sqlite_insert(AccountProject)
                        .values(
                            id=uuid.uuid4().hex,
                            user_id=str(user.id),
                            project_id=str(proj_id),
                            role_name="",  # pending (unassigned)
                            created_at=datetime.utcnow().isoformat() + "Z",
                        )
                        .prefix_with("OR IGNORE")
                    )
                    await session.execute(stmt)
                    await session.commit()
                    debug("on_after_register: pending membership created", {
                        "user_id": str(user.id), "project_id": str(proj_id), "role_name": ""
                    })

        # 4) Audit to the per-project audit log (if the path had {project})
        if request and proj_param:
            await _maybe_migrate_audit_log(proj_param)
            try:
                await _audit(
                    request,
                    user_id=user.id,
                    project=proj_param,
                    action="register",
                    resource="user",
                    resource_id=str(user.id),
                    payload={"project_code": chosen},
                )
            except IntegrityError as e:
                debug("audit insert failed once (register); migrating & retrying:", repr(e))
                await _maybe_migrate_audit_log(proj_param)
                await _audit(
                    request,
                    user_id=user.id,
                    project=proj_param,
                    action="register",
                    resource="user",
                    resource_id=str(user.id),
                    payload={"project_code": chosen},
                )

    async def on_after_login(self, user: User, request: Optional[Request] = None, response: Optional[Response] = None):
        debug("on_after_login:", user.email)
        if request and request.path_params.get("project"):
            await _maybe_migrate_audit_log(request.path_params["project"])
            await _audit(
                request, user_id=user.id, project=request.path_params["project"],
                action="login", resource="user", resource_id=str(user.id), payload=None
            )

    async def on_after_logout(self, user: User, request: Optional[Request] = None, response: Optional[Response] = None):
        debug("on_after_logout:", (user.email if user else None))
        if request and request.path_params.get("project"):
            await _maybe_migrate_audit_log(request.path_params["project"])
            await _audit(
                request, user_id=(user.id if user else None), project=request.path_params["project"],
                action="logout", resource="user", resource_id=(str(user.id) if user else None), payload=None
            )

async def get_user_manager(db=Depends(get_logins_db_dep)):
    yield UserManager(db)

fastapi_users = FastAPIUsers[User, str](
    get_user_manager,
    [jwt_backend, cookie_backend],
)
current_active_user = fastapi_users.current_user(active=True)

# ──────────────────────────────────────────────────────────────────────────────
# 4) Roles & permissions — roles.json + global membership
# ──────────────────────────────────────────────────────────────────────────────

def _load_roles_json(project: str) -> dict:
    """S3-AWARE: Uses i_o.load_data to read roles schema."""
    roles_path = resolve_path(_project_path(project), "roles_schema")
    debug("roles: locate", {"project": project, "path": roles_path.as_posix()})
    try:
        # i_o.load_data is S3-aware and handles FileNotFoundError by returning None
        data = i_o.load_data(roles_path)
        
        if data is None:
            debug("roles: missing file -> empty")
            return {"roles": []}

        cnt = len([r for r in data.get("roles", []) if isinstance(r, dict)])
        debug("roles: parsed", {"roles_count": cnt})
        return data
    except Exception as e:
        # Catch other errors (e.g., JSONDecodeError, S3 permissions)
        debug("roles: parse error", repr(e))
        return {"roles": []}

def _roles_index(roles_json: dict) -> dict[str, dict]:
    return {r["name"]: r for r in roles_json.get("roles", []) if "name" in r}

async def _resolve_project_code(project_id: str) -> Optional[str]:
    debug("project_code: resolve: begin", {"project_id": project_id})
    async for session in _logins_session():
        stmt = select(Project.project_code).where(
            (Project.name == project_id) | (Project.project_code == project_id)
        )
        res = await session.execute(stmt)
        code = res.scalars().first()
    debug("project_code: resolve: end", {"project_id": project_id, "project_code": code})
    return code

async def _user_roles_for_project(user_id: str, project_name: str) -> List[str]:
    if not project_name:
        debug("roles_for_project: no project_name -> []", {"user_id": user_id})
        return []

    async for session in _logins_session():
        proj_id = (await session.execute(
            select(Project.id).where(Project.name == project_name)
        )).scalars().first()
        if not proj_id:
            debug("roles_for_project: project not found", {"project": project_name})
            return []

        stmt = select(AccountProject.role_name).where(
            and_(AccountProject.user_id == user_id, AccountProject.project_id == proj_id)
        )
        debug("roles_for_project: query", {"user_id": user_id, "project_id": proj_id, "project_name": project_name})
        rows = (await session.execute(stmt)).scalars().all()

    roles = [r for r in rows if r]
    debug("roles_for_project: result", {"user_id": user_id, "project": project_name, "roles": roles})
    return roles

def _merge_scopes_tags(roles_json: dict, role_names: Iterable[str]) -> Tuple[List[str], List[str]]:
    idx = _roles_index(roles_json)
#    debug("scopes_tags: merge: begin", {"role_names": list(role_names)})
    scopes, tags = [], []
    for rn in role_names:
        r = idx.get(rn) or {}
        added_s = list(r.get("scopes", []))
        added_t = list(r.get("feature_tags", []))
#        debug("scopes_tags: role detail", {"role": rn, "scopes": added_s, "tags": added_t})
        scopes += added_s
        tags   += added_t
    scopes = list(dict.fromkeys(scopes))
    tags   = list(dict.fromkeys(tags))
#    debug("scopes_tags: merge: end", {"scopes": scopes, "tags": tags})
    return scopes, tags

def _wildcard_match(scope: str, needed: str) -> bool:
    so = (scope.split(":") + ["*","*","*"])[:3]
    ne = (needed.split(":") + ["*","*","*"])[:3]
    def m(a,b): return a == b or a == "*" or b == "*"
    return m(so[0], ne[0]) and m(so[1], ne[1]) and m(so[2], ne[2])

def require_scopes(*needed: str):
    """
    Authorize by EITHER classic 3-part scopes OR feature tags.
    Enforces: non-superusers must be verified (is_verified=1).
    Superusers bypass all checks automatically.
    """
    async def _dep(request: Request, user: User = Depends(current_active_user)) -> User:
        project = request.path_params.get("project")
        debug("authz: begin", {"needed": needed, "project": project, "user": getattr(user, "email", None)})

        if not project:
            debug("authz: missing project in path")
            raise HTTPException(400, "Project required in path")

        # Superuser bypass
        if user.is_superuser:
            debug("authz: superuser bypass")
            return user

        # Enforce verification for all non-superusers
        if not user.is_verified:
            debug("authz: deny – account not verified")
            raise HTTPException(status_code=403, detail="Account pending verification")

        project_code = await _resolve_project_code(project)
        roles_json = _load_roles_json(project)  # S3-aware read
        role_names = await _user_roles_for_project(user.id, project)
        s_role, t_role = _merge_scopes_tags(roles_json, role_names)
        all_scopes = list(dict.fromkeys(s_role or []))
        all_tags = set(t_role or [])
        debug("authz: assembled", {
            "project_code": project_code,
            "role_names": role_names,
            "scopes": all_scopes,
            "tags": sorted(all_tags),
        })

        checks = [n.replace("{project}", project) for n in (needed or ())]
        if not checks:
            debug("authz: no checks -> allow")
            return user

        scope_needs, tag_needs = [], []
        for c in checks:
            if c.startswith(("module:", "noun:", "verb:", "tag:")):
                tag_needs.append(c[4:] if c.startswith("tag:") else c)
            else:
                scope_needs.append(c)
        debug("authz: needs split", {"scope_needs": scope_needs, "tag_needs": tag_needs})

        scope_ok = bool(scope_needs) and any(_wildcard_match(s, need) for s in all_scopes for need in scope_needs)
        tag_ok   = bool(tag_needs)   and any(t in all_tags for t in tag_needs)
        debug("authz: evaluate", {"scope_ok": scope_ok, "tag_ok": tag_ok})

        if scope_ok or tag_ok:
            debug("authz: allow")
            return user

        debug("authz: deny")
        raise HTTPException(status_code=4403, detail="Missing permission")
    return _dep

def require_login():
    return require_scopes()

def require_feature_tags(*tags: str):
    normalized = []
    for t in tags:
        if t.startswith(("module:", "noun:", "verb:", "tag:")):
            normalized.append(t)
        else:
            normalized.append("tag:" + t)
    return require_scopes(*normalized)

# ──────────────────────────────────────────────────────────────────────────────
# 5) Audit Helper (per-project nodes.db)
# ──────────────────────────────────────────────────────────────────────────────

# NEW: helper to fetch comma-separated list of *project names* for the user
async def _project_names_for_user(user_id: Optional[Union[str, uuid.UUID]]) -> list[str]:
    if not user_id:
        return []
    uid = str(user_id)
    async for session in _logins_session():
        proj_ids = (await session.execute(
            select(AccountProject.project_id).where(AccountProject.user_id == uid)
        )).scalars().all()
        proj_ids = [p for p in proj_ids if p]
        if not proj_ids:
            return []
        names = (await session.execute(
            select(Project.name).where(Project.id.in_(proj_ids))
        )).scalars().all()
        # de-dup preserving order
        seen: Set[str] = set()
        out: List[str] = []
        for n in names:
            if n and n not in seen:
                seen.add(n)
                out.append(n)
        return out
    return []

async def _audit(
    request: Request,
    *,
    user_id: Optional[Union[str, uuid.UUID]],
    project: str,
    action: str,
    resource: str,
    resource_id: Optional[str],
    payload: Optional[dict],
) -> None:
    """Write an audit row into the per-project DB *for the URL project*, but store
    the user's full membership list as a CSV string in the `project` column."""
    # Build CSV of the user's project *names*
    names = await _project_names_for_user(user_id)
    projects_csv = ", ".join(names) if names else ""

    uid_str = str(user_id) if isinstance(user_id, (uuid.UUID, str)) else None

    async for session in _project_session(project):
        rec = AuditLog(
            ts=datetime.utcnow().isoformat() + "Z",
            user_id=uid_str,
            project=projects_csv,     # ← the requested change
            action=action,
            resource=resource,
            resource_id=resource_id,
            payload_json=json.dumps(payload or {}),
#            ip=(request.client.host if request.client else None),
#            user_agent=request.headers.get("user-agent"),
            ip="disabled I fear",
            user_agent="disabled I fear",
            path=str(request.url.path),
            method=request.method,
        )
        session.add(rec)
        try:
            await session.commit()
        except IntegrityError as e:
            await session.rollback()
            debug("Audit insert IntegrityError:", repr(e))
            raise
    debug("Audit:", action, resource, "uid=", uid_str)

# ──────────────────────────────────────────────────────────────────────────────
# 6) Auth & helper routes (mounted under /login/{project}/…)
# ──────────────────────────────────────────────────────────────────────────────

project_router = APIRouter(prefix="/{project}")

# Expose BOTH transports:
project_router.include_router(fastapi_users.get_auth_router(jwt_backend), prefix="/auth/jwt",    tags=["Auth"])
project_router.include_router(fastapi_users.get_auth_router(cookie_backend), prefix="/auth/cookie", tags=["Auth"])
project_router.include_router(fastapi_users.get_register_router(UserRead, UserCreate), prefix="/auth", tags=["Auth"])
project_router.include_router(fastapi_users.get_users_router(UserRead, UserUpdate),   prefix="/users", tags=["Users"])

# CSRF (double-submit) helper
CSRF_COOKIE_NAME = os.environ.get("GIMS_CSRF_COOKIE_NAME", "gims_csrf")
CSRF_TTL_SECONDS = int(os.environ.get("GIMS_CSRF_TTL_SECONDS", "7200"))
CSRF_SAMESITE = os.environ.get("GIMS_CSRF_SAMESITE", "lax")

@project_router.get("/csrf", tags=["Auth"])
async def get_csrf(request: Request, response: Response):
    project = request.path_params["project"]
    token = secrets.token_urlsafe(32)
    response.set_cookie(
        key=CSRF_COOKIE_NAME, value=token, max_age=CSRF_TTL_SECONDS,
        secure=COOKIE_SECURE, httponly=True, samesite=CSRF_SAMESITE, path="/",
    )
    debug("Issued CSRF for project:", project, "path:/")
    return {"csrf": token}

def _mask(tok: Optional[str]) -> str:
    if not tok: return ""
    tok = str(tok)
    return tok[:8] + "…" + tok[-6:] if len(tok) > 18 else "…"

def _bearer_from_headers(request: Request) -> Optional[str]:
    for name in ("authorization", "x-forwarded-authorization"):
        v = request.headers.get(name)
        if not v: continue
        v = v.strip()
        if v.lower().startswith("bearer "):
            return v.split(" ", 1)[1]
        return v
    return None

async def _user_from_token_or_cookie(request: Request) -> Optional[User]:
    candidates: list[str] = []
    tok = _bearer_from_headers(request)
    cookie_jwt = request.cookies.get(COOKIE_NAME)

    debug("me: auth header present?", bool(tok), "cookie present?", bool(cookie_jwt))
    if tok: candidates.append(tok)
    if cookie_jwt: candidates.append(cookie_jwt)
    if not candidates:
        debug("me: no auth candidates; headers:", {
            "auth": bool(request.headers.get("authorization")),
            "xfwd-auth": bool(request.headers.get("x-forwarded-authorization")),
            "cookie_names": list(request.cookies.keys())
        })
        return None

    strategy = get_jwt_strategy()

    async for session in _logins_session():
        db = SQLAlchemyUserDatabase(session, User)
        for t in candidates:
            try:
                payload = strategy.read_token(t)
                debug("me: token OK; payload keys:", list((payload or {}).keys()))
            except Exception as e:
                debug("me: token decode failed:", type(e).__name__)
                continue
            sub = (payload or {}).get("sub") or (payload or {}).get("user_id")
            if not sub:
                debug("me: payload missing sub/user_id")
                continue
            uid = str(sub)
            user = await db.get(uid)
            if user and user.is_active:
                debug("me: resolved user", user.email)
                return user
            debug("me: user not found or inactive; sub:", sub)
    return None

@project_router.get("/auth/me", response_model=MeResponse, tags=["Auth"])
async def me(request: Request, user_dep: Optional[User] = Depends(fastapi_users.current_user(optional=True))):
    debug("me: begin")
    user = user_dep or (await _user_from_token_or_cookie(request))
    if not user:
        debug("me: no user after all strategies - 401")
        raise HTTPException(status_code=401, detail="Unauthorized")

    project = request.path_params["project"]
    debug("me: project", {"project": project, "user": user.email})

    # READ-ONLY: do NOT write to logins.db here.
    project_code = await _resolve_project_code(project)
    roles_json = _load_roles_json(project) # S3-aware

    # If not superuser AND not verified -> report no roles/scopes/tags (UI shows pending)
    if (not user.is_superuser) and (not user.is_verified):
        debug("me: unverified -> suppress roles/scopes/tags")
        async for session in _logins_session():
            stmt = select(AccountProject.project_id).where(AccountProject.user_id == user.id)
            proj_ids = (await session.execute(stmt)).scalars().all()
            proj_ids = [p for p in proj_ids if p]
            regs = (await session.execute(select(Project).where(Project.id.in_(proj_ids)))).scalars().all() if proj_ids else []
        name_for = {r.id: r.name for r in regs}
        code_for = {r.id: r.project_code for r in regs}
        projects_names = sorted({ name_for.get(pid, pid) for pid in proj_ids })
        projects_codes = sorted({ code_for.get(pid, "") for pid in proj_ids if code_for.get(pid, "") })
        return MeResponse(
            user=UserRead.model_validate(user.__dict__),
            roles=[],
            scopes=[],
            feature_tags=[],
            projects=projects_names,
            project_codes=projects_codes,
        )

    # Normal flow (superuser OR verified user)
    role_names = await _user_roles_for_project(user.id, project)
    debug("me: role_names", role_names)
    s_role, t_role = _merge_scopes_tags(roles_json, role_names)
    scopes = sorted(set(s_role))
    tags   = sorted(set(t_role))
    plain  = {t.split(":", 1)[-1] for t in tags if ":" in t}
    tags_out = sorted(set(tags) | plain)
#    debug("me: scopes/tags", {"scopes": scopes, "tags": tags_out})

    async for session in _logins_session():
        stmt = select(AccountProject.project_id).where(AccountProject.user_id == user.id)
        proj_ids = (await session.execute(stmt)).scalars().all()
        proj_ids = [p for p in proj_ids if p]
        regs = (await session.execute(select(Project).where(Project.id.in_(proj_ids)))).scalars().all() if proj_ids else []

    name_for: dict[str, str] = {r.id: r.name for r in regs}
    code_for: dict[str, str] = {r.id: r.project_code for r in regs}
    projects_names = sorted({ name_for.get(pid, pid) for pid in proj_ids })
    projects_codes = sorted({ code_for.get(pid, "") for pid in proj_ids if code_for.get(pid, "") })

#    debug("me: final", {
#        "user": user.email,
#        "project": project,
#        "project_code": project_code,
#        "roles": role_names,
#        "scopes": scopes,
#        "tags": tags_out,
#        "projects_names": projects_names,
#        "projects_codes": projects_codes,
#    })

    return MeResponse(
        user=UserRead.model_validate(user.__dict__),
        roles=role_names,
        scopes=scopes,
        feature_tags=tags_out,
        projects=projects_names,
        project_codes=projects_codes
    )

# (Optional) Task viewers — per-project DB
@project_router.get("/tasks/my", response_model=List[TaskRow])
async def my_tasks(request: Request, user: User = Depends(current_active_user)):
    project = request.path_params["project"]

    async for session in _project_session(project):
        rows = (await session.execute(
            Task.__table__.select().where(Task.assigned_to == str(user.id)).order_by(Task.updated_at.desc())
        )).all()
    out: List[TaskRow] = []
    for r in rows:
        rec = r[0]
        out.append(TaskRow(
            id=rec.id, project=rec.project, kind=rec.kind, status=rec.status,
            created_by=str(rec.created_by), assigned_to=str(rec.assigned_to) if rec.assigned_to else None,
            run_at=rec.run_at, payload_json=json.loads(rec.payload_json) if rec.payload_json else None,
            progress=rec.progress, created_at=rec.created_at, updated_at=rec.updated_at
        ))
    return out

@project_router.get("/tasks/admin", response_model=List[TaskRow],
            dependencies=[Depends(require_scopes("*:tasks:view"))])
async def admin_tasks(request: Request):
    project = request.path_params["project"]

    async for session in _project_session(project):
        rows = (await session.execute(
            Task.__table__.select().where(Task.project == project).order_by(Task.updated_at.desc())
        )).all()
    out: List[TaskRow] = []
    for r in rows:
        rec = r[0]
        out.append(TaskRow(
            id=rec.id, project=rec.project, kind=rec.kind, status=rec.status,
            created_by=str(rec.created_by), assigned_to=str(rec.assigned_to) if rec.assigned_to else None,
            run_at=rec.run_at, payload_json=json.loads(rec.payload_json) if rec.payload_json else None,
            progress=rec.progress, created_at=rec.created_at, updated_at=rec.updated_at
        ))
    return out

@router.get("/csrf", tags=["Auth"], summary="Get a project-agnostic CSRF token")
async def get_csrf_global(response: Response):
    """Issues a CSRF token as a cookie, for use by UIs before a project is known."""
    token = secrets.token_urlsafe(32)
    response.set_cookie(
        key=CSRF_COOKIE_NAME, value=token, max_age=CSRF_TTL_SECONDS,
        secure=COOKIE_SECURE, httponly=True, samesite=CSRF_SAMESITE, path="/",
    )
    debug("Issued global CSRF token")
    return {"csrf": token}

@router.get("/auth/me", response_model=MeResponse, tags=["Auth"], summary="Get current user and all their project memberships")
async def me_global(user: User = Depends(fastapi_users.current_user(active=True))):
    """
    Provides information about the current authenticated user, including a list of all
    projects they are a member of. This endpoint is project-agnostic and is intended
    for use on pages like the main launcher. Since no single project is in context,
    roles and scopes will be empty.
    """
    debug("me_global: begin for user", user.email)

    async for session in _logins_session():
        # Get all project_ids the user is a member of
        stmt = select(AccountProject.project_id).where(AccountProject.user_id == user.id)
        proj_ids = (await session.execute(stmt)).scalars().all()
        proj_ids = list(set(p for p in proj_ids if p))
        regs = []
        if proj_ids:
            # Get the project details (name, code) for those IDs
            regs = (await session.execute(select(Project).where(Project.id.in_(proj_ids)))).scalars().all()

    # Create the lists of names and codes from the user's memberships
    projects_names = sorted([r.name for r in regs])
    projects_codes = sorted([r.project_code for r in regs])
    debug("me_global: found projects", {"names": projects_names, "codes": projects_codes})

    return MeResponse(
        user=UserRead.model_validate(user.__dict__),
        roles=[],
        scopes=[],
        feature_tags=[],
        projects=projects_names,
        project_codes=projects_codes
    )

router.include_router(project_router)

# ──────────────────────────────────────────────────────────────────────────────
# 7) Headless auth helper (inject) + State Dock tab
# ──────────────────────────────────────────────────────────────────────────────

INJECT_JS = r"""
(function () {
  if (!window.GIMS) window.GIMS = {};
  if (!window.GIMS.authReady) {
    let _resolve; window.GIMS.authReady = new Promise(res => { _resolve = res; });
    window.GIMS.__resolveAuthReady = _resolve;
  }
  window.GIMS.csrfToken = null;

  window.GIMS.__applyAuthMe = function(me){
    const tags = Array.isArray(me?.feature_tags) ? me.feature_tags : [];
    window.__featureTags = tags;
    try { localStorage.setItem("gims_feature_tags", JSON.stringify(tags)); } catch {}
    if (window.GIMS.__resolveAuthReady) { window.GIMS.__resolveAuthReady(); window.GIMS.__resolveAuthReady = null; }
    const allowAll = !!me?.user?.is_superuser;
    const allowed = new Set(tags || []);
    document.querySelectorAll("[data-tag]").forEach(el=>{
      const tag = el.getAttribute("data-tag");
      const ok = allowAll || !tag || allowed.has(tag);
      el.style.display = ok ? "" : "none";
    });
  };

  window.GIMS.getCsrfHeaders = function(){
    return window.GIMS.csrfToken ? { "X-CSRF-Token": window.GIMS.csrfToken } : {};
  };

  // Project-agnostic base; endpoints are /login/… (no project in path)
  const base = "/login";

  async function refreshSession() {
    const tok = localStorage.getItem("gims_token");
    if (!tok) {
      window.GIMS.__applyAuthMe({ feature_tags: [], user: null });
      return;
    }
    const res = await fetch(base + "/auth/me", { headers: { "Authorization": "Bearer " + tok } });
    if (!res.ok) {
      localStorage.removeItem("gims_token");
      return refreshSession();
    }
    const me = await res.json();
    window.GIMS.__applyAuthMe(me);
  }

  async function refreshCsrf() {
    try {
      // Try global CSRF route first
      let res = await fetch(base + "/csrf", { method: "GET", credentials: "include" });

      // If global route fails (e.g., 404), retry with project-specific path
      if (!res.ok) {
        const project = window.GIMS_PROJECT || "LIMS-System";
        res = await fetch(base + "/" + encodeURIComponent(project) + "/csrf", {
          method: "GET",
          credentials: "include"
        });
      }

      if (!res.ok) return;

      const data = await res.json().catch(() => null);
      if (data && data.csrf) window.GIMS.csrfToken = data.csrf;
    } catch (_) {
      console.warn("refreshCsrf: failed", _);
    }
  }

  refreshCsrf();
  refreshSession();

  window.GIMS.authRefresh = async function(){
    await refreshCsrf();
    await refreshSession();
  };
})();
"""

STATE_TAB_JS = r"""
/* Renders on mount/show, handles login/logout state changes reliably. */
(function(){
  function whenDock(cb){
    if (window.StateDock) return cb();
    const t = setInterval(()=>{ if (window.StateDock){ clearInterval(t); cb(); } }, 50);
  }
  (function ensureAuthGlobals(){
    if (!window.GIMS) window.GIMS = {};
    if (!window.GIMS.authReady) {
      let resolveReady;
      window.GIMS.authReady = new Promise(res => { resolveReady = res; });
      window.GIMS.__resolveAuthReady = resolveReady;
    }
    if (!window.GIMS.__applyAuthMe) {
      window.GIMS.__applyAuthMe = function(me){
        const tags = Array.isArray(me?.feature_tags) ? me.feature_tags : [];
        window.__featureTags = tags;
        try { localStorage.setItem("gims_feature_tags", JSON.stringify(tags)); } catch {}
        if (window.GIMS.__resolveAuthReady) { window.GIMS.__resolveAuthReady(); window.GIMS.__resolveAuthReady = null; }
        const allowAll = !!me?.user?.is_superuser;
        const allowed = new Set(tags || []);
        document.querySelectorAll("[data-tag]").forEach(el=>{
          const tag = el.getAttribute("data-tag");
          const ok = allowAll || !tag || allowed.has(tag);
          el.style.display = ok ? "": "none";
        });
      };
    }
  })();

  function el(tag, attrs, kids){
    const n = document.createElement(tag);
    if (attrs) for (const [k,v] of Object.entries(attrs)){
      if (k === "class") n.className = v;
      else if (k === "html") n.innerHTML = v;
      else if (v != null) n.setAttribute(k, v);
    }
    (Array.isArray(kids)?kids:[kids]).forEach(c=>{
      if (c==null) return;
      if (typeof c === "string") n.appendChild(document.createTextNode(c));
      else n.appendChild(c);
    });
    return n;
  }

  function chip(text, cls){
    return el("span", {class:"sd-chip " + (cls||""), style:"display:inline-block;border:1px solid rgba(255,255,255,.12);border-radius:999px;padding:2px 8px;margin:2px;font-size:11px;background:#232323"}, [text]);
  }

  function kv(label, value){
    const row = document.createElement("div");
    row.setAttribute("class","sd-row");
    row.setAttribute("style","margin:4px 0");
    const a = document.createElement("span");
    a.style.opacity = ".7";
    a.style.marginRight = "6px";
    a.textContent = label + ":";
    const b = document.createElement("span");
    b.setAttribute("class","sd-val");
    b.textContent = value || "—";
    row.append(a,b);
    return row;
  }

  function detectProject(){
    if (window.GIMS_PROJECT) return window.GIMS_PROJECT;
    const m = location.pathname.match(/\/([A-Za-z0-9._-]+)\//);
    return (m && m[1]) || "LIMS-System";
  }

  async function login(base, email, password){
    const res = await fetch(base + "/auth/jwt/login", {
      method: "POST",
      headers: {"Content-Type":"application/x-www-form-urlencoded"},
      body: new URLSearchParams({username: email, password})
    });
    if (!res.ok) throw new Error(await res.text() || "Login failed");
    const data = await res.json();
    localStorage.setItem("gims_token", data.access_token);
    return data;
  }

  async function register(base, email, password, projectCode){
    // Send project code in header to avoid schema validation on body
    const res = await fetch(base + "/auth/register", {
      method: "POST",
      headers: {
        "Content-Type":"application/json",
        "X-Project-Code": String(projectCode || "").trim()
      },
      body: JSON.stringify({email, password})
    });
    if (!res.ok) throw new Error(await res.text() || "Register failed");
    return login(base, email, password);
  }

  async function getMe(base){
    const tok = localStorage.getItem("gims_token");
    const headers = tok ? {"Authorization": "Bearer " + tok} : {};
    const res = await fetch(base + "/auth/me", { headers, credentials: "include" });
    if (!res.ok) return null;
    return res.json();
  }

  whenDock(function(){
    function accordion(title, count){
      const box = document.createElement("div");
      box.setAttribute("class","acc");
      const caret = document.createElement("span");
      caret.setAttribute("class","caret");
      caret.style.marginRight="6px";
      caret.style.display="inline-block";
      caret.style.transform="rotate(0deg)";
      caret.textContent="▸";
      const h = document.createElement("div");
      h.setAttribute("class","acc-h");
      h.title="Toggle";
      const hwrap = document.createElement("div");
      hwrap.style.display="flex";
      hwrap.style.alignItems="center";
      hwrap.append(caret, Object.assign(document.createElement("strong"), {textContent: title}));
      const countEl = Object.assign(document.createElement("span"), {textContent: String(count||0)});
      countEl.setAttribute("class","acc-count");
      countEl.style.opacity=".7";
      h.append(hwrap, countEl);
      const b = document.createElement("div");
      b.setAttribute("class","acc-b");
      h.addEventListener("click", ()=>{
        const open = box.classList.toggle("open");
        caret.style.transform = open ? "rotate(90deg)" : "rotate(0deg)";
      });
      box.append(h,b);
      return {box, body:b, header:h};
    }

    const styleId = "login-profile-inline-css";
    if (!document.getElementById(styleId)) {
      const s = document.createElement("style");
      s.id = styleId;
      s.textContent = `
        .lp-row{display:grid;gap:6px;margin:6px 0}
        .lp-row input{width:100%;background:#151515;color:#eee;border:1px solid rgba(255,255,255,.15);border-radius:8px;padding:8px}
        .lp-actions{display:flex;gap:8px;margin-top:8px;justify-content:flex-end}
        .lp-btn{all:unset;cursor:pointer;padding:6px 10px;border-radius:8px;border:1px solid rgba(255,255,255,.2);background:#252525}
        .lp-btn:hover{background:#2f2f2f}
        .lp-note{font-size:12px;opacity:.75}
        .lp-scroll{max-height:min(70vh, 560px);overflow:auto;padding-right:6px}
        .acc{border:1px solid rgba(255,255,255,.12);border-radius:10px;margin:8px 0;background:#1b1b1b}
        .acc-h{display:flex;justify-content:space-between;align-items:center;padding:6px 10px;cursor:pointer}
        .acc-b{display:none;padding:8px 10px;border-top:1px solid rgba(255,255,255,.08)}
        .acc.open .acc-b{display:block}
        .sd-chip.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
        .lp-warn{margin-top:8px;padding:8px;border-radius:8px;border:1px solid rgba(255,200,0,.25);background:#2a2200;color:#ffd166}
      `;
      document.head.appendChild(s);
    }

    window.StateDock.registerTabProvider({
      id: "login-profile",
      title: "Profile",
      icon: "👤",
      async mount(root){
        const project = detectProject();
        const base = "/login/" + encodeURIComponent(project);

        const header = document.createElement("div");
        header.setAttribute("style","display:flex;align-items:center;justify-content:space-between;margin-bottom:6px");
        header.appendChild(document.createElement("strong")).textContent = "Profile";

        const body = document.createElement("div");
        body.setAttribute("class","lp-scroll");
        body.setAttribute("style","font-size:12px;opacity:.96");

        root.innerHTML = "";
        root.append(header, body);

        async function render(){
          body.innerHTML = "Loading…";
          let me = null;
          try { me = await getMe(base); } catch(e) { body.innerHTML = "Error loading profile."; return; }
          body.innerHTML = "";

          if (!me){
            const form = document.createElement("div");
            form.setAttribute("class","lp-form");
            form.innerHTML = `
              <div class="lp-row"><label>Email</label><input id="lp-email" type="email" placeholder="you@example.com" autocomplete="username"></div>
              <div class="lp-row"><label>Password</label><input id="lp-pass" type="password" placeholder="••••••••" autocomplete="new-password"></div>
              <div class="lp-row" id="lp-project-row" style="display:none">
                <label>Project Code</label><input id="lp-project" type="text" placeholder="e.g., LIMS-System" autocomplete="off">
              </div>
              <div class="lp-actions">
                <button class="lp-btn" id="lp-register">Register</button>
                <button class="lp-btn" id="lp-login">Sign in</button>
              </div>
              <div class="lp-note" id="lp-note-default">Sign in to see roles and feature tags.</div>
              <div class="lp-note" id="lp-note-register" style="display:none">Enter a <strong>Project Code</strong> to associate your email, then click <em>Submit Registration</em>. An admin may need to verify your account.</div>
            `;
            body.append(form);

            const emailEl = form.querySelector("#lp-email");
            const passEl  = form.querySelector("#lp-pass");
            const projRow = form.querySelector("#lp-project-row");
            const projEl  = form.querySelector("#lp-project");
            const noteDefault  = form.querySelector("#lp-note-default");
            const noteRegister = form.querySelector("#lp-note-register");
            const btnRegister  = form.querySelector("#lp-register");

            form.querySelector("#lp-login").addEventListener("click", async (e)=>{
              e.target.disabled = true;
              const email = emailEl.value.trim(), pass = passEl.value;
              if (!email || !pass) { alert("Email and password required"); e.target.disabled = false; return; }
              try {
                await login(base, email, pass);
                await window.GIMS.authRefresh();
                await render();
              } catch(err){
                alert(err.message || "Login failed");
                e.target.disabled = false;
              }
            });

            btnRegister.addEventListener("click", async (e)=>{
              e.preventDefault();
              const stage = btnRegister.dataset.stage || "start";
              if (stage === "start") {
                projRow.style.display = "";
                noteDefault.style.display = "none";
                noteRegister.style.display = "";
                btnRegister.textContent = "Submit Registration";
                btnRegister.dataset.stage = "confirm";
                projEl.focus();
                return;
              }
              const email = emailEl.value.trim();
              const pass  = passEl.value;
              const proj  = (projEl.value || "").trim();
              if (!email || !pass || !proj) { alert("Email, password, and project code are required"); return; }
              btnRegister.disabled = True
              try {
                await register(base, email, pass, proj);
                await window.GIMS.authRefresh();
                await render();
              } catch(err){
                alert(err.message || "Register failed");
                btnRegister.disabled = false;
              }
            });

            return;
          }

          const u = me.user || {};
          const actions = document.createElement("div");
          actions.setAttribute("class","lp-actions");
          actions.setAttribute("style","position:sticky;top:0;background:linear-gradient(#111, #111 80%, transparent);padding-top:4px;margin-top:-4px");
          const btnRefresh = document.createElement("button");
          btnRefresh.setAttribute("class","lp-btn");
          btnRefresh.id="lp-refresh";
          btnRefresh.textContent="Refresh";
          const btnLogout = document.createElement("button");
          btnLogout.setAttribute("class","lp-btn");
          btnLogout.id="lp-logout";
          btnLogout.textContent="Sign out";
          actions.append(btnRefresh, btnLogout);

          body.append(
            kv("Email", u.email || "—"),
            kv("Verified", String(!!u.is_verified)),
            kv("Superuser", String(!!u.is_superuser)),
            kv("Projects", Array.isArray(me.projects) && me.projects.length ? me.projects.join(", ") : "—"),
            actions
          );

          if (!u.is_verified && !u.is_superuser) {
            const warn = document.createElement("div");
            warn.setAttribute("class","lp-warn");
            warn.textContent = "Your account is pending verification by a project admin. You can sign in, but features remain limited until verified.";
            body.appendChild(warn);
          }

          const roles = Array.isArray(me.roles) ? me.roles.slice().sort() : [];
          const scopes = Array.isArray(me.scopes) ? me.scopes.slice().sort() : [];

          const acc1 = document.createElement("div");
          acc1.setAttribute("class","acc");
          const acc1h = document.createElement("div");
          acc1h.setAttribute("class","acc-h");
          const acc1b = document.createElement("div");
          acc1b.setAttribute("class","acc-b");
          acc1b.style.display="block";
          acc1h.innerHTML = `<strong>Roles</strong><span class="acc-count" style="opacity:.7">${roles.length}</span>`;
          roles.forEach(r=> acc1b.append(chip(r)));
          acc1.append(acc1h, acc1b);
          body.append(acc1);

          const acc2 = document.createElement("div");
          acc2.setAttribute("class","acc");
          const acc2h = document.createElement("div");
          acc2h.setAttribute("class","acc-h");
          const acc2b = document.createElement("div");
          acc2b.setAttribute("class","acc-b");
          acc2b.style.display="block";
          acc2h.innerHTML = `<strong>Scopes</strong><span class="acc-count" style="opacity:.7">${scopes.length}</span>`;
          scopes.forEach(s=> acc2b.append(chip(s, "mono")));
          acc2.append(acc2h, acc2b);
          body.append(acc2);

          const raw = new Set(Array.isArray(me.feature_tags) ? me.feature_tags : []);
          const mods=[], nouns=[], verbs=[], other=[];
          raw.forEach(t=>{
            if (t.startsWith("module:")) mods.push(t.slice(7));
            else if (t.startsWith("noun:")) nouns.push(t.slice(5));
            else if (t.startsWith("verb:")) verbs.push(t.slice(5));
            else if (raw.has("module:"+t) || raw.has("noun:"+t) || raw.has("verb:"+t)) {
              // skip dup plain
            } else other.push(t);
          });
          mods.sort(); nouns.sort(); verbs.sort(); other.sort();

          function makeAcc(title, arr){
            const box = document.createElement("div");
            box.setAttribute("class","acc");
            const h = document.createElement("div");
            h.setAttribute("class","acc-h");
            h.innerHTML = `<strong>${title}</strong><span class="acc-count" style="opacity:.7">${arr.length}</span>`;
            const b = document.createElement("div");
            b.setAttribute("class","acc-b");
            h.addEventListener("click", ()=>{
              const open = box.classList.toggle("open");
              b.style.display = open ? "block" : "none";
            });
            arr.forEach(x=> b.append(chip(x)));
            box.append(h,b);
            return box;
          }

          if (mods.length) body.append(makeAcc("Feature Tags – Modules", mods));
          if (nouns.length) body.append(makeAcc("Feature Tags – Nouns", nouns));
          if (verbs.length) body.append(makeAcc("Feature Tags – Verbs", verbs));
          if (other.length) body.append(makeAcc("Feature Tags – Other", other));

          btnRefresh.addEventListener("click", () => render());
          btnLogout.addEventListener("click", async ()=>{
            localStorage.removeItem("gims_token");
            await window.GIMS.authRefresh();
            await render();
          });
        }

        root.render = render;
        root.render();
      },
      onShow(root){ if (root.render) root.render(); }
    });
  });
})();
"""

@router.get("/inject.js")
async def inject_js():
    return PlainTextResponse(INJECT_JS, media_type="application/javascript")

@router.get("/state-tab.js")
async def state_tab_js():
    return PlainTextResponse(STATE_TAB_JS, media_type="application/javascript")

# ──────────────────────────────────────────────────────────────────────────────
# 8) Diagnostics and Node + Module
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/_diag")
async def _diag():
    out = {}
    eng = _get_logins_engine()
    # Try SQLite PRAGMAs; ignore if non-SQLite
    try:
        async with eng.begin() as conn:
            jm = await conn.execute(text("PRAGMA journal_mode"))
            out["journal_mode"] = (await jm.first())[0]
            sy = await conn.execute(text("PRAGMA synchronous"))
            out["synchronous"] = (await sy.first())[0]
            fk = await conn.execute(text("PRAGMA foreign_keys"))
            out["foreign_keys"] = (await fk.first())[0]
            bt = await conn.execute(text("PRAGMA busy_timeout"))
            out["busy_timeout"] = (await bt.first())[0]
    except Exception:
        out["dialect"] = eng.dialect.name
    return out

login_node = Node(
    name="Login (FastAPI Users)",
    kind=NodeKind.LOGIN,
    router=router,
    meta={
        "entry_path": "/login/state-tab.js",
        "provides_inject": ["/login/state-tab.js", "/login/inject.js"],
        "icon": "🔐",
        "label": "Login",
    },
)

login_module = Module(
    name="Login Module",
    nodes=[login_node],
    version="0.4.3", # Bumped version for S3 awareness
    description="Global login (logins.db) + per-project audit; roles from roles.json; project alias matching & human names. Enforces verification; /auth/me read-only. SQLite PRAGMAs per-connection. Self-heals audit_log FK. RDS-aware engines. S3-aware for roles.json/config.json.",
    roles=set(),
)

async def _apply_pragmas(engine):
    """Apply SQLite PRAGMAs after engine creation (startup). Safe no-op on Postgres."""
    try:
        if engine.dialect.name == "sqlite":
            async with engine.begin() as conn:
                await conn.execute(text("PRAGMA journal_mode=WAL"))
                await conn.execute(text("PRAGMA synchronous=NORMAL"))
                await conn.execute(text("PRAGMA foreign_keys=ON"))
                await conn.execute(text("PRAGMA busy_timeout=30000"))
    except Exception as e:
        debug("Apply PRAGMAs (startup) ignored:", repr(e))

async def initialize_login_system():
    """Initialize the login database schema once at startup."""
    await _ensure_logins_schema_once()
    await _apply_pragmas(_get_logins_engine())
    debug("Login system initialized")

def mount_into(app, prefix: str = "") -> None:
    login_module.mount(app, prefix=prefix)

    @app.on_event("startup")
    async def startup_init():
        # Always ensure schema exists before any auth call (fixes 'no such table: users')
        await initialize_login_system()