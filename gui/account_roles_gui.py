# nodes/account_roles_node.py
from __future__ import annotations

import os
import json
import uuid
import jwt
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Body, Query, Request, Header
from fastapi.responses import JSONResponse
from fastapi_users.password import PasswordHelper
from fastapi_users.jwt import generate_jwt
from pydantic import BaseModel, Field

from sqlalchemy import (
    select,
    event,
    Boolean,
    ForeignKey,
    String,
    Text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, DeclarativeBase as _SADeclarativeBase
from sqlalchemy.exc import IntegrityError

# Project helpers
from api.manifest.resolver import resolve_path, get_canonical_module_tags, get_db_uri, RDS_ENABLED
# NOTE: Import S3-aware helpers
from api.i_o import load_schema, read_text, write_text, io_list_projects

# ──────────────────────────────────────────────────────────────────────────────
# Debug control
# ──────────────────────────────────────────────────────────────────────────────
DEBUG_ENABLED = False  # flip to False to quiet logs

def debug(*args, **kwargs):
    if DEBUG_ENABLED:
        print("[account_roles]", *args, **kwargs)

# ──────────────────────────────────────────────────────────────────────────────
# Router (GUI backend style)
# ──────────────────────────────────────────────────────────────────────────────
router = APIRouter(prefix="/api/account_roles", tags=["Accounts & Roles"])

# ──────────────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────────────
def _id() -> str:
    """Generate a compact string id (32 hex)."""
    return uuid.uuid4().hex

def datetime_utc() -> str:
    # Keep ISO8601+Z to match the rest of your tables
    from datetime import datetime
    return datetime.utcnow().isoformat() + "Z"

# ──────────────────────────────────────────────────────────────────────────────
# Section J: CSRF helper (double-submit)
# If Authorization: Bearer ... is present, treat as API and skip CSRF.
# ──────────────────────────────────────────────────────────────────────────────
CSRF_COOKIE = "gims_csrf"

async def require_csrf(
    request: Request,
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
):
    method = request.method.upper()
    if method in {"GET", "HEAD", "OPTIONS"}:
        return True

    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        debug("CSRF skipped (bearer present)")
        return True

    cookie_token = request.cookies.get(CSRF_COOKIE)
    if not cookie_token or not x_csrf_token or cookie_token != x_csrf_token:
        debug("CSRF failed:", "cookie:", cookie_token, "header:", x_csrf_token)
        raise HTTPException(status_code=403, detail="CSRF validation failed")

    debug("CSRF ok")
    return True

# ──────────────────────────────────────────────────────────────────────────────
# Project-scoped DB (nodes.db): roles/overrides/audit ONLY (no users here)
# ──────────────────────────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass

class UserRole(Base):
    __tablename__ = "user_roles"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(String, index=True)  # references LUser.id (TEXT)
    role_name: Mapped[str] = mapped_column(String, index=True)

class UserPermission(Base):
    __tablename__ = "user_permissions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(String, index=True)  # references LUser.id (TEXT)
    scope: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    feature_tag: Mapped[Optional[str]] = mapped_column(String, nullable=True)

class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    ts: Mapped[str] = mapped_column(String, index=True, default=datetime_utc)
    # plain String to avoid FK to a non-existent local users table
    user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    project: Mapped[str] = mapped_column(String, index=True)
    action: Mapped[str] = mapped_column(String, index=True)
    resource: Mapped[str] = mapped_column(String, index=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    method: Mapped[Optional[str]] = mapped_column(String, nullable=True)

# ──────────────────────────────────────────────────────────────────────────────
# Local/RDS project DB session helpers (RDS-aware, like login node)
# ──────────────────────────────────────────────────────────────────────────────
_engine_cache: Dict[str, Any] = {}
_session_cache: Dict[str, async_sessionmaker[AsyncSession]] = {}

def _project_path(project: str) -> Path:
    project_root = resolve_path(Path(), "project_root")
    return project_root / project

def _sqlite_pragmas_event(dbapi_conn, _conn_rec):
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.execute("PRAGMA foreign_keys=ON;")
        cur.execute("PRAGMA busy_timeout=30000;")
    finally:
        cur.close()

def _effective_nodes_uri(project: str) -> str:
    """
    Priority:
      1) env NODES_DB_URI (rare override)
      2) resolver.get_db_uri('nodes_db', project=project)
    """
    env_uri = os.environ.get("NODES_DB_URI")
    if env_uri:
        return env_uri
    uri = get_db_uri("nodes_db", project=project)
    if RDS_ENABLED and uri.startswith("sqlite+"):
        debug(f"WARNING: RDS is enabled but nodes DB for '{project}' is falling back to SQLite. "
              f"Check local_layout_map.json for 'RDS+' prefix on 'nodes_db'.")
    return uri

def _engine_for(project: str):
    if project in _engine_cache:
        return _engine_cache[project]

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

    _engine_cache[project] = engine
    debug("Project engine URI:", project, "->", uri)
    return engine

def _sessionmaker_for(project: str) -> async_sessionmaker[AsyncSession]:
    if project in _session_cache:
        return _session_cache[project]
    sm = async_sessionmaker(bind=_engine_for(project), expire_on_commit=False)
    _session_cache[project] = sm
    return sm

async def _ensure_project_schema(project: str) -> None:
    async with _engine_for(project).begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    debug("Project schema ensured:", project)

async def _get_session_local(project: str):
    await _ensure_project_schema(project)
    sm = _sessionmaker_for(project)
    async with sm() as session:
        yield session

async def session_dep(project: str):
    async for s in _get_session_local(project):
        yield s

# ──────────────────────────────────────────────────────────────────────────────
# Global Logins DB: users + projects + accounts_projects (authoritative users)
# (RDS-aware, same selection logic as login node)
# ──────────────────────────────────────────────────────────────────────────────
class LoginsBase(_SADeclarativeBase):
    pass

_logins_engine = None
_logins_session_maker: Optional[async_sessionmaker[AsyncSession]] = None

class LUser(LoginsBase):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # TEXT ids (32-hex)
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

class LProject(LoginsBase):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)

from sqlalchemy import DateTime
from datetime import datetime, timezone

class AccountProject(LoginsBase):
    __tablename__ = "accounts_projects"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(String, index=True)
    role_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

def _effective_logins_uri() -> str:
    """
    Priority:
      1) env LOGINS_DB_URI (rare override)
      2) resolver.get_db_uri('logins_db')
    """
    env_uri = os.environ.get("LOGINS_DB_URI")
    if env_uri:
        return env_uri
    uri = get_db_uri("logins_db")
    if RDS_ENABLED and uri.startswith("sqlite+"):
        debug("WARNING: RDS is enabled but login DB is falling back to SQLite. "
              "Check local_layout_map.json for 'RDS+' prefix on 'logins_db'.")
    return uri

def _logins_engine_get():
    global _logins_engine, _logins_session_maker
    if _logins_engine:
        return _logins_engine

    uri = _effective_logins_uri()

    # Only mkdir in local sqlite mode
    if uri.startswith("sqlite+aiosqlite:///"):
        db_file = uri.removeprefix("sqlite+aiosqlite:///")
        Path(db_file).parent.mkdir(parents=True, exist_ok=True)

    _logins_engine = create_async_engine(
        uri,
        future=True,
        pool_pre_ping=True,
        echo=False,
        connect_args=({"timeout": 30, "check_same_thread": False} if uri.startswith("sqlite+") else {}),
    )

    if uri.startswith("sqlite+"):
        event.listen(_logins_engine.sync_engine, "connect", _sqlite_pragmas_event)

    _logins_session_maker = async_sessionmaker(bind=_logins_engine, expire_on_commit=False)
    debug("Logins engine URI:", uri)
    return _logins_engine

def _logins_session():
    _logins_engine_get()
    return _logins_session_maker

async def logins_session_dep():
    sm = _logins_session()
    async with sm() as session:
        yield session

# ──────────────────────────────────────────────────────────────────────────────
# Roles file helpers — projects field is DEPRECATED (kept for compatibility)
# ──────────────────────────────────────────────────────────────────────────────
def _roles_path_for(project: str) -> Path:
    proj = _project_path(project)
    p = resolve_path(proj, "roles_schema")
    debug("Resolved roles_schema:", p.as_posix())
    return p

class RoleDef(BaseModel):
    name: str
    description: Optional[str] = None
    scopes: List[str] = Field(default_factory=list, description="e.g. ['{project}:run:view','*:tasks:view']")
    feature_tags: List[str] = Field(default_factory=list, description="UI gating tags")
    # NOTE: 'projects' is deprecated; project membership now lives in logins.db (accounts_projects)
    projects: List[str] = Field(default_factory=list, description='[DEPRECATED] No longer used for membership')

class RolesFile(BaseModel):
    roles: List[RoleDef] = Field(default_factory=list)

def _read_roles(project: str) -> RolesFile:
    path = _roles_path_for(project)
    if not path.exists():
        debug("roles.json missing; returning empty structure")
        return RolesFile(roles=[])
    try:
        # S3-aware read
        data = json.loads(read_text(path, encoding="utf-8"))
        debug("Loaded roles.json with", len(data.get("roles", [])), "roles")
        return RolesFile.model_validate(data)
    except Exception as e:
        debug("ERROR reading roles.json:", e)
        raise HTTPException(500, f"Invalid roles.json: {e}")

def _write_roles(project: str, rf: RolesFile) -> None:
    path = _roles_path_for(project)
    # S3-aware write
    write_text(path, json.dumps(rf.model_dump(), indent=2), encoding="utf-8")
    debug("Wrote roles.json with", len(rf.roles), "roles at", path.as_posix())

# ──────────────────────────────────────────────────────────────────────────────
# Pydantic models for requests
# ──────────────────────────────────────────────────────────────────────────────
class RoleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    scopes: List[str] = Field(default_factory=list)
    feature_tags: List[str] = Field(default_factory=list)
    # 'projects' retained only for backward-compat in file format; not used for membership
    projects: List[str] = Field(default_factory=list)

class RoleUpdate(BaseModel):
    description: Optional[str] = None
    scopes: Optional[List[str]] = None
    feature_tags: Optional[List[str]] = None
    # 'projects' is ignored; roles no longer carry membership
    projects: Optional[List[str]] = None

class AssignRolesRequest(BaseModel):
    role_names: List[str] = Field(default_factory=list)

class OverridesRequest(BaseModel):
    add_scopes: List[str] = Field(default_factory=list)
    remove_scopes: List[str] = Field(default_factory=list)
    add_feature_tags: List[str] = Field(default_factory=list)
    remove_feature_tags: List[str] = Field(default_factory=list)

class ApproveUserRequest(BaseModel):
    approve: bool = True
    assign_roles: List[str] = Field(default_factory=list)

class UserStatusRequest(BaseModel):
    is_active: Optional[bool] = None
    force_password_reset: Optional[bool] = None
    deactivate_sessions: Optional[bool] = None  # JWT is stateless; see note.

# New: membership mutate API
class MembershipUpdateIn(BaseModel):
    user_id: str
    project: str  # human project name (LProject.name)
    op: str = Field(pattern="^(add|remove)$")
    role_name: Optional[str] = None  # optional role hint (stored on link)

class MembershipsOut(BaseModel):
    user_id: str
    memberships: List[Dict[str, Optional[str]]]  # [{project, role_name}]

# ──────────────────────────────────────────────────────────────────────────────
# Serialization helpers
# ──────────────────────────────────────────────────────────────────────────────
def serialize_user(lu: LUser, roles: List[str], overrides: List[Tuple[Optional[str], Optional[str]]]) -> Dict[str, Any]:
    return {
        "id": lu.id,
        "email": lu.email,
        "is_active": lu.is_active,
        "is_superuser": lu.is_superuser,
        "is_verified": lu.is_verified,
        "roles": roles,
        "overrides": [{"scope": s, "feature_tag": t} for (s, t) in overrides],
    }

def serialize_audit(a: AuditLog) -> Dict[str, Any]:
    return {
        "id": a.id,
        "ts": a.ts,
        "user_id": a.user_id,
        "action": a.action,
        "resource": a.resource,
        "resource_id": a.resource_id,
        "ip": a.ip,
        "path": a.path,
        "method": a.method,
    }

# ──────────────────────────────────────────────────────────────────────────────
# Auth helpers for superuser-gated endpoints
# ──────────────────────────────────────────────────────────────────────────────
DEFAULT_ROLES_PROJECT = os.environ.get("ROLES_CANONICAL_PROJECT", "LIMS-System")

async def _fetch_auth_me(request: Request, project: str) -> dict:
    # Build same-origin base
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    base = f"{proto}://{host}"
    url = f"{base}/login/{project}/auth/me"

    # Forward cookies and any Bearer token we can find
    cookies = request.cookies.copy()
    headers: Dict[str, str] = {}
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth:
        headers["Authorization"] = auth

    try:
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
            r = await client.get(url, headers=headers, cookies=cookies)
            if r.status_code == 200:
                return r.json() or {}
            return {}
    except Exception as e:
        debug("auth_me fetch error:", repr(e))
        return {}

async def require_superuser(request: Request, lsession: AsyncSession = Depends(logins_session_dep)):
    """
    Verify the caller is a superuser using /auth/me → users.is_superuser.
    """
    me = await _fetch_auth_me(request, DEFAULT_ROLES_PROJECT)
    user = (me.get("user") or {}) if isinstance(me, dict) else {}
    if user.get("id") and bool(user.get("is_superuser")):
        return True
    raise HTTPException(403, "Superuser required")

# ──────────────────────────────────────────────────────────────────────────────
# Section A: Role registry (create / edit / delete / export)
# (unchanged behavior, except 'projects' is now deprecated/ignored for membership)
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/{project}/roles", response_model=RolesFile)
async def list_roles(project: str):
    debug("GET /roles:", project)
    return _read_roles(project)

@router.post("/{project}/roles", response_model=RoleDef)
async def create_role(project: str, body: RoleCreate):
    debug("POST /roles", project, body.model_dump())
    rf = _read_roles(project)
    if any(r.name == body.name for r in rf.roles):
        raise HTTPException(409, f"Role '{body.name}' already exists")
    role = RoleDef(**body.model_dump())
    rf.roles.append(role)
    _write_roles(project, rf)
    return role

@router.patch("/{project}/roles/{role_name}", response_model=RoleDef)
async def update_role(project: str, role_name: str, body: RoleUpdate):
    debug("PATCH /roles", project, role_name, body.model_dump())
    rf = _read_roles(project)
    for i, r in enumerate(rf.roles):
        if r.name == role_name:
            upd = r.model_dump()
            if body.description is not None:
                upd["description"] = body.description
            if body.scopes is not None:
                upd["scopes"] = body.scopes
            if body.feature_tags is not None:
                upd["feature_tags"] = body.feature_tags
            # 'projects' ignored; kept only for file-format compatibility
            rf.roles[i] = RoleDef(**upd)
            _write_roles(project, rf)
            return rf.roles[i]
    raise HTTPException(404, f"Role '{role_name}' not found")

@router.delete("/{project}/roles/{role_name}")
async def delete_role(project: str, role_name: str):
    debug("DELETE /roles", project, role_name)
    rf = _read_roles(project)
    before = len(rf.roles)
    rf.roles = [r for r in rf.roles if r.name != role_name]
    after = len(rf.roles)
    if before == after:
        raise HTTPException(404, f"Role '{role_name}' not found")
    _write_roles(project, rf)
    return {"ok": True, "deleted": role_name}

@router.get("/{project}/roles/export")
async def export_roles(project: str):
    debug("GET /roles/export", project)
    rf = _read_roles(project)
    return JSONResponse(rf.model_dump())

# ──────────────────────────────────────────────────────────────────────────────
# Section B: Account approval (pending, approve/reject, assign roles) — logins.db
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/{project}/users/pending")
async def list_pending(project: str, lsession: AsyncSession = Depends(logins_session_dep)):
    """
    Pending = users in logins.users with is_verified = 0
              AND who have an accounts_projects row for the requested project.
    """
    debug("GET /users/pending (logins.db)", project)

    proj_row = (await lsession.execute(
        select(LProject).where(LProject.name == project)
    )).scalars().first()
    if not proj_row:
        return {"pending": []}
    pid = proj_row.id

    rows = (await lsession.execute(
        select(LUser, AccountProject.role_name)
        .join(AccountProject, AccountProject.user_id == LUser.id)
        .where(
            (LUser.is_verified == 0) &   # noqa: E712
            (AccountProject.project_id == pid)
        )
    )).all()

    out = []
    for u, role_name in rows:
        out.append({
            "id": u.id,
            "email": u.email,
            "is_active": u.is_active,
            "is_verified": u.is_verified,
            "roles": ([role_name] if role_name else [])
        })
        debug(" pending:", u.email, "role_name:", role_name, "pid:", pid)
    return {"pending": out}

@router.post("/{project}/users/{user_id}/approve", dependencies=[Depends(require_csrf)])
async def approve_user(project: str, user_id: str, body: ApproveUserRequest,
                       lsession: AsyncSession = Depends(logins_session_dep)):
    """
    On approve:
      - logins.users.is_verified = 1
      - Upsert accounts_projects (logins.db) with a single role_name (optional)
    """
    debug("POST /users/approve (logins.db)", project, user_id, body.model_dump())

    uid = user_id  # TEXT id (32-hex)
    user = await lsession.get(LUser, uid)
    if not user:
        raise HTTPException(404, "User not found")

    proj_row = (await lsession.execute(
        select(LProject).where(LProject.name == project)
    )).scalars().first()
    if not proj_row:
        raise HTTPException(404, f"Project '{project}' not found in logins.db")
    pid = proj_row.id

    if not body.approve:
        debug(" rejected:", user.email)
        return {"ok": True, "approved": False}

    roles_idx = {r.name for r in _read_roles(project).roles}
    chosen_role = None
    for rn in (body.assign_roles or []):
        if rn in roles_idx:
            chosen_role = rn
            break

    user.is_verified = True

    link = (await lsession.execute(
        select(AccountProject).where(
            (AccountProject.user_id == uid) & (AccountProject.project_id == pid)
        )
    )).scalars().first()

    if link:
        link.role_name = chosen_role
    else:
        lsession.add(AccountProject(id=_id(), user_id=uid, project_id=pid, role_name=chosen_role))

    await lsession.commit()
    debug(" approved:", user.email, "role:", chosen_role, "pid:", pid)
    return {"ok": True, "approved": True, "roles_assigned": ([chosen_role] if chosen_role else [])}

# ──────────────────────────────────────────────────────────────────────────────
# Section C: User role management & overrides — nodes.db + logins merge
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/{project}/users")
async def list_users(
    project: str,
    session: AsyncSession = Depends(session_dep),       # for overrides
    lsession: AsyncSession = Depends(logins_session_dep),  # for roles/membership
):
    debug("GET /users", project)

    # Resolve project id in logins.db
    proj_row = (
        await lsession.execute(select(LProject).where(LProject.name == project))
    ).scalars().first()
    if not proj_row:
        return {"users": []}
    pid = proj_row.id

    # Users + role from logins.db
    rows = (
        await lsession.execute(
            select(LUser, AccountProject.role_name)
            .join(AccountProject, AccountProject.user_id == LUser.id)
            .where(AccountProject.project_id == pid)
        )
    ).all()

    # Overrides still come from nodes.db
    perm_rows = (await session.execute(select(UserPermission))).scalars().all()
    perms_by_user: Dict[str, List[Tuple[Optional[str], Optional[str]]]] = {}
    for p in perm_rows:
        perms_by_user.setdefault(p.user_id, []).append((p.scope, p.feature_tag))

    out = []
    for u, role_name in rows:
        roles = [role_name] if role_name else []
        overrides = perms_by_user.get(u.id, [])
        out.append(serialize_user(u, roles, overrides))
        debug(" user:", u.email, "roles:", roles, "overrides:", overrides)

    return {"users": out}

@router.post("/{project}/users/{user_id}/roles", dependencies=[Depends(require_csrf)])
async def assign_roles(
    project: str,
    user_id: str,
    body: AssignRolesRequest,
    lsession: AsyncSession = Depends(logins_session_dep),
):
    """
    Update a user's roles in a project.
    Uses logins.db (accounts_projects) instead of nodes.db.user_roles.
    Mirrors approve_user but without touching verification.
    """
    debug("POST /users/{id}/roles".format(id=user_id), project, body.model_dump())

    uid = user_id
    user = await lsession.get(LUser, uid)
    if not user:
        raise HTTPException(404, "User not found")

    # Resolve project row
    proj_row = (
        await lsession.execute(select(LProject).where(LProject.name == project))
    ).scalars().first()
    if not proj_row:
        raise HTTPException(404, f"Project '{project}' not found in logins.db")
    pid = proj_row.id

    # Validate against roles.json
    roles_idx = {r.name for r in _read_roles(project).roles}
    chosen_role = None
    for rn in (body.role_names or []):
        if rn in roles_idx:
            chosen_role = rn
            break

    # Upsert into accounts_projects
    link = (
        await lsession.execute(
            select(AccountProject).where(
                (AccountProject.user_id == uid) & (AccountProject.project_id == pid)
            )
        )
    ).scalars().first()

    if link:
        link.role_name = chosen_role
    else:
        lsession.add(
            AccountProject(id=_id(), user_id=uid, project_id=pid, role_name=chosen_role)
        )

    await lsession.commit()
    debug(" assigned role:", chosen_role, "for", user.email, "pid:", pid)

    return {
        "ok": True,
        "roles": [chosen_role] if chosen_role else [],
    }

@router.post("/{project}/users/{user_id}/overrides")
async def edit_overrides(project: str, user_id: str, body: OverridesRequest,
                         session: AsyncSession = Depends(session_dep)):
    debug("POST /users/{id}/overrides".format(id=user_id), project, body.model_dump())
    uid = user_id

    if body.remove_scopes:
        await session.execute(
            UserPermission.__table__.delete().where(
                (UserPermission.user_id == uid) &
                (UserPermission.scope.in_(body.remove_scopes))
            )
        )
        debug(" removed scopes:", body.remove_scopes)
    if body.remove_feature_tags:
        await session.execute(
            UserPermission.__table__.delete().where(
                (UserPermission.user_id == uid) &
                (UserPermission.feature_tag.in_(body.remove_feature_tags))
            )
        )
        debug(" removed tags:", body.remove_feature_tags)

    for s in body.add_scopes:
        session.add(UserPermission(id=_id(), user_id=uid, scope=s, feature_tag=None))
    for t in body.add_feature_tags:
        session.add(UserPermission(id=_id(), user_id=uid, scope=None, feature_tag=t))
    await session.commit()
    debug(" added scopes:", body.add_scopes, "added tags:", body.add_feature_tags)
    return {"ok": True}

# ──────────────────────────────────────────────────────────────────────────────
# Section D: Account status controls — operate on logins.db (authoritative)
# ──────────────────────────────────────────────────────────────────────────────
@router.post("/{project}/users/{user_id}/status")
async def set_user_status(project: str, user_id: str, body: UserStatusRequest,
                          lsession: AsyncSession = Depends(logins_session_dep)):
    debug("POST /users/{id}/status".format(id=user_id), project, body.model_dump())
    uid = user_id
    u = await lsession.get(LUser, uid)
    if not u:
        raise HTTPException(404, "User not found")

    if body.is_active is not None:
        u.is_active = bool(body.is_active)
        debug(" set is_active:", u.is_active)
    if body.force_password_reset:
        debug(" force_password_reset requested (not implemented)")
    if body.deactivate_sessions:
        debug(" deactivate_sessions requested (not implemented)")
    await lsession.commit()
    return {"ok": True, "is_active": u.is_active}

@router.delete("/{project}/users/{user_id}")
async def delete_user(
    project: str,
    user_id: str,
    hard: bool = Query(False, description="Hard delete (purge)"),
    lsession: AsyncSession = Depends(logins_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    """
    Purge flow (hard=True):
      1) Remove per-project roles/overrides (nodes.db)
      2) Remove ALL accounts_projects links for this user (logins.db)
      3) Delete the user row from logins.users

    Soft flow (hard=False):
      1) Remove per-project roles/overrides (nodes.db) for this project
      2) Mark the user inactive in logins.users
         (keeps the user record for other projects / audit)
    """
    debug("DELETE /users/{id}".format(id=user_id), project, "hard:", hard)
    uid = user_id

    # Always clean per-project data first (nodes.db has no FK to logins)
    await session.execute(UserRole.__table__.delete().where(UserRole.user_id == uid))
    await session.execute(UserPermission.__table__.delete().where(UserPermission.user_id == uid))
    await session.commit()

    if not hard:
        # Soft-delete: only flip active flag in the global user record
        u = await lsession.get(LUser, uid)
        if not u:
            # Nothing to deactivate, treat as idempotent
            return {"ok": True, "is_active": None, "note": "user not found (soft)"}
        u.is_active = False
        await lsession.commit()
        debug(" soft-deactivated (global):", u.email)
        return {"ok": True, "is_active": u.is_active}

    # Hard-delete: remove all project links first, then delete user
    try:
        # 1) Delete all project links for this user across ALL projects
        await lsession.execute(AccountProject.__table__.delete().where(AccountProject.user_id == uid))
        # 2) Now delete the user row
        await lsession.execute(LUser.__table__.delete().where(LUser.id == uid))
        await lsession.commit()
        debug(" hard-purged user (global):", uid)
        return {"ok": True, "hard": True}
    except IntegrityError as e:
        # If other unexpected FKs exist, surface a useful message
        await lsession.rollback()
        debug(" hard-purge failed (FK):", repr(e))
        raise HTTPException(
            status_code=409,
            detail="Cannot purge user due to foreign key references. "
                   "All related rows must be removed first."
        )
    except Exception as e:
        await lsession.rollback()
        debug(" hard-purge failed (other):", repr(e))
        raise HTTPException(status_code=500, detail=f"Failed to purge user: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# Section E: Role visibility & effective permissions per user
# (Projects are now sourced from logins.db memberships — NOT roles.json.)
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/{project}/roles/usage")
async def role_usage(project: str, session: AsyncSession = Depends(session_dep)):
    debug("GET /roles/usage", project)
    rf = _read_roles(project)
    usage: Dict[str, int] = {r.name: 0 for r in rf.roles}
    rows = (await session.execute(select(UserRole.role_name))).scalars().all()
    for rn in rows:
        usage[rn] = usage.get(rn, 0) + 1
    debug(" role usage:", usage)
    return {"usage": usage}

@router.get("/{project}/users/{user_id}/effective")
async def user_effective_permissions(project: str, user_id: str,
                                     session: AsyncSession = Depends(session_dep),
                                     lsession: AsyncSession = Depends(logins_session_dep)):
    debug("GET /users/{id}/effective".format(id=user_id), project)
    uid = user_id
    rf = _read_roles(project)

    u = await lsession.get(LUser, uid)
    if not u:
        raise HTTPException(404, "User not found")

    role_names = (await session.execute(
        select(UserRole.role_name).where(UserRole.user_id == uid)
    )).scalars().all()

    idx = {r.name: r for r in rf.roles}
    s_role: List[str] = []
    t_role: List[str] = []
    for rn in role_names:
        r = idx.get(rn)
        if r:
            s_role.extend(r.scopes or [])
            t_role.extend(r.feature_tags or [])

    def _dedupe(seq: List[str]) -> List[str]:
        seen = set(); out: List[str] = []
        for v in seq:
            if v not in seen:
                seen.add(v); out.append(v)
        return out

    s_role = _dedupe(s_role)
    t_role = _dedupe(t_role)

    orows = (await session.execute(
        select(UserPermission.scope, UserPermission.feature_tag)
        .where(UserPermission.user_id == uid)
    )).all()
    s_ovr = [row[0] for row in orows if row[0]]
    t_ovr = [row[1] for row in orows if row[1]]

    scopes = sorted(set(s_role) | set(s_ovr))
    tags = sorted(set(t_role) | set(t_ovr))

    # NEW: projects from logins.db memberships (decoupled from roles.json)
    proj_rows = (await lsession.execute(
        select(LProject.name)
        .join(AccountProject, AccountProject.project_id == LProject.id)
        .where(AccountProject.user_id == uid)
    )).scalars().all()
    projects_eff = sorted({p for p in proj_rows if p})

    debug(" effective:", u.email, "roles:", role_names, "scopes:", scopes, "tags:", tags, "projects:", projects_eff)
    return {
        "user": {"id": u.id, "email": u.email},
        "roles": role_names,
        "scopes": scopes,
        "feature_tags": tags,
        "projects": projects_eff,
    }

# ──────────────────────────────────────────────────────────────────────────────
# Section F: Audit integration (project-local)
# ──────────────────────────────────────────────────────────────────────────────
async def _audit(request: Request, session: AsyncSession, *, project: str,
                 action: str, resource: str, resource_id: Optional[str], payload: Optional[dict]):
    rec = AuditLog(
        id=_id(),
        ts=datetime_utc(),
        user_id=None,
        project=project,
        action=action,
        resource=resource,
        resource_id=resource_id,
        payload_json=json.dumps(payload or {}),
        ip=(request.client.host if request.client else None),
        user_agent=request.headers.get("user-agent"),
        path=request.url.path,
        method=request.method,
    )
    session.add(rec)
    await session.commit()
    debug("Audit:", action, resource, resource_id)

@router.get("/{project}/audit")
async def audit_view(project: str, limit: int = 200, session: AsyncSession = Depends(session_dep)):
    debug("GET /audit", project, "limit:", limit)
    a_rows = (await session.execute(
        select(AuditLog)
        .where(AuditLog.project == project)
        .order_by(AuditLog.ts.desc())
        .limit(limit)
    )).scalars().all()

    out = [serialize_audit(a) for a in a_rows]
    debug(" audit rows:", len(out))
    return {"audit": out}

# ──────────────────────────────────────────────────────────────────────────────
# Section G: Security/compliance placeholders
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/{project}/policies")
async def get_policies(project: str):
    debug("GET /policies", project)
    return JSONResponse({"password_min_len": 8, "mfa_required": False}, status_code=200)

@router.post("/{project}/policies")
async def set_policies(project: str, body: Dict[str, Any] = Body(...)):
    debug("POST /policies", project, body)
    projp = _project_path(project)
    pol_path = resolve_path(projp, "logins_dir") / "policies.json"
    # S3-aware write (no local mkdir needed for S3)
    write_text(pol_path, json.dumps(body, indent=2), encoding="utf-8")
    debug(" policies saved at", pol_path.as_posix())
    return {"ok": True}

@router.post("/{project}/users/{user_id}/sessions/revoke")
async def revoke_sessions(project: str, user_id: str):
    debug("POST /users/{id}/sessions/revoke".format(id=user_id), project)
    return JSONResponse({"ok": True, "note": "JWT is stateless; implement jti/blacklist to truly revoke."}, status_code=200)

# ──────────────────────────────────────────────────────────────────────────────
# Section H: Project scoping UX helpers
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/projects")
async def list_projects():
    """Return a list of available projects (S3- and local-aware)."""
    try:
        projects = io_list_projects()
        return {"projects": projects}
    except Exception as e:
        print(f"[list_projects] list_projects failed: {e!r}")
        return {"projects": []}

# ──────────────────────────────────────────────────────────────────────────────
# Section H2: Dropdown catalogs (nouns, verb groups, modules)
# ──────────────────────────────────────────────────────────────────────────────
def _safe_load_json(path: Path) -> Any:
    # S3-aware JSON loader
    try:
        if path.exists():
            return json.loads(read_text(path, encoding="utf-8"))
    except Exception as e:
        debug("JSON load error at", path.as_posix(), "->", e)
    return None

def _list_noun_types_for(project: str) -> List[str]:
    projp = _project_path(project)

    data: Any = None
    try:
        data = load_schema(projp, "noun")
    except Exception as e:
        debug("load_schema(noun) failed:", e)

    if data is None:
        try:
            schema_path = resolve_path(projp, "noun_schema")
            data = _safe_load_json(schema_path)
        except Exception as e:
            debug("noun_schema fallback failed:", e)
            data = None

    names: List[str] = []
    if isinstance(data, dict):
        if "types" in data and isinstance(data["types"], list):
            for entry in data["types"]:
                if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                    names.append(entry["name"])
        elif "nouns" in data and isinstance(data["nouns"], list):
            for entry in data["nouns"]:
                if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                    names.append(entry["name"])
        else:
            names.extend([k for k in data.keys() if isinstance(k, str)])
    elif isinstance(data, list):
        for entry in data:
            if isinstance(entry, str):
                names.append(entry)
            elif isinstance(entry, dict) and isinstance(entry.get("name"), str):
                names.append(entry["name"])
    return sorted(set(names))

def _list_verb_groups_for(project: str) -> List[str]:
    projp = _project_path(project)
    try:
        root = resolve_path(projp, "verbs_dir")
    except Exception as e:
        debug("resolve verbs_dir failed:", e)
        root = projp / "verbs"

    if not root.exists():
        return []
    return sorted([p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")])

def _list_custom_modules_for(project: str) -> List[str]:
    projp = _project_path(project)
    try:
        mods_root = resolve_path(projp, "custom_module_dir")
    except Exception as e:
        debug("resolve custom_module_dir failed:", e)
        mods_root = projp / "custom" / "modules"

    names: set[str] = set()
    if mods_root.exists():
        for p in mods_root.iterdir():
            if p.is_dir() and not p.name.startswith("."):
                names.add(p.name)
            elif p.is_file() and p.suffix == ".py" and not p.name.startswith("_"):
                names.add(p.stem)
    return sorted(names)

@router.get("/{project}/noun_types")
async def ar_list_noun_types(project: str) -> List[str]:
    debug("[account_roles:noun_types]", project)
    return _list_noun_types_for(project)

@router.get("/{project}/verb_groups")
async def ar_list_verb_groups(project: str) -> List[str]:
    debug("[account_roles:verb_groups]", project)
    return _list_verb_groups_for(project)

@router.get("/{project}/modules")
async def ar_list_modules(project: str) -> Dict[str, List[str]]:
    debug("[account_roles:modules]", project)
    try:
        canonical = list(get_canonical_module_tags() or [])
    except Exception as e:
        debug("get_canonical_module_tags failed:", e)
        canonical = []
    custom = _list_custom_modules_for(project)
    merged = sorted(set(canonical) | set(custom))
    return {"canonical": sorted(canonical), "custom": custom, "all": merged}

# ──────────────────────────────────────────────────────────────────────────────
# Sign-off gates + unified catalog
# ──────────────────────────────────────────────────────────────────────────────
def _looks_like_verb_schema(d: dict) -> bool:
    if not isinstance(d, dict):
        return False
    if {"adverb", "adverb_class"} & set(d.keys()):
        return False
    return bool({"verb_name", "linear_status", "verb_group", "status_values"} & set(d.keys()))

def _iter_verbs_from_schema_tree(tree: Any):
    debug("[signoff] walk type=", type(tree).__name__)
    if not isinstance(tree, dict):
        return
    for k, v in tree.items():
        if not isinstance(v, dict):
            continue
        if _looks_like_verb_schema(v):
            name = v.get("verb_name") or k
            debug(f"[signoff] verb(flat): {name}")
            yield name, v
            continue
        child_verbs = []
        for ck, cv in v.items():
            if isinstance(cv, dict) and _looks_like_verb_schema(cv):
                child_verbs.append((ck, cv))
        if child_verbs:
            debug(f"[signoff] group '{k}' -> {len(child_verbs)} verbs")
            for ck, cv in child_verbs:
                name = cv.get("verb_name") or ck
                debug(f"[signoff] verb(group): {name}")
                yield name, cv

def _list_signoff_gates_for(project: str) -> List[str]:
    debug(f"[_list_signoff_gates_for] START project={project}")
    projp = _project_path(project)
    debug(f"[_list_signoff_gates_for] project_path={projp}")

    data: Any = None
    try:
        data = load_schema(projp, "verb")
        ksample = list(data.keys())[:10] if isinstance(data, dict) else []
        debug(f"[_list_signoff_gates_for] load_schema OK ({type(data).__name__}) sample={ksample}")
    except Exception as e:
        debug(f"[_list_signoff_gates_for] load_schema(verb) failed: {e!r}")

    if data is None:
        try:
            schema_path = resolve_path(projp, "verb_schema")
            debug(f"[_list_signoff_gates_for] fallback path={schema_path}")
            data = _safe_load_json(schema_path)
            ksample = list(data.keys())[:10] if isinstance(data, dict) else []
            debug(f"[_list_signoff_gates_for] fallback loaded ({type(data).__name__}) sample={ksample}")
        except Exception as e:
            debug(f"[_list_signoff_gates_for] verb_schema fallback failed: {e!r}")
            return []

    seen: set[str] = set()
    out: List[str] = []
    verbs_seen = 0

    for verb_name, vs in _iter_verbs_from_schema_tree(data):
        verbs_seen += 1
        ls = vs.get("linear_status")
        if not isinstance(ls, dict):
            debug(f"[signoff] {verb_name}: no linear_status -> skip")
            continue
        if not bool(ls.get("enabled", False)):
            debug(f"[signoff] {verb_name}: linear_status disabled -> skip")
            continue

        steps = ls.get("steps")
        if not isinstance(steps, list):
            debug(f"[signoff] {verb_name}: no steps list -> skip")
            continue

        gates = [s for s in steps if isinstance(s, dict) and (s.get("type") or "").lower() == "gate"]
        if not gates:
            debug(f"[signoff] {verb_name}: no gate steps -> skip")
            continue

        debug(f"[signoff] {verb_name}: {len(gates)} gate(s)")
        for idx, step in enumerate(gates):
            sid = step.get("id") or step.get("label") or f"gate_{idx+1}"
            label = f"{verb_name}: {sid}"
            if label not in seen:
                debug(f"[signoff] ADD {label}")
                seen.add(label)
                out.append(label)

    debug(f"[_list_signoff_gates_for] DONE verbs_seen={verbs_seen} gates_found={len(out)}")
    return sorted(out)

@router.get("/{project}/signoff_gates")
async def ar_list_signoff_gates(project: str) -> List[str]:
    debug(f"[account_roles:signoff_gates] START project={project}")
    out = _list_signoff_gates_for(project)
    debug(f"[account_roles:signoff_gates] RETURN count={len(out)}")
    return out

@router.get("/{project}/catalog")
async def ar_dropdown_catalog(project: str):
    debug("[account_roles:catalog]", project)
    nouns = _list_noun_types_for(project)
    verb_groups = _list_verb_groups_for(project)
    modules = await ar_list_modules(project)
    signoff_gates = _list_signoff_gates_for(project)
    projs = (await list_projects())["projects"]

    return {
        "nouns": nouns,
        "verb_groups": verb_groups,
        "modules": modules,
        "signoff_gates": signoff_gates,
        "projects": projs,
    }

# ──────────────────────────────────────────────────────────────────────────────
# Section I: Effective feature tags for launcher preview
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/{project}/users/{user_id}/feature-tags")
async def feature_tags_for_user(project: str, user_id: str,
                                session: AsyncSession = Depends(session_dep)):
    debug("GET /users/{id}/feature-tags".format(id=user_id), project)
    uid = user_id
    rf = _read_roles(project)

    role_names = (await session.execute(
        select(UserRole.role_name).where(UserRole.user_id == uid)
    )).scalars().all()

    idx = {r.name: r for r in rf.roles}
    t_role: List[str] = []
    for rn in role_names:
        r = idx.get(rn)
        if r:
            t_role.extend(r.feature_tags or [])

    seen = set(); t_role_dedup: List[str] = []
    for v in t_role:
        if v not in seen:
            seen.add(v); t_role_dedup.append(v)

    t_ovr = (await session.execute(
        select(UserPermission.feature_tag).where(UserPermission.user_id == uid)
    )).scalars().all()
    tags = sorted(set(t_role_dedup) | {t for t in t_ovr if t})
    debug(" feature tags:", tags)
    return {"feature_tags": tags}

# ──────────────────────────────────────────────────────────────────────────────
# Section K: Password reset (Beta) — uses logins.db (authoritative)
# ──────────────────────────────────────────────────────────────────────────────
JWT_SECRET = os.environ.get("GIMS_JWT_SECRET") or ("dev-" + uuid.uuid4().hex)

class ResetInitiateIn(BaseModel):
    email: str

class ResetInitiateOut(BaseModel):
    email: str
    token: str  # Beta: returned so admin can share it

class ResetPerformIn(BaseModel):
    token: str
    new_password: str = Field(min_length=8)

async def _audit_reset(
    request: Request,
    session: AsyncSession,
    *,
    project: str,
    action: str,
    resource_id: Optional[str],
    payload: Optional[dict] = None,
) -> None:
    try:
        rec = AuditLog(
            id=uuid.uuid4().hex,
            ts=datetime_utc(),
            user_id=None,
            project=project,
            action=action,
            resource="user",
            resource_id=resource_id,
            payload_json=json.dumps(payload or {}),
            ip=(request.client.host if request.client else None) if request else None,
            user_agent=(request.headers.get("user-agent") if request else None),
            path=(request.url.path if request else None),
            method="POST",
        )
        session.add(rec)
        await session.commit()
    except Exception as e:
        debug("audit write failed:", repr(e))

@router.post(
    "/{project}/users/reset/initiate",
    response_model=ResetInitiateOut,
    dependencies=[Depends(require_csrf)],
)
async def reset_initiate(
    project: str,
    body: ResetInitiateIn = Body(...),
    request: Request = None,
    lsession: AsyncSession = Depends(logins_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    debug("POST /users/reset/initiate", project, body.email)

    user = (
        await lsession.execute(select(LUser).where(LUser.email == body.email))
    ).scalars().first()
    if not user:
        raise HTTPException(404, "User not found")

    lifetime_seconds = int(timedelta(hours=1).total_seconds())
    token = generate_jwt(
        {"sub": str(user.id), "aud": "fastapi-users:reset"},
        JWT_SECRET,
        lifetime_seconds,
    )
    debug("Generated token:", token[:20] + "...")

    await _audit_reset(
        request, session,
        project=project,
        action="admin_issue_reset",
        resource_id=str(user.id),
        payload={"email": body.email},
    )
    return ResetInitiateOut(email=body.email, token=token)

@router.post(
    "/{project}/users/reset/perform",
    dependencies=[Depends(require_csrf)],
)
async def reset_perform(
    project: str,
    body: ResetPerformIn = Body(...),
    request: Request = None,
    lsession: AsyncSession = Depends(logins_session_dep),
    session: AsyncSession = Depends(session_dep),
):
    debug("POST /users/reset/perform", project)

    try:
        claims = jwt.decode(
            body.token,
            JWT_SECRET,
            algorithms=["HS256"],
            audience="fastapi-users:reset",
        )
    except Exception as e:
        debug("reset perform: token decode failed:", repr(e))
        raise HTTPException(400, "Invalid or expired token")

    user_sub = claims.get("sub")
    if not user_sub:
        debug("reset perform: token missing 'sub'")
        raise HTTPException(400, "Invalid or expired token")

    uid = str(user_sub)
    user = await lsession.get(LUser, uid)
    if not user:
        debug("reset perform: user not found for sub:", user_sub)
        raise HTTPException(404, "User not found")

    try:
        helper = PasswordHelper()
        user.hashed_password = helper.hash(body.new_password)
        await lsession.commit()
    except Exception as e:
        debug("reset perform: password update failed:", repr(e))
        raise HTTPException(400, "Could not set new password")

    await _audit_reset(
        request, session,
        project=project,
        action="reset_password",
        resource_id=str(user.id),
    )
    debug("reset perform ok for", user.email)
    return {"ok": True}

# ──────────────────────────────────────────────────────────────────────────────
# Section L: Project code lookup (from config.json)
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/{project}/project_code")
async def get_project_code(project: str):
    """
    Returns the project_code from the project's config.json.
    Example response: { "project": "LIMS-System", "project_code": "12345" }
    """
    try:
        proj_root = _project_path(project)
        cfg_path = resolve_path(proj_root, "config.json")
        if not cfg_path.exists():
            raise HTTPException(404, f"config.json not found for project '{project}'")

        # S3-aware read
        data = json.loads(read_text(cfg_path, encoding="utf-8"))
        code = data.get("project_code")
        if not code:
            raise HTTPException(404, f"project_code missing in config.json for project '{project}'")

        return {"project": project, "project_code": code}
    except HTTPException:
        raise
    except Exception as e:
        debug("get_project_code error:", e)
        raise HTTPException(500, f"Failed to read project_code: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# Section M: Superuser-only membership mutate API (logins.db)
#   • Decouples project membership from roles.json entirely.
#   • Adds/Removes rows in accounts_projects.
# ──────────────────────────────────────────────────────────────────────────────
async def _ensure_project(lsession: AsyncSession, project_name: str) -> LProject:
    row = (await lsession.execute(
        select(LProject).where(LProject.name == project_name)
    )).scalars().first()
    if row:
        return row
    # Auto-create project record if missing
    row = LProject(id=_id(), name=project_name)
    lsession.add(row)
    await lsession.commit()
    return row

@router.post(
    "/memberships/update",
    response_model=MembershipsOut,
    dependencies=[Depends(require_superuser)],
)
async def memberships_update(
    body: MembershipUpdateIn = Body(...),
    lsession: AsyncSession = Depends(logins_session_dep),
):
    debug("POST /memberships/update", body.model_dump())
    # Validate user exists
    user = await lsession.get(LUser, body.user_id)
    if not user:
        raise HTTPException(404, "User not found")

    proj = await _ensure_project(lsession, body.project)

    if body.op == "add":
        # Upsert link
        link = (await lsession.execute(
            select(AccountProject).where(
                (AccountProject.user_id == user.id) & (AccountProject.project_id == proj.id)
            )
        )).scalars().first()
        if link:
            link.role_name = body.role_name
        else:
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc)

            lsession.add(AccountProject(
                id=_id(),
                user_id=user.id,
                project_id=proj.id,
                role_name=body.role_name,
                created_at=now,
                updated_at=now
            ))
        await lsession.commit()
    elif body.op == "remove":
        await lsession.execute(
            AccountProject.__table__.delete().where(
                (AccountProject.user_id == user.id) & (AccountProject.project_id == proj.id)
            )
        )
        await lsession.commit()
    else:
        raise HTTPException(400, "op must be 'add' or 'remove'")

    # Return updated memberships for this user
    rows = (await lsession.execute(
        select(LProject.name, AccountProject.role_name)
        .join(AccountProject, AccountProject.project_id == LProject.id)
        .where(AccountProject.user_id == user.id)
    )).all()
    memberships = [{"project": r[0], "role_name": r[1]} for r in rows]
    debug(" memberships updated:", memberships)
    return MembershipsOut(user_id=user.id, memberships=memberships)

@router.get(
    "/memberships/{user_id}",
    response_model=MembershipsOut,
    dependencies=[Depends(require_superuser)],
)
async def memberships_list(user_id: str, lsession: AsyncSession = Depends(logins_session_dep)):
    debug("GET /memberships", user_id)
    user = await lsession.get(LUser, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    rows = (await lsession.execute(
        select(LProject.name, AccountProject.role_name)
        .join(AccountProject, AccountProject.project_id == LProject.id)
        .where(AccountProject.user_id == user.id)
    )).all()
    memberships = [{"project": r[0], "role_name": r[1]} for r in rows]
    return MembershipsOut(user_id=user.id, memberships=memberships)

# ──────────────────────────────────────────────────────────────────────────────
# Section N: Current user info (bridge to /auth/me)
# ──────────────────────────────────────────────────────────────────────────────
@router.get("/{project}/me")
async def account_roles_me(request: Request, project: str):
    """
    Returns the current logged-in user as seen by /login/{project}/auth/me.
    Includes: user object, roles, overrides, feature_tags, projects.
    """
    # fetch canonical /auth/me
    me = await _fetch_auth_me(request, project)
    user = (me.get("user") or {}) if isinstance(me, dict) else {}
    if not user:
        raise HTTPException(401, "Not authenticated")

    # Collect effective roles/overrides/feature_tags from nodes.db + logins.db
    try:
        # session helpers
        async for session in _get_session_local(project):
            async for lsession in logins_session_dep():
                # Grab effective permissions
                eff = await user_effective_permissions(project, user["id"], session, lsession)
                return {
                    "user": user,
                    "roles": eff["roles"],
                    "scopes": eff["scopes"],
                    "feature_tags": eff["feature_tags"],
                    "projects": eff["projects"],
                }
    except Exception as e:
        debug("account_roles_me error:", repr(e))
        raise HTTPException(500, f"Failed to resolve /me: {e}")
