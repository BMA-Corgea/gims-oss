# core/core_archive.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union
from datetime import datetime, timedelta

# Debug control - set to False to disable all backend debug logging
from utils.logger import get_logger
log = get_logger(__name__)
DEBUG_ENABLED = log.is_debug()

# -----------------------------------------------------------------------------
# Types: "Plan steps" are declarative intents. The API layer can execute them.
# -----------------------------------------------------------------------------

@dataclass
class EnsureSoftColumns:
    """Intention: make sure {archived:int, archived_at:text} exist on table."""
    target: str              # "hot" DB alias
    table: str

@dataclass
class EnsureArchiveTable:
    """
    Intention: ensure archive table exists with given columns
    (mirror of hot), plus meta cols.
    """
    source_target: str       # "hot"
    source_table: str
    dest_target: str         # "archive"
    dest_table: str
    columns: List[Tuple[str, str]]  # [(name, decl_type)]
    include_meta: bool = True        # add archived_from_table, archived_at, archive_strategy

@dataclass
class SQLStep:
    """A parameterized SQL statement to run on a DB alias."""
    target: str              # "hot" | "archive"
    sql: str
    params: Tuple[Any, ...] = field(default_factory=tuple)

@dataclass
class FileOp:
    """
    Declarative FS op for run folders:
      - op: "move", "write_text", "delete", "mkdir_p"
    """
    op: str
    src: Optional[str] = None
    dst: Optional[str] = None
    text: Optional[str] = None

PlanStep = Union[EnsureSoftColumns, EnsureArchiveTable, SQLStep, FileOp]

@dataclass
class Plan:
    description: str
    steps: List[PlanStep] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

# -----------------------------------------------------------------------------
# Helpers (pure logic)
# -----------------------------------------------------------------------------

def _now_iso() -> str:
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    log.debug("[_now_iso] ->", ts)
    return ts

def effective_policy(policy: Dict[str, Any], kind: str, name: str) -> Dict[str, Any]:
    """
    Merge default + specific policy block.
    kind: "nouns" | "verbs"
    """
    log.debug("[effective_policy] start kind=", kind, "name=", name)
    default = policy.get("default", {}) if isinstance(policy, dict) else {}
    block = (policy.get(kind) or {}).get(name, {}) if isinstance(policy, dict) else {}
    merged = dict(default)
    merged.update(block)
    log.debug("[effective_policy] merged ->", merged)
    return merged

def intersect_columns(
    hot_cols: Sequence[Tuple[str, str]],
    arc_cols: Optional[Sequence[Tuple[str, str]]] = None
) -> List[str]:
    """
    Compute common column names, preserving hot order. If arc_cols is None,
    assume all hot columns are accepted.
    """
    log.debug("[intersect_columns] hot_cols=", hot_cols, "arc_cols=", arc_cols)
    hot_names = [c[0] for c in hot_cols]
    if arc_cols is None:
        log.debug("[intersect_columns] archive columns unknown; using hot order")
        return hot_names
    arc_names = {c[0] for c in arc_cols}
    common = [c for c in hot_names if c in arc_names]
    log.debug("[intersect_columns] common ->", common)
    return common

def select_eligible_by_count(
    total_count: int,
    max_items: int,
    ordered_oldest_ids: Sequence[str]
) -> List[str]:
    """
    If total_count > max_items, return oldest surplus IDs based on the provided ordering.
    """
    log.debug("[select_eligible_by_count] total=", total_count, "max=", max_items)
    if total_count <= max_items:
        log.debug("[select_eligible_by_count] no overflow")
        return []
    surplus = total_count - max_items
    chosen = list(ordered_oldest_ids[:surplus])
    log.debug("[select_eligible_by_count] surplus=", surplus, "chosen=", chosen)
    return chosen

