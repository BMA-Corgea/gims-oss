# nodes/star_spirits_state_node.py
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from core.errors import AppError

# Orchestration glue (matches your core classes)
from core.orchestration.node import Node, NodeKind
from core.orchestration.module import Module

# Project helpers (your code)
from api.manifest.resolver import resolve_path           # uses local_layout_map.json
from api.i_o import load_local_layout_map                # to locate project root

# SQLAlchemy async
from sqlalchemy import String, Boolean, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ──────────────────────────────────────────────────────────────────────────────
# Debug (toggle here)
# ──────────────────────────────────────────────────────────────────────────────

from utils.logger import get_logger
log = get_logger(__name__)
DEBUG_ENABLED = log.is_debug()

# ──────────────────────────────────────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/star-spirits", tags=["Star Spirits – State"])
log.debug("router:init", {"prefix": "/star-spirits"})

def _api_dir() -> Path:
    p = Path(__file__).resolve().parents[1] / "api"
    log.debug("paths:api_dir", {"path": p.as_posix()})
    return p

def _repo_root() -> Path:
    from utils.paths import repo_root
    p = repo_root()
    log.debug("paths:repo_root", {"path": p.as_posix()})
    return p

def _projects_root() -> Path:
    layout = load_local_layout_map(_api_dir())
    root = _repo_root() / layout.get("project_root", "projects")
    log.debug("paths:projects_root", {"path": root.as_posix(), "layout_key": layout.get("project_root")})
    return root

def _project_path(project: str) -> Path:
    p = _projects_root() / project
    log.debug("paths:project_path", {"project": project, "path": p.as_posix()})
    return p

# ──────────────────────────────────────────────────────────────────────────────
# DB: nodes_db per project
# ──────────────────────────────────────────────────────────────────────────────

_engine_cache: dict[str, Any] = {}
_session_cache: dict[str, async_sessionmaker[AsyncSession]] = {}

def _engine_for(project: str):
    if project in _engine_cache:
        log.debug("db:engine:cache-hit", {"project": project})
        return _engine_cache[project]
    proj = _project_path(project)
    db_path = resolve_path(proj, "nodes_db")  # from local_layout_map.json
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}", future=True)
    log.debug("db:engine:new", {"project": project, "db": db_path.as_posix()})

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_conn, _conn_rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.execute("PRAGMA foreign_keys=ON;")
        cur.close()
        # sync-engine listener: avoid printing per-connection spam

    _engine_cache[project] = engine
    return engine

def _sessionmaker_for(project: str) -> async_sessionmaker[AsyncSession]:
    if project in _session_cache:
        log.debug("db:sessionmaker:cache-hit", {"project": project})
        return _session_cache[project]
    sm = async_sessionmaker(bind=_engine_for(project), expire_on_commit=False)
    _session_cache[project] = sm
    log.debug("db:sessionmaker:new", {"project": project})
    return sm

class Base(DeclarativeBase):
    pass

class StarSpiritsProgress(Base):
    """
    One row per user in a given project.
    NOTE: Use TEXT for user_id and do NOT FK to users to avoid type-mismatch headaches.
    """
    __tablename__ = "star_spirits_progress"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)  # dashed UUID string from /auth/me
    s1: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    s2: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    s3: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    s4: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    s5: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    s6: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    s7: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, default=lambda: datetime.utcnow().isoformat() + "Z")

async def _ensure_schema(project: str) -> None:
    log.debug("db:schema:ensure:start", {"project": project})
    async with _engine_for(project).begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.debug("db:schema:ensure:done", {"project": project})

async def _get_session(project: str) -> AsyncIterator[AsyncSession]:
    await _ensure_schema(project)
    sm = _sessionmaker_for(project)
    async with sm() as session:
        log.debug("db:session:open", {"project": project})
        try:
            yield session
        finally:
            log.debug("db:session:close", {"project": project})

# ──────────────────────────────────────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────────────────────────────────────

class ProgressOut(BaseModel):
    user_id: str
    s1: bool = False
    s2: bool = False
    s3: bool = False
    s4: bool = False
    s5: bool = False
    s6: bool = False
    s7: bool = False
    updated_at: str

