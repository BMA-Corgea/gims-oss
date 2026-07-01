# api/routers/backup/models.py
#
# Pydantic request/state models. Moved VERBATIM from the former single-file
# api/routers/backup.py (no logic changes).

from pydantic import BaseModel, Field, validator
from typing import Optional

from .paths import _new_id


# ──────────────────────────────────────────────────────────────────────────────
# Models
# ──────────────────────────────────────────────────────────────────────────────
class BackupNowRequest(BaseModel):
    project: str = Field(..., description="Project directory name under /projects")
    type: str = Field("hybrid", description="zip | sqlite | hybrid")
    paranoid: bool = Field(False, description="If true, also write per-file checksums.txt where applicable")
    notes: Optional[str] = None

    @validator("type")
    def _type_ok(cls, v):
        if v not in {"zip", "sqlite", "hybrid"}:
            raise ValueError("type must be one of: zip | sqlite | hybrid")
        return v

class RestoreRequest(BaseModel):
    project: str = Field(..., description="Original project name (for lookup)")
    mode: str = Field("clone", description="clone | inplace (inplace not yet implemented)")
    new_project: Optional[str] = Field(None, description="New project name for clone mode")
    scope: Optional[str] = Field(None, description="None | db_only | files_only")

class Schedule(BaseModel):
    id: str = Field(default_factory=lambda: _new_id("sch-"))
    project: str
    type: str = Field("hybrid", description="zip | sqlite | hybrid")
    frequency: str = Field(..., description="hourly | daily | weekly | monthly")
    # timing options
    minute: int = 0
    hour: int = 2
    dow: Optional[int] = None
    dom: Optional[int] = None
    retention_keep: Optional[int] = Field(10, description="Keep last N backups for this schedule")
    enabled: bool = True
    last_run_at: Optional[str] = None
    next_run_at: Optional[str] = None
    notes: Optional[str] = None

    @validator("frequency")
    def _freq_ok(cls, v):
        if v not in {"hourly", "daily", "weekly", "monthly"}:
            raise ValueError("frequency must be hourly|daily|weekly|monthly")
        return v

    @validator("dom")
    def _dom_ok(cls, v, values):
        if values.get("frequency") == "monthly":
            if v is None:
                return 1
            if v < 1 or v > 28:
                raise ValueError("dom must be in 1..28 for monthly")
        return v