def select_eligible_by_age(
    rows_for_age_eval: Sequence[Dict[str, Any]],
    id_field: str,
    date_field: str,
    archive_after_days: int,
    *,
    parse_formats: Sequence[str] = ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m%d%Y", "%Y%m%d", "%Y-%m-%dT%H:%M:%SZ")
) -> List[str]:
    """
    Determine which IDs are older than X days using provided rows. Caller
    feeds minimal projections: [{id_field:.., date_field:..}, ...]
    """
    log.debug("[select_eligible_by_age] rows=", len(rows_for_age_eval), "id_field=", id_field, "date_field=", date_field, "days=", archive_after_days)
    cutoff = datetime.utcnow() - timedelta(days=int(archive_after_days))
    out: List[str] = []
    for r in rows_for_age_eval:
        raw = r.get(date_field)
        pid = r.get(id_field)
        if raw in (None, "") or pid in (None, ""):
            continue
        s = str(raw)
        parsed = None
        for fmt in parse_formats:
            try:
                parsed = datetime.strptime(s, fmt)
                break
            except Exception:
                continue
        if parsed and parsed < cutoff:
            out.append(pid)
    log.debug("[select_eligible_by_age] chosen ->", out)
    return out

# -----------------------------------------------------------------------------
# Noun instance archiving (plan generation only)
# -----------------------------------------------------------------------------

def plan_soft_archive_nouns(
    noun_type: str,
    table: str,
    primary_field: str,
    primary_ids: Iterable[str],
    *,
    index_table: str = "noun_archive_index",
    notes: str = ""
) -> Plan:
    log.debug("[plan_soft_archive_nouns] noun=", noun_type, "table=", table, "pf=", primary_field, "ids=", list(primary_ids))
    plan = Plan(description=f"Soft archive {noun_type}", meta={"noun_type": noun_type, "strategy": "soft"})
    plan.steps.append(EnsureSoftColumns(target="hot", table=table))
    ids = list(primary_ids)
    if not ids:
        log.debug("[plan_soft_archive_nouns] no IDs; returning empty plan")
        return plan

    placeholders = ",".join(["?"] * len(ids))
    sql = f'UPDATE "{table}" SET archived=1, archived_at=? WHERE "{primary_field}" IN ({placeholders})'
    params = ( _now_iso(), *ids )
    log.debug("[plan_soft_archive_nouns] SQL:", sql, "params_len=", len(params))
    plan.steps.append(SQLStep(target="hot", sql=sql, params=params))

    # index inserts (one per id)
    for pid in ids:
        step = SQLStep(
            target="archive",
            sql=f'INSERT INTO {index_table}(noun_type, primary_id, table_name, archived_at, strategy, notes) VALUES (?,?,?,?,?,?)',
            params=(noun_type, pid, table, _now_iso(), "soft", notes)
        )
        log.debug("[plan_soft_archive_nouns] index insert for", pid)
        plan.steps.append(step)

    return plan

