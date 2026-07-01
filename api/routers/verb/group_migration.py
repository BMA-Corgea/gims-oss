# api/routers/verb/group_migration.py
#
# Verb-group migration: move/merge data-dump folders (local & S3), the SQL-
# native row migration orchestrator, and the group scaffold guarantee.
# Moved VERBATIM from api/routers/verb.py (no logic changes).

from pathlib import Path
from typing import Any, Dict
import shutil

from api.i_o import get_verb_group_log_config, save_json
from api.manifest.resolver import resolve_path

from ._log import log
from ._compat import ensure_prefix, touch, _s3_is_path, _s3_copy_prefix
from ._db import _select_run_ids_for_group, _update_rows_change_group


# ─────────────────────────────────────────────────────────────
# Data-dump folder migration (local & S3)
# ─────────────────────────────────────────────────────────────
def _move_data_dump_folder(proj: Path, old_group: str, new_group: str, run_id: str) -> Dict[str, Any]:
    """
    Move/merge the data dump folder named after run_id from old_group to new_group.
    """
    src = resolve_path(proj, "data_dump_dir", verb_group=old_group, run_id=str(run_id))
    dst_parent = resolve_path(proj, "data_dump_entry", verb_group=new_group)
    dst = dst_parent / str(run_id)

    # S3 path? Use prefix copy.
    if _s3_is_path(str(src)) or _s3_is_path(str(dst_parent)):
        ensure_prefix(dst_parent)
        info = _s3_copy_prefix(src, dst, delete_src=True)
        log.debug("[_move_data_dump_folder][s3]", {"src": str(src), "dst": str(dst), **info})
        # In S3 we don't "exist()" check; report best-effort stats
        return {"run_id": run_id, "src_exists": True, "dst_exists": True, "moved": info.get("copied", 0) > 0, "merged": False, "s3": True}

    # Local filesystem branch
    ensure_prefix(dst_parent)

    if not src.exists():
        log.debug("[_move_data_dump_folder] source missing", {"src": str(src)})
        return {"run_id": run_id, "src_exists": False, "dst_exists": dst.exists(), "moved": False, "merged": False}

    moved = False
    merged = False

    if not dst.exists():
        shutil.move(str(src), str(dst))
        moved = True
        log.debug("[_move_data_dump_folder] moved", {"src": str(src), "dst": str(dst)})
    else:
        for item in src.iterdir():
            s = item
            d = dst / item.name
            if item.is_dir():
                shutil.copytree(str(s), str(d), dirs_exist_ok=True)
            else:
                ensure_prefix(d.parent)
                shutil.copy2(str(s), str(d))
        shutil.rmtree(str(src))
        merged = True
        log.debug("[_move_data_dump_folder] merged", {"src": str(src), "dst": str(dst)})

    return {"run_id": run_id, "src_exists": True, "dst_exists": True, "moved": moved, "merged": merged}

def _migrate_group_sql_and_dumps(proj: Path, verb_name: str, old_group: str, new_group: str) -> Dict[str, Any]:
    """
    SQL-native migration when a verb changes groups:
      1) Gather run_ids for rows where (verb=verb_name AND verb_group=old_group)
      2) UPDATE those rows to set verb_group=new_group
      3) Move corresponding data_dump folders by run_id from old_group to new_group
    Returns a summary dict.
    """
    if not old_group or not new_group or old_group == new_group:
        return {"updated": 0, "run_ids": [], "data_dumps": {"moved": 0, "merged": 0, "missing": 0}}

    # ensure destination scaffold exists (S3 aware)
    _ensure_group_scaffold(proj, new_group)

    run_ids = _select_run_ids_for_group(proj, verb_name, old_group)
    log.debug("[_migrate_group_sql_and_dumps] candidates", {"count": len(run_ids)})

    updated_rows = _update_rows_change_group(proj, verb_name, old_group, new_group)

    # Move data-dump folders for those run_ids
    dd_moved = 0
    dd_merged = 0
    dd_missing = 0
    for pid in run_ids:
        res = _move_data_dump_folder(proj, old_group, new_group, pid)
        if not res.get("src_exists"):
            dd_missing += 1
        elif res.get("merged"):
            dd_merged += 1
        elif res.get("moved"):
            dd_moved += 1

    return {
        "updated": updated_rows,
        "run_ids": run_ids,
        "data_dumps": {"moved": dd_moved, "merged": dd_merged, "missing": dd_missing},
    }


def _ensure_group_scaffold(proj: Path, group_name: str) -> None:
    """
    Keep the folder/config scaffold for UI compatibility (data_dumps + log_config).
    Persistence of run rows is SQL-only; JSONL is not used.
    S3-aware: uses ensure_prefix/touch so the console shows the structure.
    """
    log_file = resolve_path(proj, "verb_group_log", verb_group=group_name)  # legacy .jsonl (empty)
    cfg_path = resolve_path(proj, "verb_group_log_config", verb_group=group_name)
    dumps_root = resolve_path(proj, "data_dump_entry", verb_group=group_name)

    ensure_prefix(log_file.parent)
    ensure_prefix(dumps_root)

    # Always guarantee a zero-byte object for the legacy log file (or a local file)
    touch(log_file)
    log.debug("[_ensure_group_scaffold] touched legacy log file", {"log_file": str(log_file)})

    # Ensure a config exists (save_json is S3-aware via i_o/json_proxy)
    need_default = False
    if not _s3_is_path(str(cfg_path)) and not cfg_path.exists():
        need_default = True
    else:
        try:
            _ = get_verb_group_log_config(proj, group_name)
        except FileNotFoundError:
            need_default = True

    if need_default:
        default_schema = {
            "primary_id": "run_ID",
            "fields": {
                "run_ID": {"type": "string", "required": True},
                "date_tested": {"type": "date", "required": True},
                "test_type": {"type": "string", "required": True},
            }
        }
        save_json(cfg_path, default_schema)
        log.debug("[_ensure_group_scaffold] wrote default log config", {"config_path": str(cfg_path)})