class CollectIn(BaseModel):
    user_id: str = Field(..., description="Dashed UUID string from /login/{project}/auth/me")
    spirit: str = Field(..., description="One of s1..s7 or numeric '1'..'7'")

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _norm_spirit(spirit: str) -> str:
    s = str(spirit).strip().lower()
    log.debug("helpers:norm-spirit:in", {"raw": spirit, "norm": s})
    if s in {"1","2","3","4","5","6","7"}:
        out = f"s{s}"
        log.debug("helpers:norm-spirit:ok:numeric", {"out": out})
        return out
    if s in {"s1","s2","s3","s4","s5","s6","s7"}:
        log.debug("helpers:norm-spirit:ok:prefixed", {"out": s})
        return s
    log.debug("helpers:norm-spirit:error", {"raw": spirit})
    raise AppError(
        "INVALID_SPIRIT",
        "spirit must be one of s1..s7 or 1..7",
        status=400,
        details={"spirit": spirit},
    )

async def _get_or_create(session: AsyncSession, user_id: str) -> StarSpiritsProgress:
    log.debug("db:get-or-create:start", {"user_id": user_id})
    rec = await session.get(StarSpiritsProgress, user_id)
    if rec:
        log.debug("db:get-or-create:hit", {"user_id": user_id})
        return rec
    rec = StarSpiritsProgress(user_id=user_id)
    session.add(rec)
    await session.commit()
    await session.refresh(rec)
    log.debug("db:get-or-create:new", {"user_id": user_id})
    return rec

def _to_out(rec: StarSpiritsProgress) -> ProgressOut:
    out = ProgressOut.model_validate({
        "user_id": rec.user_id,
        "s1": rec.s1, "s2": rec.s2, "s3": rec.s3, "s4": rec.s4, "s5": rec.s5, "s6": rec.s6, "s7": rec.s7,
        "updated_at": rec.updated_at
    })
    log.debug("helpers:to-out", {"user_id": out.user_id, "flags": [out.s1,out.s2,out.s3,out.s4,out.s5,out.s6,out.s7]})
    return out

# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────

project_router = APIRouter(prefix="/{project}")
log.debug("router:project:init", {"prefix": "/{project}"})

@project_router.get("/progress", response_model=ProgressOut)
async def get_progress(project: str, user_id: str = Query(..., description="Dashed UUID from /auth/me")):
    log.debug("route:GET /progress:begin", {"project": project, "user_id": user_id})
    async for session in _get_session(project):
        rec = await _get_or_create(session, user_id)
        out = _to_out(rec)
        log.debug("route:GET /progress:end", {"project": project, "user_id": user_id})
        return out

@project_router.post("/collect", response_model=ProgressOut)
async def collect_spirit(project: str, payload: CollectIn):
    log.debug("route:POST /collect:begin", {"project": project, "payload": payload.model_dump()})
    spirit = _norm_spirit(payload.spirit)
    async for session in _get_session(project):
        rec = await _get_or_create(session, payload.user_id)
        setattr(rec, spirit, True)
        rec.updated_at = datetime.utcnow().isoformat() + "Z"
        await session.commit()
        await session.refresh(rec)
        out = _to_out(rec)
        log.debug("route:POST /collect:commit", {"project": project, "user_id": payload.user_id, "spirit": spirit})
        return out

@project_router.post("/reset", response_model=ProgressOut)
async def reset_progress(project: str, user_id: str = Query(...)):
    log.debug("route:POST /reset:begin", {"project": project, "user_id": user_id})
    async for session in _get_session(project):
        rec = await _get_or_create(session, user_id)
        for i in range(1,8):
            setattr(rec, f"s{i}", False)
        rec.updated_at = datetime.utcnow().isoformat() + "Z"
        await session.commit()
        await session.refresh(rec)
        out = _to_out(rec)
        log.debug("route:POST /reset:commit", {"project": project, "user_id": user_id})
        return out

router.include_router(project_router)
log.debug("router:include", {"mounted": "/star-spirits/{project}/..."})

# ──────────────────────────────────────────────────────────────────────────────
# Node + Module
# ──────────────────────────────────────────────────────────────────────────────

star_state_node = Node(
    name="Star Spirits – State",
    kind=NodeKind.STATE,
    router=router,
    meta={
        "icon": "🟊",
        "label": "Star Spirits State",
    },
)
log.debug("node:created", {"name": star_state_node.name, "kind": str(star_state_node.kind)})

state_module = Module(
    name="Star Spirits State",
    nodes=[star_state_node],
    version="0.1.0",
    description="Tracks collection of 7 star spirits per user in nodes.db",
    roles=set(),
)
log.debug("module:created", {"name": state_module.name, "version": state_module.version})