def plan_hard_archive_nouns(
    noun_type: str,
    src_table: str,
    dst_table: Optional[str],
    primary_field: str,
    primary_ids: Iterable[str],
    *,
    hot_columns: Sequence[Tuple[str, str]],            # [(name, decl_type)]
    archive_columns: Optional[Sequence[Tuple[str, str]]] = None,
    index_table: str = "noun_archive_index",
    notes: str = ""
) -> Plan:
    dst_table = dst_table or src_table
    log.debug("[plan_hard_archive_nouns] noun=", noun_type, "src_table=", src_table, "dst_table=", dst_table, "pf=", primary_field)
    plan = Plan(description=f"Hard archive {noun_type}", meta={"noun_type": noun_type, "strategy": "hard"})
    ids = list(primary_ids)
    log.debug("[plan_hard_archive_nouns] ids=", ids)

    # Ensure archive table mirror exists (logic-only intent)
    plan.steps.append(EnsureArchiveTable(
        source_target="hot",
        source_table=src_table,
        dest_target="archive",
        dest_table=dst_table,
        columns=list(hot_columns),
        include_meta=True
    ))
    _common_cols = intersect_columns(hot_columns, archive_columns)
    # Determine columns to use: Prioritize hot columns if archive doesn't exist/is empty
    if archive_columns:
        # Archive table exists, find common columns preserving hot order
        cols_to_use = intersect_columns(hot_columns, archive_columns)
        log.debug("[plan_hard_archive_nouns] Archive table exists, using common columns:", cols_to_use)
    else:
        # Archive table likely doesn't exist yet, use all hot columns
        cols_to_use = [c[0] for c in hot_columns]
        log.debug("[plan_hard_archive_nouns] Archive table likely new, using all hot columns:", cols_to_use)

    if not cols_to_use:
        log.debug("[plan_hard_archive_nouns] X No columns identified to copy; returning plan with only EnsureArchiveTable")
        # This case should ideally not happen if hot_columns is valid
        return plan

    col_csv = ", ".join(f'"{c}"' for c in cols_to_use)
    placeholders = ", ".join(["?"] * len(cols_to_use))
    sel_sql = f'SELECT {col_csv} FROM "{src_table}" WHERE "{primary_field}"=?'
    # Always include meta columns in the INSERT for hard archive
    meta_cols_csv = ", archived_from_table, archived_at_meta, archive_strategy"
    meta_placeholders = ", ?, ?, ?"
    ins_sql = f'INSERT INTO "{dst_table}" ({col_csv}{meta_cols_csv}) VALUES ({placeholders}{meta_placeholders})'
    del_sql = f'DELETE FROM "{src_table}" WHERE "{primary_field}"=?'

    for pid in ids:
        log.debug("[plan_hard_archive_nouns] steps for pid=", pid)
        # 1) select row subset from hot
        plan.steps.append(SQLStep(target="hot", sql=sel_sql, params=(pid,)))
        # 2) insert into archive (using placeholders for selected columns + meta values)
        plan.steps.append(SQLStep(
            target="archive",
            sql=ins_sql,
            params=tuple([f"<{c}>" for c in cols_to_use] + [src_table, _now_iso(), "hard"]) # Executor binds <col> values
        ))
        # 3) delete from hot
        plan.steps.append(SQLStep(target="hot", sql=del_sql, params=(pid,)))
        # 4) index row (using src_table which matches the original hot table name)
        plan.steps.append(SQLStep(
            target="archive",
            sql=f'INSERT INTO {index_table}(noun_type, primary_id, table_name, archived_at, strategy, notes) VALUES (?,?,?,?,?,?)',
            params=(noun_type, pid, src_table, _now_iso(), "hard", notes)
        ))

    return plan

def plan_restore_nouns_soft(
    noun_type: str,
    table: str,
    primary_field: str,
    primary_ids: Iterable[str]
) -> Plan:
    log.debug("[plan_restore_nouns_soft] noun=", noun_type, "table=", table, "pf=", primary_field, "ids=", list(primary_ids))
    plan = Plan(description=f"Soft-restore {noun_type}", meta={"noun_type": noun_type, "mode": "soft"})
    ids = list(primary_ids)
    if not ids:
        log.debug("[plan_restore_nouns_soft] no IDs; empty plan")
        return plan
    placeholders = ",".join(["?"] * len(ids))
    sql = f'UPDATE "{table}" SET archived=0, archived_at=NULL WHERE "{primary_field}" IN ({placeholders})'
    log.debug("[plan_restore_nouns_soft] SQL:", sql)
    plan.steps.append(SQLStep(target="hot", sql=sql, params=tuple(ids)))
    return plan

def plan_restore_nouns_hard(
    noun_type: str,
    src_table: str,  # archive table
    dst_table: str,  # hot table
    primary_field: str,
    primary_ids: Iterable[str],
    *,
    hot_columns: Sequence[Tuple[str, str]],
    archive_columns: Optional[Sequence[Tuple[str, str]]] = None
) -> Plan:
    log.debug("[plan_restore_nouns_hard] noun=", noun_type, "src(archive)=", src_table, "dst(hot)=", dst_table, "pf=", primary_field)
    plan = Plan(description=f"Hard-restore {noun_type}", meta={"noun_type": noun_type, "mode": "hard"})
    ids = list(primary_ids)
    common = intersect_columns(hot_columns, archive_columns)
    if not common:
        log.debug("[plan_restore_nouns_hard] X No common columns; returning empty plan")
        return plan

    col_csv = ", ".join(f'"{c}"' for c in common)
    placeholders = ", ".join(["?"] * len(common))
    sel_sql = f'SELECT {col_csv} FROM "{src_table}" WHERE "{primary_field}"=?'
    ins_sql = f'INSERT OR REPLACE INTO "{dst_table}" ({col_csv}) VALUES ({placeholders})'
    del_sql = f'DELETE FROM "{src_table}" WHERE "{primary_field}"=?'

    for pid in ids:
        log.debug("[plan_restore_nouns_hard] steps for pid=", pid)
        plan.steps.append(SQLStep(target="archive", sql=sel_sql, params=(pid,)))
        plan.steps.append(SQLStep(
            target="hot",
            sql=ins_sql,
            params=tuple(f"<{c}>" for c in common)  # executor binds from previous SELECT
        ))
        plan.steps.append(SQLStep(target="archive", sql=del_sql, params=(pid,)))

    return plan

# -----------------------------------------------------------------------------
# Run archiving (plan generation only)
# -----------------------------------------------------------------------------

def plan_archive_runs_hard(
    items: Sequence[Dict[str, str]],
    *,
    index_table: str = "runs_archive_index",
    notes: str = ""
) -> Plan:
    """
    items: [{run_id, test_type, verb_group, src_dir, dst_dir}, ...]
    Hard = move src_dir -> dst_dir and index it.
    """
    log.debug("[plan_archive_runs_hard] items=", len(items))
    plan = Plan(description="Hard archive runs", meta={"strategy": "hard"})
    for it in items:
        rid = it["run_id"]; verb = it["test_type"]; vg = it["verb_group"]
        src = it["src_dir"]; dst = it["dst_dir"]
        log.debug("[plan_archive_runs_hard] rid=", rid, "vg=", vg, "src=", src, "dst=", dst)
        # FS move
        plan.steps.append(FileOp(op="mkdir_p", dst=str(dst).rsplit("/", 1)[0]))
        plan.steps.append(FileOp(op="move", src=src, dst=dst))
        # Index row
        plan.steps.append(SQLStep(
            target="archive",
            sql=f'INSERT INTO {index_table}(run_id, verb, verb_group, archive_path, archived_at, strategy, notes) VALUES (?,?,?,?,?,?,?)',
            params=(rid, verb, vg, dst, _now_iso(), "hard", notes)
        ))
    return plan

def plan_archive_runs_soft(
    items: Sequence[Dict[str, str]],
    *,
    index_table: str = "runs_archive_index",
    notes: str = ""
) -> Plan:
    """
    Soft = write a '.archived' marker into the run folder (logic intent),
    and index it.
    """
    log.debug("[plan_archive_runs_soft] items=", len(items))
    plan = Plan(description="Soft archive runs", meta={"strategy": "soft"})
    for it in items:
        rid = it["run_id"]; verb = it["test_type"]; vg = it["verb_group"]
        src = it["src_dir"]
        marker = f"{src}/.archived"
        log.debug("[plan_archive_runs_soft] rid=", rid, "marker=", marker)
        plan.steps.append(FileOp(op="write_text", dst=marker, text=_now_iso()))
        plan.steps.append(SQLStep(
            target="archive",
            sql=f'INSERT INTO {index_table}(run_id, verb, verb_group, archive_path, archived_at, strategy, notes) VALUES (?,?,?,?,?,?,?)',
            params=(rid, verb, vg, src, _now_iso(), "soft", notes)
        ))
    return plan

def plan_restore_runs(
    items: Sequence[Dict[str, str]]
) -> Plan:
    """
    Try hard-restore first (move from archive dir back to hot).
    If not present, remove soft marker.
    Caller decides which case per run and feeds appropriate paths.
    items: [{run_id, verb_group, arc_dir, hot_dir, has_hard: bool, has_soft: bool}, ...]
    """
    log.debug("[plan_restore_runs] items=", len(items))
    plan = Plan(description="Restore runs", meta={})
    for it in items:
        rid = it["run_id"]; _vg = it.get("verb_group")
        has_hard = bool(it.get("has_hard"))
        has_soft = bool(it.get("has_soft"))
        arc_dir = it.get("arc_dir"); hot_dir = it.get("hot_dir")
        log.debug("[plan_restore_runs] rid=", rid, "hard=", has_hard, "soft=", has_soft)
        if has_hard and arc_dir and hot_dir:
            plan.steps.append(FileOp(op="mkdir_p", dst=str(hot_dir).rsplit("/", 1)[0]))
            plan.steps.append(FileOp(op="move", src=arc_dir, dst=hot_dir))
        elif has_soft and hot_dir:
            marker = f"{hot_dir}/.archived"
            plan.steps.append(FileOp(op="delete", src=marker))
        else:
            log.debug("[plan_restore_runs] no-op for rid=", rid)
    return plan

# -----------------------------------------------------------------------------
# Policy runner (pure logic)
# -----------------------------------------------------------------------------

def plan_apply_archive_policy_for_nouns(
    policy: Dict[str, Any],
    noun_tables: Dict[str, Dict[str, Any]],
    # noun_tables entry example:
    #   {
    #     "table": "noun_Sample",
    #     "primary_field": "sample_id",
    #     "total_count": 123456,
    #     "ordered_oldest_ids": [...],             # for count trimming
    #     "date_field": "received_date",           # optional
    #     "rows_for_age_eval": [                   # optional, for age trimming
    #        {"sample_id":"LOL...", "received_date":"2024-01-01"}, ...
    #     ],
    #     "hot_columns": [("sample_id","TEXT"), ...],
    #     "archive_columns": [("sample_id","TEXT"), ...]  # optional
    #   }
) -> Dict[str, Dict[str, Any]]:
    """
    Returns a map of noun_type -> {eligible_ids, strategy, plan}
    Does not execute anything.
    """
    log.debug("[plan_apply_archive_policy_for_nouns] start with", len(noun_tables), "noun types")
    out: Dict[str, Dict[str, Any]] = {}
    for noun_type, info in noun_tables.items():
        log.debug("\n[plan_apply_archive_policy_for_nouns] noun=", noun_type)
        pol = effective_policy(policy, "nouns", noun_type)
        strategy = pol.get("strategy", "soft")
        max_items = pol.get("max_items")
        after_days = pol.get("archive_after_days")
        table = info["table"]; pf = info["primary_field"]
        hot_cols = info.get("hot_columns") or []
        arc_cols = info.get("archive_columns")
        eligible: List[str] = []

        # Count-based
        if isinstance(max_items, int):
            log.debug("[policy] count-based max_items=", max_items)
            total_count = int(info.get("total_count") or 0)
            ordered_ids = info.get("ordered_oldest_ids") or []
            chosen = select_eligible_by_count(total_count, max_items, ordered_ids)
            eligible.extend(chosen)

        # Age-based
        if isinstance(after_days, int):
            log.debug("[policy] age-based after_days=", after_days)
            date_field = pol.get("date_field") or info.get("date_field")
            rows = info.get("rows_for_age_eval") or []
            if date_field:
                chosen = select_eligible_by_age(rows, pf, date_field, after_days)
                eligible.extend(chosen)
            else:
                log.debug("[policy] no date_field available; skipping age rule")

        # Deduplicate preserve order
        seen = set()
        eligible = [x for x in eligible if not (x in seen or seen.add(x))]
        log.debug("[plan_apply_archive_policy_for_nouns] eligible ids ->", eligible)

        # Build plan (per strategy)
        if strategy == "soft":
            plan = plan_soft_archive_nouns(noun_type, table, pf, eligible)
        elif strategy == "hard":
            plan = plan_hard_archive_nouns(
                noun_type, table, table, pf, eligible,
                hot_columns=hot_cols, archive_columns=arc_cols
            )
        else:
            log.debug("[plan_apply_archive_policy_for_nouns] unknown strategy:", strategy)
            plan = Plan(description=f"Unknown strategy for {noun_type}", meta={"strategy": strategy})

        out[noun_type] = {
            "strategy": strategy,
            "eligible_ids": eligible,
            "plan": plan
        }

    log.debug("\n[plan_apply_archive_policy_for_nouns] done")
    return out
